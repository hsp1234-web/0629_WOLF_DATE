# -*- coding: utf-8 -*-
# 精煉廠測試檔
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

class TestTaifexDataPipeline(unittest.TestCase):

    def setUp(self):
        """測試設定"""
        self.pipeline_run_py = os.path.join(current_pipeline_dir, "run.py")
        self.sample_zip_file_path = os.path.join(current_pipeline_dir, "sample_pipeline_data.zip")

        # 建立臨時目錄
        self.base_temp_dir = tempfile.mkdtemp(prefix="test_pipeline_")
        self.temp_input_dir = os.path.join(self.base_temp_dir, "input")
        self.temp_db_output_dir = os.path.join(self.base_temp_dir, "db_output")
        self.temp_work_dir = os.path.join(self.base_temp_dir, "work_temp") # 給 run.py 的 --temp-dir

        os.makedirs(self.temp_input_dir, exist_ok=True)
        os.makedirs(self.temp_db_output_dir, exist_ok=True)
        os.makedirs(self.temp_work_dir, exist_ok=True)

        # 解壓縮範例 ZIP 檔案到臨時輸入目錄
        if not os.path.exists(self.sample_zip_file_path):
            self.fail(f"測試用的 ZIP 檔案 {self.sample_zip_file_path} 不存在。請先建立它。")

        with zipfile.ZipFile(self.sample_zip_file_path, 'r') as zip_ref:
            zip_ref.extractall(self.temp_input_dir)

        # 確認解壓縮後的檔案存在 (可選)
        # print(f"解壓縮到 {self.temp_input_dir}: {os.listdir(self.temp_input_dir)}")


    def tearDown(self):
        """測試清理"""
        if os.path.exists(self.base_temp_dir):
            shutil.rmtree(self.base_temp_dir)
            # print(f"移除臨時目錄: {self.base_temp_dir}")

    def test_pipeline_full_run(self):
        """
        測試精煉廠的完整執行流程：
        從解壓縮的範例檔讀取 -> 解析 -> 清洗 -> 載入 DuckDB -> 驗證 DB 內容。
        """
        test_db_name = "test_analytics.duckdb"
        db_full_path = os.path.join(self.temp_db_output_dir, test_db_name)
        format_map_path = os.path.join(self.temp_db_output_dir, "format_map.json")

        pipeline_command = [
            sys.executable, self.pipeline_run_py,
            "--input-dir", self.temp_input_dir,
            "--db-output-dir", self.temp_db_output_dir,
            "--db-name", test_db_name,
            "--temp-dir", self.temp_work_dir,
            "--log-level", "INFO" # 測試時可以設為 DEBUG 以獲取更多資訊
        ]

        print(f"執行精煉廠命令: {' '.join(pipeline_command)}")
        pipeline_process = subprocess.run(pipeline_command, capture_output=True, text=True, encoding='utf-8')

        print(f"精煉廠 stdout:\n{pipeline_process.stdout}")
        if pipeline_process.stderr: # 只在 stderr 有內容時印出
            print(f"精煉廠 stderr:\n{pipeline_process.stderr}")

        self.assertEqual(pipeline_process.returncode, 0,
                         f"精煉廠 run.py 應成功執行並返回 0。Stderr: {pipeline_process.stderr}")

        # 1. 驗證 format_map.json 是否已建立
        self.assertTrue(os.path.exists(format_map_path), f"格式地圖 {format_map_path} 未被建立。")
        try:
            with open(format_map_path, 'r') as f_map:
                format_map_content = json.load(f_map)
            self.assertTrue(len(format_map_content) > 0, "格式地圖不應為空。")
        except Exception as e:
            self.fail(f"讀取或解析格式地圖 {format_map_path} 失敗: {e}")


        # 2. 驗證 DuckDB 資料庫內容
        self.assertTrue(os.path.exists(db_full_path), f"DuckDB 資料庫檔案 {db_full_path} 未被建立。")

        try:
            con = duckdb.connect(database=db_full_path, read_only=True)

            # 驗證 daily_ohlc 表
            # 預期: v1 (2筆) + v2 (2筆) + futures (1筆) = 5 筆
            res_ohlc_count = con.execute("SELECT COUNT(*) FROM daily_ohlc;").fetchone()
            self.assertIsNotNone(res_ohlc_count, "無法從 daily_ohlc 查詢筆數。")
            self.assertEqual(res_ohlc_count[0], 5, "daily_ohlc 表的記錄數不符合預期 (應為5)。")

            # 抽樣驗證 daily_ohlc 內容
            # 來自 options_daily_v1_sample.csv, 第一筆, TXO 202201W1 18000 買權, 收盤價 190
            res_v1_sample = con.execute("SELECT close FROM daily_ohlc WHERE product_id='TXO' AND expiry_month='202201W1' AND strike_price=18000 AND option_type='C' AND trading_date='2022-01-04';").fetchone()
            self.assertIsNotNone(res_v1_sample, "查詢 daily_ohlc (v1 sample) 失敗。")
            self.assertEqual(res_v1_sample[0], 190, "daily_ohlc v1 sample 的收盤價不符預期。")

            # 來自 futures_daily_sample.csv, TXF 202301, 收盤價 16950
            res_futures_sample = con.execute("SELECT close FROM daily_ohlc WHERE product_id='TXF' AND expiry_month='202301' AND trading_date='2023-01-03';").fetchone()
            self.assertIsNotNone(res_futures_sample, "查詢 daily_ohlc (futures sample) 失敗。")
            self.assertEqual(res_futures_sample[0], 16950, "daily_ohlc futures sample 的收盤價不符預期。")


            # 驗證 institutional_investors 表
            # 預期: 2 筆
            res_inst_count = con.execute("SELECT COUNT(*) FROM institutional_investors;").fetchone()
            self.assertIsNotNone(res_inst_count, "無法從 institutional_investors 查詢筆數。")
            self.assertEqual(res_inst_count[0], 2, "institutional_investors 表的記錄數不符合預期 (應為2)。")

            # 抽樣驗證 institutional_investors 內容
            # 臺股期貨, 外資, long_pos_vol 10000
            res_inst_sample_fut = con.execute("SELECT long_pos_vol FROM institutional_investors WHERE product_name='臺股期貨' AND investor_type='外資' AND data_date='2023-01-03' AND instrument_type='Future';").fetchone()
            self.assertIsNotNone(res_inst_sample_fut, "查詢 institutional_investors (臺股期貨 sample) 失敗。")
            self.assertEqual(res_inst_sample_fut[0], 10000, "institutional_investors 臺股期貨 sample 的 long_pos_vol 不符預期。")

            # 臺指選擇權, 外資, 買權, long_pos_vol 20000
            res_inst_sample_opt = con.execute("SELECT long_pos_vol FROM institutional_investors WHERE product_name='臺指選擇權' AND investor_type='外資' AND data_date='2023-01-03' AND instrument_type='Option' AND option_type='C';").fetchone()
            self.assertIsNotNone(res_inst_sample_opt, "查詢 institutional_investors (臺指選擇權 sample) 失敗。")
            self.assertEqual(res_inst_sample_opt[0], 20000, "institutional_investors 臺指選擇權 sample 的 long_pos_vol 不符預期。")

            con.close()
        except Exception as e_db_check:
            self.fail(f"檢查 DuckDB 資料庫內容時發生錯誤: {e_db_check}")


if __name__ == "__main__":
    unittest.main(argv=['first-arg-is-ignored'], exit=False)
