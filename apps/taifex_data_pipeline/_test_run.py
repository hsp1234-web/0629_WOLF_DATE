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

class TestTaifexDataPipelineBatch(unittest.TestCase): # 更名以區分

    def setUp(self):
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

if __name__ == "__main__":
    unittest.main(argv=['first-arg-is-ignored'], exit=False)
