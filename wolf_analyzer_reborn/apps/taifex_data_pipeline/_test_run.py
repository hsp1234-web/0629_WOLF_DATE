# -*- coding: utf-8 -*-
import unittest
import subprocess
import os
import shutil
import sys
import json
import duckdb
import pandas as pd # 用於創建範例 DataFrame

class TestPipelineRun(unittest.TestCase):
    # ---- 目錄名稱 ----
    base_dir = os.path.dirname(os.path.abspath(__file__))
    run_script_path = os.path.join(base_dir, "run.py")

    mock_gdrive_input_name = "mock_pipeline_input_X9Y8Z7"
    mock_gdrive_output_db_name = "mock_pipeline_output_db_X9Y8Z7"
    temp_local_workspace_name = "temp_pipeline_ws_X9Y8Z7"

    # ---- 完整路徑 ----
    mock_input_dir_path: str
    mock_output_db_dir_path: str
    temp_workspace_path: str

    @classmethod
    def setUpClass(cls):
        """在所有測試開始前，創建模擬目錄和範例數據"""
        cls.mock_input_dir_path = os.path.join(cls.base_dir, cls.mock_gdrive_input_name)
        cls.mock_output_db_dir_path = os.path.join(cls.base_dir, cls.mock_gdrive_output_db_name)
        cls.temp_workspace_path = os.path.join(cls.base_dir, cls.temp_local_workspace_name)

        # 清理可能存在的舊目錄
        for path in [cls.mock_input_dir_path, cls.mock_output_db_dir_path, cls.temp_workspace_path]:
            if os.path.exists(path):
                shutil.rmtree(path)
            os.makedirs(path, exist_ok=True)

        cls._create_sample_data()

    @classmethod
    def tearDownClass(cls):
        """在所有測試結束後，清理所有模擬目錄和檔案"""
        for path in [cls.mock_input_dir_path, cls.mock_output_db_dir_path, cls.temp_workspace_path]:
            if os.path.exists(path):
                shutil.rmtree(path)

    @classmethod
    def _create_sample_data(cls):
        """創建範例輸入檔案"""
        # 1. 模擬期貨每日行情 (futures_daily - 使用手動欄位名)
        #    對應 pipeline_core.MANUAL_COLUMN_NAMES['futures_daily']
        #    對應 pipeline_daily_ohlc
        futures_daily_content = (
            "交易日期,商品代號,到期月份/週別,開盤價,最高價,最低價,收盤價,漲跌價,漲跌%,成交量,結算價,未沖銷契約數,最後最佳買價,最後最佳賣價,歷史最高價,歷史最低價,是否暫停交易,交易時段,價差對單式委託成交量\n"
            "2023/01/03,TX,202301,14100,14200,14050,14150,50,0.35,80000,14150,90000,14149,14150,18000,10000,否,一般,0\n"
            "2023/01/03,MTX,202301,3500,3550,3490,3520,20,0.57,15000,3520,20000,3519,3520,4000,3000,否,一般,0\n"
        )
        with open(os.path.join(cls.mock_input_dir_path, "futures_daily_manual_cols_20230103.csv"), "w", encoding="ms950") as f:
            f.write(futures_daily_content)

        # 2. 模擬三大法人 (institutional_investors - 動態表頭)
        #    對應 pipeline_institutional_investors
        inst_invest_content = (
            "日期,商品名稱,身份別,多方交易 口數,多方交易 契約金額(千元),空方交易 口數,空方交易 契約金額(千元),多空交易淨口數,多空交易淨額(千元),未平倉 多方 口數,未平倉 多方 契約金額(千元),未平倉 空方 口數,未平倉 空方 契約金額(千元),未平倉淨口數,未平倉淨額(千元)\n" # 欄位名中的空格模擬真實情況
            "2023/01/03,臺股期貨,自營商,1000,1400000,800,1120000,200,280000,5000,7000000,4000,5600000,1000,1400000\n"
            "2023/01/03,臺股期貨,投信,500,700000,600,840000,-100,-140000,2000,2800000,2500,3500000,-500,-700000\n"
            "2023/01/03,電子期貨,外資,200,50000,150,37500,50,12500,1000,250000,800,200000,200,50000\n"
        )
        with open(os.path.join(cls.mock_input_dir_path, "inst_invest_dynamic_20230103.csv"), "w", encoding="utf-8") as f:
            f.write(inst_invest_content)

        # 3. 模擬期貨Tick數據 (tick_data - 固定寬度 FWF)
        #    對應 pipeline_tick_data
        #    成交日期 商品代號 到期月份/週別 履約價 買賣權 成交時間 成交價格 成交數量(B+S) 近月價格 遠月價格 狀態碼
        #    YYYYMMDD CC YYMMDD/YYYYWW NNNNNN C/P HHMMSSNNN NNNNNN NNNNNN NNNNNN NNNNNN NN
        #    長度:    8  2+1+6/6      1+6     1  1+6+3    6      6      6      6      2
        #    實際長度: 8  不固定       7       1    10       6      6      6      6      2
        #    範例: 20230103TX     202301W1          101527001  8477     2
        #    由於 FWF 推斷可能不穩定，提供一個更結構化的範例，並確保有 '---' 分隔線
        tick_data_content = (
            "成交日期 商品代號       到期月份/週別   履約價 買賣權 成交時間   成交價格 成交數量(B+S)\n"
            "----------------------------------------------------------------------\n"
            "20230103 TX             202301          0      P      10:15:27.001   14100    2\n" # 履約價0, 買賣權P 模擬期貨
            "20230103 TXO            202301W1     14000     C      10:15:28.123   50       10\n"
            "20230103 MTX            202302          0      P      10:16:01.500   3500     5\n"
        )
        # 調整欄位對齊以輔助 pandas.read_fwf (雖然它應該能自動推斷)
        # 成交日期(8) 商品代號(7) 到期月份/週別(12) 履約價(7) 買賣權(2) 成交時間(12) 成交價格(7) 成交數量(6)
        tick_data_content_aligned = (
            "成交日期 商品代號 到期月份/週別   履約價 買賣權 成交時間     成交價格 成交數量\n"
            "--------------------------------------------------------------------------\n"
            "20230103 TX      202301             0 P 10:15:27.001  14100      2\n"
            "20230103 TXO     202301W1        14000 C 10:15:28.123     50     10\n"
            "20230103 MTX     202302             0 P 10:16:01.500   3500      5\n"
        )

        with open(os.path.join(cls.mock_input_dir_path, "tick_data_fwf_20230103.txt"), "w", encoding="utf-8") as f:
            f.write(tick_data_content_aligned)

        # 4. 模擬一個 ODS 檔案 (pipeline: unknown)
        #    ODS 檔案是二進位，這裡創建一個假的空 ods 檔案，只為測試檔案發現和配方判斷
        with open(os.path.join(cls.mock_input_dir_path, "fake_data.ods"), "w", encoding="utf-8") as f:
            f.write("This is not a real ODS file, for testing only.")


    def _run_pipeline_script(self) -> tuple[str, str, int]:
        """輔助函數：執行 run.py 並返回其標準輸出、標準錯誤和返回碼"""
        python_executable = sys.executable
        cmd = [
            python_executable, self.run_script_path,
            "--input-dir", self.mock_input_dir_path,
            "--output-db-dir", self.mock_output_db_dir_path,
            "--local-workspace-dir", self.temp_workspace_path,
            "--monitoring-interval", "0" # 測試時關閉高頻監控
        ]
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8'
        )
        # 增加 timeout 因為 DuckDB 載入和 Parquet 寫入可能耗時
        stdout, stderr = process.communicate(timeout=300)

        # ---- 移除強制打印 ----
        # print("\n--- _run_pipeline_script captured STDOUT: ---", flush=True)
        # print(stdout if stdout else "<stdout is empty>", flush=True)
        # print("--- END _run_pipeline_script STDOUT ---\n", flush=True)

        # if stderr:
        #     print("\n--- _run_pipeline_script captured STDERR: ---", flush=True)
        #     print(stderr, flush=True)
        #     print("--- END _run_pipeline_script STDERR ---\n", flush=True)

        return stdout, stderr, process.returncode

    def test_pipeline_end_to_end(self):
        """測試精煉廠的完整端到端流程"""
        stdout, stderr, returncode = self._run_pipeline_script()

        if returncode != 0: # 如果 run.py 本身執行失敗，打印其輸出
            print("--- pipeline run.py STDOUT (on error returncode) ---")
            print(stdout)
            print("--- pipeline run.py STDERR (on error returncode) ---")
            print(stderr)
        self.assertEqual(returncode, 0, f"pipeline run.py 執行失敗。Return code: {returncode}\nstderr:\n{stderr}")

        # 1. 檢查 format_map.json 是否生成/更新
        format_map_path = os.path.join(self.mock_output_db_dir_path, "format_map.json")
        self.assertTrue(os.path.exists(format_map_path), f"format_map.json 未找到於 {format_map_path}.\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}")
        with open(format_map_path, 'r') as f:
            format_map = json.load(f)
        self.assertTrue(len(format_map) >= 3, "format_map.json 應至少包含3個已知檔案的配方") # futures, inst, tick (ods是unknown)

        # 檢查是否有為已知檔案生成的配方
        found_futures_recipe = any(fm.get("pipeline") == "daily_ohlc" for fm in format_map.values())
        self.assertTrue(found_futures_recipe, "未找到 daily_ohlc 的配方")
        found_inst_recipe = any(fm.get("pipeline") == "institutional_investors" for fm in format_map.values())
        self.assertTrue(found_inst_recipe, "未找到 institutional_investors 的配方")
        found_tick_recipe = any(fm.get("pipeline") == "tick_data" for fm in format_map.values())
        self.assertTrue(found_tick_recipe, "未找到 tick_data 的配方")


        # 2. 檢查 DuckDB 資料庫檔案是否生成
        db_file_path = os.path.join(self.mock_output_db_dir_path, "taifex_analytics_v8.0.duckdb")
        self.assertTrue(os.path.exists(db_file_path), "DuckDB 資料庫檔案未找到")

        # 3. 連接資料庫並驗證數據
        try:
            con = duckdb.connect(database=db_file_path, read_only=True)

            # 驗證 daily_ohlc 表
            daily_ohlc_count = con.execute("SELECT COUNT(*) FROM daily_ohlc").fetchone()[0]
            self.assertEqual(daily_ohlc_count, 2, "daily_ohlc 表應有 2 筆記錄")
            sample_ohlc = con.execute("SELECT product_id, close FROM daily_ohlc WHERE product_id = 'TX'").fetchone()
            self.assertIsNotNone(sample_ohlc, "TX 數據應存在於 daily_ohlc")
            self.assertEqual(sample_ohlc[1], 14150.0, "TX 收盤價不符預期")

            # 驗證 institutional_investors 表
            inst_count = con.execute("SELECT COUNT(*) FROM institutional_investors").fetchone()[0]
            self.assertEqual(inst_count, 3, "institutional_investors 表應有 3 筆記錄")
            sample_inst = con.execute("SELECT net_pos_vol FROM institutional_investors WHERE product_name = '臺股期貨' AND investor_type = '自營商'").fetchone()
            self.assertIsNotNone(sample_inst, "臺股期貨-自營商數據應存在")
            self.assertEqual(sample_inst[0], 200, "臺股期貨-自營商淨部位口數不符預期")

            # 驗證 tick_data 表
            tick_count = con.execute("SELECT COUNT(*) FROM tick_data").fetchone()[0]
            self.assertEqual(tick_count, 3, "tick_data 表應有 3 筆記錄")
            sample_tick = con.execute("SELECT price FROM tick_data WHERE product_id = 'TXO' AND strike_price = 14000 AND option_type = 'C'").fetchone()
            self.assertIsNotNone(sample_tick, "TXO 14000 Call 數據應存在")
            self.assertEqual(sample_tick[0], 50.0, "TXO 14000 Call 成交價不符預期")

            con.close()
        except Exception as e_db:
            self.fail(f"資料庫驗證失敗: {e_db}")

        # 4. 檢查日誌檔案是否生成 (路徑可能在 local_workspace 或 output_db_dir)
        log_files_in_output = [f for f in os.listdir(self.mock_output_db_dir_path) if f.startswith("pipeline_run_log_") and f.endswith(".txt")]
        log_files_in_workspace = [f for f in os.listdir(self.temp_workspace_path) if f.startswith("pipeline_run_log_") and f.endswith(".txt")]
        self.assertTrue(len(log_files_in_output) > 0 or len(log_files_in_workspace) > 0, "未找到執行日誌檔案")

        # 5. 檢查本地暫存區是否被清理 (staging 和 duckdb_temp)
        local_staging_path = os.path.join(self.temp_workspace_path, "staging_parquet")
        local_db_temp_path = os.path.join(self.temp_workspace_path, "duckdb_temp")
        self.assertFalse(os.path.exists(local_staging_path) and os.listdir(local_staging_path),
                         f"本地暫存目錄 {local_staging_path} 未被完全清理")
        self.assertFalse(os.path.exists(local_db_temp_path) and os.listdir(local_db_temp_path),
                         f"本地 DuckDB 臨時目錄 {local_db_temp_path} 未被完全清理")

        # 如果測試到這裡還沒因為 returncode != 0 而提前結束，
        # 但後續斷言失敗了，可以嘗試讀取 debug log
        if hasattr(self, '_outcome') and self._outcome.errors: # _outcome is internal, use carefully
             pass # Errors already printed by Popen handling
        elif hasattr(self, '_outcome') and self._outcome.failures: # Check if any test failed
            debug_log_file = os.path.join(self.temp_workspace_path, "debug_pipeline_run.log")
            if os.path.exists(debug_log_file):
                print(f"\n--- DEBUG LOG ({debug_log_file}) ---")
                with open(debug_log_file, "r", encoding="utf-8") as f_log:
                    print(f_log.read())
                print("--- END DEBUG LOG ---")
            else:
                print(f"\n--- DEBUG LOG {debug_log_file} NOT FOUND ---")
        # 移除之前的臨時 stdout/stderr 打印，因為它可能不總是被 runner 顯示
        # 依賴 run.py 中 logger 的 console 輸出 和 debug_pipeline_run.log

if __name__ == '__main__':
    unittest.main(verbosity=2)
