# -*- coding: utf-8 -*-
# 精煉廠測試檔 (v16.0 批次掃描版)
import os
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

from apps.taifex_data_pipeline.run import SimpleLogger, determine_parsing_recipe, worker_process_file, HardwareManager

class TestTaifexDataPipelineBatch(unittest.TestCase): # 更名以區分

    def setUp(self):
        # 初始化測試專用的 logger
        # 注意：這會覆蓋 run.py 中全域的 logger，僅在測試期間生效
        global logger
        self.logger = SimpleLogger(log_level="DEBUG")
        logger = self.logger # 讓 run.py 中直接使用 logger 的部分能獲取到這個實例

        self.pipeline_run_py = os.path.join(current_pipeline_dir, "run.py")
        self.sample_zip_file_path = os.path.join(current_pipeline_dir, "sample_pipeline_data.zip")

        self.base_temp_dir = tempfile.mkdtemp(prefix="test_pipeline_batch_")
        self.temp_input_dir = os.path.join(self.base_temp_dir, "input") # run.py 的 --input-dir
        self.temp_db_output_dir = os.path.join(self.base_temp_dir, "db_output") # run.py 的 --db-output-dir
        self.temp_work_dir = os.path.join(self.base_temp_dir, "work_temp") # run.py 的 --temp-dir

        os.makedirs(self.temp_input_dir, exist_ok=True)
        os.makedirs(self.temp_db_output_dir, exist_ok=True)
        os.makedirs(self.temp_work_dir, exist_ok=True)

        if not os.path.exists(self.sample_zip_file_path):
            self.fail(f"測試 ZIP 檔案 {self.sample_zip_file_path} 不存在。")

        # 在此版本測試中，run.py 會掃描 --input-dir，所以我們需要將 ZIP 複製進去
        shutil.copy(self.sample_zip_file_path, os.path.join(self.temp_input_dir, "sample_pipeline_data.zip"))

    def tearDown(self):
        if hasattr(self, 'base_temp_dir') and os.path.exists(self.base_temp_dir):
            shutil.rmtree(self.base_temp_dir)

    def test_pipeline_batch_run(self):
        test_db_name = "test_batch_analytics.duckdb"
        db_full_path = os.path.join(self.temp_db_output_dir, test_db_name)
        format_map_path = os.path.join(self.temp_db_output_dir, "format_map.json") # run.py 中的常數

        pipeline_command = [
            sys.executable, self.pipeline_run_py,
            "--input-dir", self.temp_input_dir, # 包含 ZIP 的目錄
            "--db-output-dir", self.temp_db_output_dir,
            "--db-name", test_db_name,
            "--temp-dir", self.temp_work_dir,
            "--log-level", "INFO"
        ]

        print(f"執行 (v16 Hotfix前) 精煉廠命令: {' '.join(pipeline_command)}")
        pipeline_process = subprocess.run(pipeline_command, capture_output=True, text=True, encoding='utf-8')

        print(f"精煉廠 stdout (v16 Hotfix前):\n{pipeline_process.stdout}")
        if pipeline_process.stderr:
            print(f"精煉廠 stderr (v16 Hotfix前):\n{pipeline_process.stderr}")

        self.assertEqual(pipeline_process.returncode, 0,
                         f"精煉廠 run.py (v16 Hotfix前) 應成功執行。Stderr: {pipeline_process.stderr}")

        self.assertTrue(os.path.exists(format_map_path), f"格式地圖 {format_map_path} 未建立。")
        with open(format_map_path, 'r') as f_map:
            format_map_content = json.load(f_map)
        self.assertTrue(len(format_map_content) > 0, "格式地圖不應為空。")

        self.assertTrue(os.path.exists(db_full_path), f"DuckDB 資料庫 {db_full_path} 未建立。")

        con = duckdb.connect(database=db_full_path, read_only=True)

        # **預期**: 在 Hotfix 後, futures_daily_sample.csv 應被正確解析
        # 所以 daily_ohlc 記錄數應為 5 (options v1(2) + v2(2) + futures(1))
        res_ohlc_count = con.execute("SELECT COUNT(*) FROM daily_ohlc;").fetchone()
        self.assertIsNotNone(res_ohlc_count)
        self.assertEqual(res_ohlc_count[0], 5, f"daily_ohlc 表記錄數 (Hotfix後) 應為5, 實際 {res_ohlc_count[0]}")

        res_v1_sample = con.execute("SELECT close FROM daily_ohlc WHERE product_id='TXO' AND expiry_month='202201W1' AND strike_price=18000 AND option_type='C' AND trading_date='2022-01-04';").fetchone()
        self.assertIsNotNone(res_v1_sample)
        self.assertEqual(res_v1_sample[0], 190.0)

        res_inst_count = con.execute("SELECT COUNT(*) FROM institutional_investors;").fetchone()
        self.assertIsNotNone(res_inst_count)
        self.assertEqual(res_inst_count[0], 2, f"institutional_investors 記錄數應為2, 實際 {res_inst_count[0]}")

        res_inst_sample_fut = con.execute("SELECT long_pos_vol FROM institutional_investors WHERE product_name='臺股期貨' AND investor_type='外資' AND data_date='2023-01-03' AND instrument_type='Future';").fetchone()
        self.assertIsNotNone(res_inst_sample_fut)
        self.assertEqual(res_inst_sample_fut[0], 10000)
        con.close()

    def test_process_file_with_scl_trigger_sample(self):
        """
        測試 pipeline_tick_data 是否能處理包含已知髒數據（類似導致 'scl' 的情況）的樣本。
        預期：髒數據應被轉換為 NaN，並且如果影響到 dropna 的關鍵欄位，則該行被移除，
              或者 worker_process_file 成功返回，相應欄位為 NaN。
              整個過程不應拋出未處理的 'scl' 異常。
        """
        self.logger.info("--- 開始執行: test_process_file_with_scl_trigger_sample --- (QA)")
        # from apps.taifex_data_pipeline.run import determine_parsing_recipe, worker_process_file, SimpleLogger, HardwareManager # 已在頂部導入

        # 來自指令的 trigger_scl_error_sample.csv 內容
        csv_content_str = """成交日期,商品代號,到期月份(週別),成交時間,成交價格,成交數量(B+S),開盤價,最高價,最低價,結算價,近月價格,遠月價格,委買價格,委賣價格,委買數量,委賣數量
20250701,TX,202507,084500,18001,10,18000,18005,17999,18002,18001,18010,18000,18001,20,15
20250701,TX,202507,084501,數據遺失,5,18002,18006,18001,18003,18001,18010,18001,18002,22,18
20250701,TX,202507,084502,18005,---,18004,18008,18003,18005,18001,18010,18004,18005,19,13
20250701,TX,202507,084503,18010,8,18009,18012,18008,18011,18001,18010,18009,18010,25,30
"""
        # hw_mgr 初始化，logger 已在 setUp 中設定並賦值給全域 logger
        hw_mgr = HardwareManager(user_max_workers=1)


        descriptor = "trigger_scl_error_sample.csv"
        content_bytes = csv_content_str.encode('ms950') # 假設是 MS950 編碼

        # 1. 確定解析配方
        # 為了讓 determine_parsing_recipe 正確識別為 tick_data，我們需要確保 header 和內容符合其判斷邏輯
        # Daily_*.csv 通常是動態表頭，包含 "成交日期", "商品代號", "成交價格", "成交時間"
        # 指令中提供的樣本是CSV，所以應該是 csv_dynamic_header -> tick_data
        recipe = determine_parsing_recipe(content_bytes, descriptor)
        self.assertIsNotNone(recipe, "無法為樣本數據確定解析配方。")
        self.assertEqual(recipe.get("pipeline"), "tick_data", f"樣本數據應被識別為 'tick_data' 管線，而非 '{recipe.get('pipeline')}'")
        self.assertIn(recipe.get("parser"), ["csv_dynamic_header", "csv"], f"預期解析器為 csv_dynamic_header 或 csv，得到 {recipe.get('parser')}")


        # 2. 執行 worker_process_file
        # worker_process_file 需要一個 staging_path
        staging_dir = os.path.join(self.base_temp_dir, "scl_test_staging")
        os.makedirs(staging_dir, exist_ok=True)

        result = worker_process_file((descriptor, content_bytes, recipe, staging_dir, hw_mgr))

        self.logger.debug(f"worker_process_file 結果: {result}")

        # 3. 驗證結果
        # 預期：worker_process_file 應成功執行 (status: 'success')
        # 因為 pipeline_tick_data 中的 errors='coerce' 會將 "數據遺失" 和 "---" 轉為 NaN
        # 然後 dropna(subset=['trade_datetime','product_id','price','volume']) 會移除這些行
        self.assertEqual(result.get("status"), "success",
                         f"處理包含髒數據的樣本時，worker_process_file 未成功。錯誤: {result.get('error_msg')}")

        # 由於第二行 price='數據遺失' -> NaN, 第三行 volume='---' -> NaN，這兩行都會被 dropna 移除
        # 所以最終 parquet 檔案中應該只包含第一行和第四行數據
        self.assertEqual(result.get("rows"), 2,
                         f"預期處理後剩下 2 行數據，實際得到 {result.get('rows')} 行。檢查髒數據是否按預期被移除。")

        # 可以進一步讀取 parquet 檔案驗證內容 (可選)
        if result.get("status") == "success" and result.get("file"):
            import pandas as pd
            df_processed = pd.read_parquet(result.get("file"))
            self.assertEqual(len(df_processed), 2, "Parquet 檔案中的行數與預期不符。")
            # 檢查 price 和 volume 是否都是數值類型且不含 NaN (因為 NaN 的行已被移除)
            self.assertTrue(pd.api.types.is_numeric_dtype(df_processed['price']))
            self.assertTrue(pd.api.types.is_numeric_dtype(df_processed['volume']))
            self.assertFalse(df_processed['price'].isnull().any())
            self.assertFalse(df_processed['volume'].isnull().any())
            self.logger.debug("Parquet 檔案內容驗證通過。")

        self.logger.info("--- test_process_file_with_scl_trigger_sample 執行完畢 --- (QA)")


if __name__ == "__main__":
    # unittest.main(argv=['first-arg-is-ignored'], exit=False)
    # 為了能在 Colab 或腳本中單獨運行和調試，可以這樣配置：
    suite = unittest.TestSuite()
    # suite.addTest(TestTaifexDataPipelineBatch('test_pipeline_batch_run')) # 如果需要運行舊測試
    suite.addTest(TestTaifexDataPipelineBatch('test_process_file_with_scl_trigger_sample'))
    runner = unittest.TextTestRunner()
    runner.run(suite)

# 之前的 log_message 和 _get_test_logger 輔助函式定義已移除
# --- End of _test_run.py ---
