# -*- coding: utf-8 -*-
# 精煉廠測試檔 (v16.0 批次掃描版)
import os
# 確保 DuckDB 和其他數值計算庫在受限環境下不會因線程競爭導致效能下降
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
import sys
import unittest
import subprocess
import json
import tempfile
import shutil
import zipfile
import duckdb

# --- 路徑自我校正樣板碼 ---
try:
    current_pipeline_dir = os.path.dirname(os.path.abspath(__file__))
    apps_dir = os.path.dirname(current_pipeline_dir)
    project_root = os.path.dirname(apps_dir)

    if apps_dir not in sys.path:
        sys.path.insert(0, apps_dir)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
except Exception as e:
    print(f"路徑校正時發生錯誤: {e}", file=sys.stderr)
    pass
# --- 路徑自我校正樣板碼結束 ---

# from apps.taifex_data_pipeline.run import SimpleLogger, determine_parsing_recipe, HardwareManager # worker_process_file 已移除
from apps.taifex_data_pipeline.run import SimpleLogger, HardwareManager # determine_parsing_recipe 仍在 run.py 中，但測試可能不再直接調用它

class TestTaifexDataPipelineAsync(unittest.TestCase):
    """
    針對重構後的非同步串流版本 `run.py` 的端到端測試。
    主要測試 `run.py` 作為一個整體應用程式，通過命令列介面執行時，
    是否能正確處理輸入的樣本數據（ZIP 檔案和單獨的 CSV 檔案），
    並在 DuckDB 資料庫中生成預期的表和數據。
    """

    def setUp(self):
        """
        為每個測試案例設定初始環境。
        - 初始化一個測試專用的 logger (雖然對 subprocess 影響有限)。
        - 定義測試所需的檔案路徑 (run.py 腳本, 樣本 ZIP, 臨時 tick data CSV)。
        - 創建臨時目錄用於存放測試輸入、資料庫輸出和工作檔案。
        - 將樣本 ZIP 檔案複製到臨時輸入目錄。
        - 調用 `_create_sample_tick_data_csv` 創建包含有效和無效數據的 tick data 樣本 CSV，
          並將其複製到臨時輸入目錄，以供 `run.py` 處理。
        """
        # 初始化測試專用的 logger
        # 注意：這會覆蓋 run.py 中全域的 logger，僅在測試期間生效
        # global logger # logger is already global in run.py, this line is not needed here
        # To ensure test logger is used if run.py's logger is accessed by imported functions:
        # This is tricky because run.py initializes its own logger.
        # For subprocess calls, run.py will use its own logger.
        # For direct function calls from run.py (if any were tested this way), we'd need to patch run.py.logger
        self.logger = SimpleLogger(log_level="DEBUG")
        # If run.py's functions are imported and use its global logger, this won't override it unless patched.
        # However, the main test `test_pipeline_end_to_end` uses subprocess, so run.py's internal logger is used.

        self.current_pipeline_dir = os.path.dirname(os.path.abspath(__file__))
        self.pipeline_run_py = os.path.join(self.current_pipeline_dir, "run.py")
        self.sample_zip_file_path = os.path.join(self.current_pipeline_dir, "sample_pipeline_data.zip")
        self.tick_data_sample_csv_path = os.path.join(self.current_pipeline_dir, "tick_data_sample.csv") # 新增

        self.base_temp_dir = tempfile.mkdtemp(prefix="test_pipeline_async_") # 更新前綴
        self.temp_input_dir = os.path.join(self.base_temp_dir, "input")
        self.temp_db_output_dir = os.path.join(self.base_temp_dir, "db_output")
        self.temp_work_dir = os.path.join(self.base_temp_dir, "work_temp")

        os.makedirs(self.temp_input_dir, exist_ok=True)
        os.makedirs(self.temp_db_output_dir, exist_ok=True)
        os.makedirs(self.temp_work_dir, exist_ok=True)

        if not os.path.exists(self.sample_zip_file_path):
            self.fail(f"測試 ZIP 檔案 {self.sample_zip_file_path} 不存在。")

        shutil.copy(self.sample_zip_file_path, os.path.join(self.temp_input_dir, "sample_pipeline_data.zip"))

        # 準備並複製 tick_data_sample.csv
        self._create_sample_tick_data_csv(self.tick_data_sample_csv_path)
        if os.path.exists(self.tick_data_sample_csv_path): # 防禦性檢查
            shutil.copy(self.tick_data_sample_csv_path, os.path.join(self.temp_input_dir, "tick_data_sample.csv"))
        else:
            self.logger.warning(f"測試用的 tick_data_sample.csv 未能創建於 {self.tick_data_sample_csv_path}，tick data 相關測試可能不完整。")


    def _create_sample_tick_data_csv(self, file_path: str):
        """
        輔助函數：在指定的 `file_path` 創建一個包含混合（有效與無效）數據的
        `tick_data` 樣本 CSV 檔案。
        此檔案用於測試 `run.py` 對 tick data 的解析、轉換及髒數據處理能力。
        CSV 內容包含：
        - 多行有效的期貨/選擇權 tick 記錄。
        - 日期格式錯誤的記錄。
        - 成交量包含無效字符的記錄。
        - 價格為 "數據遺失" 的記錄。
        - 成交量為 "---" 的記錄。
        這些情況旨在模擬實際數據中可能遇到的問題，並驗證管線的穩健性。
        """
        # 確保表頭與 determine_parsing_recipe 中對 tick_data (csv_dynamic_header) 的預期一致
        # 預期表頭包含：成交日期, 商品代號, 成交價格, 成交時間
        # 為了能被 process_tick_data_row 正確處理，使用它期望的欄位名
        # （process_tick_data_row 內部會做一些清理和映射）
        # 原始 CSV 可能的表頭：成交日期,商品代號,到期月份(週別),履約價,買賣權,成交時間,成交價格,成交數量
        content = """成交日期,商品代號,到期月份(週別),履約價,買賣權,成交時間,成交價格,成交數量
20240726,TXO,202408W1,18000,買權,084501,120.5,2
20240726,TXO,202408W1,18000,買權,084502,121.0,3
20240726,MXF,202408,,,084503,1750.5,10
BADDATE,MXF,202408,,,084504,1750.0,1
20240726,TXF,202408,,,084505,17800,INVALID_VOL
20240727,TXO,202408W2,17500,賣權,090000,50.0,5
"""
        # 加入一行包含 "數據遺失" 和 "---" 的髒數據，模擬 scl_trigger_sample 的情況
        # 注意：determine_parsing_recipe 可能不會將這種情況識別為 tick_data，除非表頭符合
        # 我們需要確保這個檔案的表頭能被識別為 tick_data
        # content += "20240727,QQQ,202409,數據遺失,買權,090100,---,10\n" # 價格和成交量無效
        # 為了讓 determine_parsing_recipe 能識別，我們還是用標準的 tick data 表頭
        # process_tick_data_row 會處理數值轉換失敗的情況
        content += "20240727,QQQ,202409,100,買權,090100,數據遺失,10\n" # 價格無效
        content += "20240727,RRR,202409,200,賣權,090200,50.0,---\n" # 成交量無效

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        self.logger.info(f"已創建樣本 tick data CSV: {file_path}")


    def tearDown(self):
        if hasattr(self, 'base_temp_dir') and os.path.exists(self.base_temp_dir):
            shutil.rmtree(self.base_temp_dir)
        if hasattr(self, 'tick_data_sample_csv_path') and os.path.exists(self.tick_data_sample_csv_path):
            os.remove(self.tick_data_sample_csv_path) # 清理臨時創建的樣本檔案

    def test_pipeline_end_to_end_run(self):
        """
        執行端到端的管線測試。
        此測試模擬 `run.py` 腳本的實際執行，包括：
        1. 準備一個包含樣本 ZIP 檔案和一個特製的 `tick_data_sample.csv` 的輸入目錄。
        2. 通過 `subprocess.run` 調用 `run.py`，傳遞必要的命令列參數
           (輸入目錄, 輸出目錄, 資料庫名稱, 臨時工作目錄, 日誌級別)。
        3. 驗證 `run.py` 是否成功執行 (返回碼為 0)。
        4. 檢查 `format_map.json` 是否已創建且內容不為空。
        5. 檢查 DuckDB 資料庫檔案是否已創建。
        6. 連接到生成的 DuckDB 資料庫，並對其中的數據進行驗證：
           - `daily_ohlc` 表：檢查總記錄數是否符合預期 (基於 ZIP 中的樣本檔案)，
             並抽樣檢查一條記錄的特定欄位值。
           - `institutional_investors` 表：檢查總記錄數是否符合預期。
           - `tick_data` 表：檢查總記錄數是否符合預期 (基於 `tick_data_sample.csv` 中
             有效和無效數據的設計)，並抽樣檢查一條記錄的特定欄位值。
        此測試旨在確保整個管線從檔案讀取、解析、轉換到數據庫載入的流程正確無誤，
        並且能夠按預期處理有效的數據和過濾掉無效的數據。
        """
        test_db_name = "test_async_stream_analytics.duckdb" # 與 run.py 中預設一致或自定義
        db_full_path = os.path.join(self.temp_db_output_dir, test_db_name)
        format_map_path = os.path.join(self.temp_db_output_dir, "format_map.json")

        pipeline_command = [
            sys.executable, self.pipeline_run_py,
            "--input-dir", self.temp_input_dir,
            "--db-output-dir", self.temp_db_output_dir,
            "--db-name", test_db_name,
            "--temp-dir", self.temp_work_dir,
            "--log-level", "INFO" # 設為 DEBUG 可以看到更詳細的串流處理日誌
        ]

        print(f"執行 (v19.1 Async Stream) 精煉廠命令: {' '.join(pipeline_command)}")
        pipeline_process = subprocess.run(pipeline_command, capture_output=True, text=True, encoding='utf-8')

        print(f"精煉廠 stdout (v19.1 Async Stream):\n{pipeline_process.stdout}")
        if pipeline_process.stderr: # stderr 通常用於錯誤訊息
            print(f"精煉廠 stderr (v19.1 Async Stream):\n{pipeline_process.stderr}")

        self.assertEqual(pipeline_process.returncode, 0,
                         f"精煉廠 run.py (第一次執行) 應成功執行。Stderr: {pipeline_process.stderr}")

        self.assertTrue(os.path.exists(format_map_path), f"格式地圖 {format_map_path} (第一次執行後) 未建立。")
        with open(format_map_path, 'r', encoding='utf-8') as f_map:
            format_map_content = json.load(f_map)
        self.assertTrue(len(format_map_content) > 0, "格式地圖 (第一次執行後) 不應為空。")

        self.assertTrue(os.path.exists(db_full_path), f"DuckDB 資料庫 {db_full_path} (第一次執行後) 未建立。")

        # --- 第一次執行後的資料庫驗證 ---
        con1 = duckdb.connect(database=db_full_path, read_only=True)

        # 獲取並儲存第一次執行後的行數
        # 假設 sample_pipeline_data.zip 包含 daily_ohlc, institutional_investors, pcr, fx_rates
        # tick_data 來自 tick_data_sample.csv
        tables_to_check = ['daily_ohlc', 'institutional_investors', 'tick_data', 'pcr', 'fx_rates']
        initial_row_counts = {}

        for table_name in tables_to_check:
            try:
                count_res = con1.execute(f"SELECT COUNT(*) FROM {table_name};").fetchone()
                initial_row_counts[table_name] = count_res[0] if count_res else 0
            except duckdb.CatalogException: # 表可能不存在 (如果樣本數據中沒有該類型的檔案)
                initial_row_counts[table_name] = 0
            self.logger.info(f"第一次執行後，表格 {table_name} 行數: {initial_row_counts[table_name]}")

        # 驗證 daily_ohlc (來自 zip) - 第一次執行
        self.assertEqual(initial_row_counts.get('daily_ohlc', 0), 5, f"daily_ohlc 表初次記錄數應為5, 實際 {initial_row_counts.get('daily_ohlc', 0)}")
        res_v1_sample = con1.execute("SELECT close FROM daily_ohlc WHERE product_id='TXO' AND expiry_month='202201W1' AND strike_price=18000 AND option_type='C' AND trading_date='2022-01-04';").fetchone()
        self.assertIsNotNone(res_v1_sample)
        self.assertEqual(res_v1_sample[0], 190.0)

        # 驗證 institutional_investors (來自 zip) - 第一次執行
        self.assertEqual(initial_row_counts.get('institutional_investors', 0), 2, f"institutional_investors 初次記錄數應為2, 實際 {initial_row_counts.get('institutional_investors', 0)}")

        # 驗證 tick_data (來自 tick_data_sample.csv) - 第一次執行
        self.assertEqual(initial_row_counts.get('tick_data', 0), 4, f"tick_data 表初次記錄數應為4, 實際 {initial_row_counts.get('tick_data', 0)}")
        query_datetime_condition = "strftime(trade_datetime, '%Y-%m-%d %H:%M:%S.%f') = '2024-07-26 08:45:01.000000'"
        sql_query_final = f"SELECT price, volume FROM tick_data WHERE product_id='TXO' AND strike_price=18000 AND option_type='C' AND {query_datetime_condition};"
        res_tick_sample_final = con1.execute(sql_query_final).fetchone()
        self.assertIsNotNone(res_tick_sample_final, f"未能查詢到指定的 tick_data 樣本記錄 (第一次執行)。查詢: {sql_query_final}")
        if res_tick_sample_final:
            self.assertEqual(res_tick_sample_final[0], 120.5, "tick_data 樣本價格不符 (第一次執行)。")
            self.assertEqual(res_tick_sample_final[1], 2, "tick_data 樣本成交量不符 (第一次執行)。")

        # 假設 pcr 和 fx_rates 檔案在 sample_pipeline_data.zip 中存在且各有一行有效數據
        # 實際應根據 zip 內容調整預期值
        # 根據 sample_pipeline_data.zip 的內容，它似乎不直接包含 pcr.csv 或 fx_rates.csv。
        # 它包含 futures_daily_sample.csv, institutional_investors_sample.csv, options_daily_v1_sample.csv, options_daily_v2_sample.csv
        # options_daily_* 會進入 daily_ohlc 表。
        # futures_daily_sample.csv 也會進入 daily_ohlc 表。
        # 所以 pcr 和 fx_rates 的期望行數應該是 0，除非測試樣本有變動。
        self.assertEqual(initial_row_counts.get('pcr', 0), 0, f"pcr 表初次記錄數應為0 (除非樣本更新), 實際 {initial_row_counts.get('pcr', 0)}")
        self.assertEqual(initial_row_counts.get('fx_rates', 0), 0, f"fx_rates 表初次記錄數應為0 (除非樣本更新), 實際 {initial_row_counts.get('fx_rates', 0)}")

        con1.close()

        # --- 第二次執行管線 ---
        self.logger.info("="*20 + " 開始第二次執行管線 " + "="*20)
        pipeline_process_run2 = subprocess.run(pipeline_command, capture_output=True, text=True, encoding='utf-8')

        print(f"精煉廠 stdout (第二次執行):\n{pipeline_process_run2.stdout}")
        if pipeline_process_run2.stderr:
            print(f"精煉廠 stderr (第二次執行):\n{pipeline_process_run2.stderr}")

        self.assertEqual(pipeline_process_run2.returncode, 0,
                         f"精煉廠 run.py (第二次執行) 應成功執行。Stderr: {pipeline_process_run2.stderr}")

        # 驗證第二次執行過程中沒有 ON CONFLICT 相關的錯誤或警告日誌
        # 由於我們移除了應用程式級別的 "唯一索引欄位...不完全存在於插入欄位..." 警告，
        # 主要關注的是 DuckDB 是否有其他錯誤。DO NOTHING 應該是靜默的。
        log_output_run2 = pipeline_process_run2.stdout + pipeline_process_run2.stderr
        self.assertNotIn("ON CONFLICT", log_output_run2.upper(), # 檢查大寫以捕獲不同的大小寫形式
                         "第二次執行時，日誌中不應出現 ON CONFLICT 相關的錯誤或非預期警告。")
        self.assertNotIn("WARNING", log_output_run2, # 檢查是否有其他意外的 WARNING
                         f"第二次執行時，日誌中不應出現非預期的 WARNING。Stdout: {pipeline_process_run2.stdout} Stderr: {pipeline_process_run2.stderr}")
        self.assertNotIn("ERROR", log_output_run2, # 檢查是否有其他意外的 ERROR
                         f"第二次執行時，日誌中不應出現非預期的 ERROR。Stdout: {pipeline_process_run2.stdout} Stderr: {pipeline_process_run2.stderr}")


        # --- 第二次執行後的資料庫驗證 ---
        con2 = duckdb.connect(database=db_full_path, read_only=True)
        secondary_row_counts = {}
        for table_name in tables_to_check:
            try:
                count_res = con2.execute(f"SELECT COUNT(*) FROM {table_name};").fetchone()
                secondary_row_counts[table_name] = count_res[0] if count_res else 0
            except duckdb.CatalogException:
                secondary_row_counts[table_name] = 0
            self.logger.info(f"第二次執行後，表格 {table_name} 行數: {secondary_row_counts[table_name]}")

            # 斷言行數沒有增加
            self.assertEqual(secondary_row_counts[table_name], initial_row_counts[table_name],
                             f"表格 {table_name} 在第二次執行後行數不應改變。初始: {initial_row_counts[table_name]}, 第二次: {secondary_row_counts[table_name]}")

        con2.close()

    # test_process_file_with_scl_trigger_sample 已被移除的功能所替代


if __name__ == "__main__":
    # unittest.main(argv=['first-arg-is-ignored'], exit=False)
    # 為了能在 Colab 或腳本中單獨運行和調試，可以這樣配置：
    suite = unittest.TestSuite()
    suite.addTest(TestTaifexDataPipelineAsync('test_pipeline_end_to_end_run')) # 運行主要的端到端測試
    # suite.addTest(TestTaifexDataPipelineAsync('test_process_file_with_scl_trigger_sample')) # 此測試已被移除或合併邏輯
    runner = unittest.TextTestRunner()
    runner.run(suite)

# 之前的 log_message 和 _get_test_logger 輔助函式定義已移除
# --- End of _test_run.py ---
