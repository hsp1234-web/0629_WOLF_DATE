# -*- coding: utf-8 -*-
# 精煉廠測試檔 (v18.0 佇列驅動版本)
import os
import sys
import unittest
import json
import tempfile
import shutil
import zipfile
import duckdb
import queue # 用於模擬佇列

# --- 路徑自我校正樣板碼 ---
try:
    current_pipeline_dir = os.path.dirname(os.path.abspath(__file__))
    apps_dir = os.path.dirname(current_pipeline_dir)
    project_root = os.path.dirname(apps_dir)

    if apps_dir not in sys.path:
        sys.path.insert(0, apps_dir)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    # 導入被測試的模組
    from taifex_data_pipeline import run as pipeline_run_module
    # 如果 run.py 中有 SimpleLogger 或 HardwareManager 的全域實例，
    # 且不希望它們在 import 時就打印，可能需要調整 run.py 或在這裡 mock它們
    # 目前 run.py 的 logger 是在 main() 或 run_pipeline_from_queue() 中才實例化的

except Exception as e:
    print(f"路徑校正或導入時發生錯誤: {e}", file=sys.stderr)
    # 讓 unittest 框架能捕獲這個導入錯誤
    pipeline_run_module = None # 標記導入失敗
    # raise # 可以選擇重新拋出，讓測試框架直接失敗

class TestTaifexDataPipelineQueueDriven(unittest.TestCase):

    def setUp(self):
        """測試設定"""
        if pipeline_run_module is None:
            self.fail("pipeline_run_module 未能成功導入，請檢查路徑或 run.py 錯誤。")

        self.sample_zip_file_path = os.path.join(current_pipeline_dir, "sample_pipeline_data.zip")

        self.base_temp_dir = tempfile.mkdtemp(prefix="test_pipeline_q_")
        self.temp_files_input_dir = os.path.join(self.base_temp_dir, "unzipped_input_files") # 解壓縮後的檔案放這裡
        self.temp_db_output_dir = os.path.join(self.base_temp_dir, "db_output") # DB 和 format_map 放這裡
        self.temp_processing_dir = os.path.join(self.base_temp_dir, "processing_temp") # 給 run_pipeline_from_queue 的 processing_temp_dir

        os.makedirs(self.temp_files_input_dir, exist_ok=True)
        os.makedirs(self.temp_db_output_dir, exist_ok=True)
        os.makedirs(self.temp_processing_dir, exist_ok=True)

        if not os.path.exists(self.sample_zip_file_path):
            self.fail(f"測試 ZIP 檔案 {self.sample_zip_file_path} 不存在。")

        self.extracted_file_paths = []
        with zipfile.ZipFile(self.sample_zip_file_path, 'r') as zip_ref:
            zip_ref.extractall(self.temp_files_input_dir)
            for member in zip_ref.namelist():
                # 確保只添加檔案路徑，排除可能的目錄條目
                extracted_path = os.path.join(self.temp_files_input_dir, member)
                if os.path.isfile(extracted_path):
                     self.extracted_file_paths.append(os.path.abspath(extracted_path))

        self.assertTrue(len(self.extracted_file_paths) > 0, "未能從 sample_pipeline_data.zip 解壓縮任何檔案。")
        # print(f"解壓縮後的檔案路徑: {self.extracted_file_paths}")


    def tearDown(self):
        """測試清理"""
        if hasattr(self, 'base_temp_dir') and os.path.exists(self.base_temp_dir):
            shutil.rmtree(self.base_temp_dir)

    def test_pipeline_run_from_queue(self):
        """
        測試精煉廠的佇列驅動執行流程。
        """
        test_db_name = "test_q_analytics.duckdb"
        db_full_path = os.path.join(self.temp_db_output_dir, test_db_name)
        format_map_path = os.path.join(self.temp_db_output_dir, pipeline_run_module.FORMAT_MAP_FILENAME) # 使用模組中的常數

        # 創建並填充模擬佇列
        task_q = queue.Queue()
        for file_path in self.extracted_file_paths:
            # 模擬 downloader 可能放入的項目格式
            queue_item = {'type': 'file', 'path': file_path, 'source_url': f'test_sample:{os.path.basename(file_path)}'}
            task_q.put(queue_item)

        stop_sentinel_value = "STOP_TESTING_QUEUE"
        task_q.put(stop_sentinel_value)

        hw_settings_for_test = {"max_workers": 2, "memory_limit_gb": 1} # 測試時使用較小資源配置

        # 執行 pipeline 的核心佇列處理函式
        # 需要確保 run.py 中的 logger 在此時能正確初始化或被 mock
        # 假設 run_pipeline_from_queue 會處理 logger 的初始化
        try:
            pipeline_run_module.run_pipeline_from_queue(
                task_queue=task_q,
                db_file_path=db_full_path,
                format_map_path=format_map_path,
                processing_temp_dir=self.temp_processing_dir,
                hw_settings=hw_settings_for_test,
                stop_sentinel=stop_sentinel_value
            )
        except Exception as e_run_pipeline:
            # 捕獲執行期間的任何例外，以便調試
            # 這裡不應該有來自 run_pipeline_from_queue 本身的未處理例外，它應該內部 try-except
            self.fail(f"執行 run_pipeline_from_queue 時發生未預期錯誤: {e_run_pipeline}")


        # 1. 驗證 format_map.json 是否已建立且包含內容
        self.assertTrue(os.path.exists(format_map_path), f"格式地圖 {format_map_path} 未建立。")
        try:
            with open(format_map_path, 'r', encoding='utf-8') as f_map:
                format_map_content = json.load(f_map)
            # 預期應有4個檔案的配方 (即使 futures_daily_sample.csv 之前有問題，現在應該也被正確處理了)
            # 或者是3個，如果有一個檔案的內容完全相同導致hash碰撞 (不太可能)
            self.assertTrue(len(format_map_content) >= 3, f"格式地圖應至少包含 {len(self.extracted_file_paths)-1} 個配方，實際: {len(format_map_content)}")
        except Exception as e_map:
            self.fail(f"讀取或解析格式地圖 {format_map_path} 失敗: {e_map}")

        # 2. 驗證 DuckDB 資料庫內容
        self.assertTrue(os.path.exists(db_full_path), f"DuckDB 資料庫檔案 {db_full_path} 未建立。")

        try:
            con = duckdb.connect(database=db_full_path, read_only=True)

            # 預期 daily_ohlc: v1(2) + v2(2) + futures(1) = 5 筆
            res_ohlc_count = con.execute("SELECT COUNT(*) FROM daily_ohlc;").fetchone()
            self.assertIsNotNone(res_ohlc_count)
            self.assertEqual(res_ohlc_count[0], 5, f"daily_ohlc 表記錄數應為5, 實際為 {res_ohlc_count[0]}")

            res_v1_sample = con.execute("SELECT close FROM daily_ohlc WHERE product_id='TXO' AND expiry_month='202201W1' AND strike_price=18000 AND option_type='C' AND trading_date='2022-01-04';").fetchone()
            self.assertIsNotNone(res_v1_sample)
            self.assertEqual(res_v1_sample[0], 190.0) # DuckDB 可能返回 float

            res_futures_sample = con.execute("SELECT close FROM daily_ohlc WHERE product_id='TXF' AND expiry_month='202301' AND trading_date='2023-01-03';").fetchone()
            self.assertIsNotNone(res_futures_sample)
            self.assertEqual(res_futures_sample[0], 16950.0)

            # 預期 institutional_investors: 2 筆
            res_inst_count = con.execute("SELECT COUNT(*) FROM institutional_investors;").fetchone()
            self.assertIsNotNone(res_inst_count)
            self.assertEqual(res_inst_count[0], 2, f"institutional_investors 表記錄數應為2, 實際為 {res_inst_count[0]}")

            res_inst_sample_fut = con.execute("SELECT long_pos_vol FROM institutional_investors WHERE product_name='臺股期貨' AND investor_type='外資' AND data_date='2023-01-03' AND instrument_type='Future';").fetchone()
            self.assertIsNotNone(res_inst_sample_fut)
            self.assertEqual(res_inst_sample_fut[0], 10000)

            con.close()
        except Exception as e_db:
            self.fail(f"檢查 DuckDB 內容時發生錯誤: {e_db}")

if __name__ == "__main__":
    # 確保在測試環境中，apps 目錄位於 PYTHONPATH，以便 `from taifex_data_pipeline import run` 能工作
    # 這已在頂部的路徑校正中處理
    unittest.main(argv=['first-arg-is-ignored'], exit=False)
