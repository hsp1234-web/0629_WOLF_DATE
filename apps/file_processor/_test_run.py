# -*- coding: utf-8 -*-
"""
整合測試腳本 for apps.file_processor.run

此腳本旨在驗證 file_processor 應用的核心端到端流程：
1.  解析命令列參數。
2.  掃描指定的輸入目錄。
3.  為目錄中的檔案（例如模擬的 TXO 日報表）選擇合適的解析器 (TXODailyParser)。
4.  使用解析器解析檔案。
5.  使用 DBManager 將解析後的數據儲存到 DuckDB 資料庫。
6.  檢查資料庫中是否已成功寫入數據。
7.  檢查已處理的檔案是否被正確移動到 'processed' 子目錄。
"""
import unittest
import subprocess
import os
import sys
import duckdb
import shutil
import pandas as pd
from datetime import datetime

# --- 設定專案路徑 ---
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 假設 TXODailyParser 和 DBManager 的路徑
# from apps.file_processor.parsers.txo_daily_parser import TXODailyParser
# from apps.file_processor.db_manager import DBManager

class TestFileProcessorRun(unittest.TestCase):
    """
    測試 file_processor.run.py 腳本的整合案例。
    """
    BASE_TEST_DIR = os.path.join(PROJECT_ROOT, "data_workspace", "temp_file_processor_tests")
    TEST_INPUT_DIR = os.path.join(BASE_TEST_DIR, "test_uploads")
    TEST_DB_NAME = "test_file_processor_integration.duckdb"
    TEST_DB_PATH = os.path.join(BASE_TEST_DIR, "dbs", TEST_DB_NAME)

    # 解析器處理後檔案會被移到的子目錄
    PROCESSED_SUBDIR = "processed_test" # 與 run.py 中的 PROCESSED_DIR_NAME 不同，以避免衝突
    FAILED_SUBDIR = "failed_test"       # 與 run.py 中的 FAILED_DIR_NAME 不同

    MOCK_OPTIONS_DAILY_FILENAME = "mock_taifex_OptionsDaily_20231018.csv" # 符合 run.py 中的 PARSER_MAPPING 關鍵字

    @classmethod
    def setUpClass(cls):
        """測試類別設定，在所有測試前執行一次。"""
        print(f"INFO [TestFileProcessorRun]: 設定測試環境於 {cls.BASE_TEST_DIR}...")
        # 清理並重建測試目錄結構
        if os.path.exists(cls.BASE_TEST_DIR):
            shutil.rmtree(cls.BASE_TEST_DIR)
        os.makedirs(cls.TEST_INPUT_DIR, exist_ok=True)
        os.makedirs(os.path.join(cls.TEST_INPUT_DIR, cls.PROCESSED_SUBDIR), exist_ok=True)
        os.makedirs(os.path.join(cls.TEST_INPUT_DIR, cls.FAILED_SUBDIR), exist_ok=True)
        os.makedirs(os.path.dirname(cls.TEST_DB_PATH), exist_ok=True)

        # 準備一個模擬的 TXO 選擇權日報表檔案
        cls.create_mock_txo_csv(os.path.join(cls.TEST_INPUT_DIR, cls.MOCK_OPTIONS_DAILY_FILENAME))

    @classmethod
    def tearDownClass(cls):
        """測試類別清理，在所有測試後執行一次。"""
        print(f"INFO [TestFileProcessorRun]: 清理測試環境 {cls.BASE_TEST_DIR}...")
        if os.path.exists(cls.BASE_TEST_DIR):
            shutil.rmtree(cls.BASE_TEST_DIR)

    @staticmethod
    def create_mock_txo_csv(file_path: str):
        """建立一個模擬的 TXO 選擇權 CSV 檔案 (Big5 編碼)。"""
        # 內容來自 txo_daily_parser.py 的測試範例，確保欄位和格式能被解析
        # 注意：run.py 中的 parser mapping 是基於檔名關鍵字 "OptionsDaily"
        header = "交易日期,契約,到期月份(週別),履約價,買賣權,開盤價,最高價,最低價,收盤價,結算價,成交量,未沖銷契約量\n"
        data_lines = [
            "2023/11/01,TXO,202311W1,17000,買權,10.5,12,8.0,9.5,9.5,\"1,234\",500\n",
            "2023/11/01,TXO,202311,17000,賣權,-,-,-,-,80.0,0,300\n", # 有 '-' 代表無開盤/高/低/收
            "2023/11/02,TXO,202312W5,17500,買權,5,5,5,5,5,10,20\n"
        ]
        # 增加一個應該解析失敗的行 (例如日期格式錯誤)
        # data_lines.append("WRONGDATE,TXO,202312,18000,買權,1,1,1,1,1,1,1\n")

        full_content = header + "".join(data_lines)
        try:
            with open(file_path, "w", encoding="big5") as f:
                f.write(full_content)
            print(f"INFO [TestFileProcessorRun]: 已建立模擬檔案: {file_path}")
        except Exception as e:
            print(f"錯誤 [TestFileProcessorRun]: 建立模擬檔案 {file_path} 失敗: {e}")
            raise

    def test_run_script_success_options_daily_file(self):
        """
        測試 file_processor.run.py 處理一個選擇權日報表檔案。
        驗證：
        1. 腳本成功執行 (返回碼 0)。
        2. 數據被正確解析並寫入資料庫。
        3. 原始檔案被移動到 "processed" 子目錄。
        """
        run_py_path = os.path.join(PROJECT_ROOT, "apps", "file_processor", "run.py")

        cmd = [
            sys.executable,
            run_py_path,
            "--input-dir", self.TEST_INPUT_DIR,
            "--db-path", self.TEST_DB_PATH,
            "--processed-subdir", self.PROCESSED_SUBDIR, # 使用測試用的子目錄名
            "--failed-subdir", self.FAILED_SUBDIR
        ]

        print(f"INFO [TestFileProcessorRun]: 執行檔案處理: {' '.join(cmd)}")
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
        stdout, stderr = process.communicate(timeout=60)

        print(f"INFO [TestFileProcessorRun]: STDOUT:\n{stdout}")
        if stderr:
            print(f"ERROR [TestFileProcessorRun]: STDERR:\n{stderr}")

        self.assertEqual(process.returncode, 0, f"file_processor.run.py 執行失敗，返回碼 {process.returncode}")

        # 1. 驗證檔案是否被移動
        original_file_path = os.path.join(self.TEST_INPUT_DIR, self.MOCK_OPTIONS_DAILY_FILENAME)
        processed_file_path = os.path.join(self.TEST_INPUT_DIR, self.PROCESSED_SUBDIR, self.MOCK_OPTIONS_DAILY_FILENAME)

        self.assertFalse(os.path.exists(original_file_path), f"原始檔案 {original_file_path} 未被移動。")
        self.assertTrue(os.path.exists(processed_file_path), f"檔案未移動到處理後目錄 {processed_file_path}。")
        print(f"INFO [TestFileProcessorRun]: 確認檔案已移動到 {processed_file_path}")

        # 2. 驗證資料庫中是否有數據
        #    TXODailyParser 的 target_table_name 是 "taifex_options_daily"
        target_table = "taifex_options_daily"
        self.assertTrue(os.path.exists(self.TEST_DB_PATH), f"測試資料庫檔案 {self.TEST_DB_PATH} 未建立。")

        try:
            with duckdb.connect(self.TEST_DB_PATH, read_only=True) as con:
                # 檢查資料表是否存在
                table_check = con.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{target_table}';").fetchone()
                self.assertIsNotNone(table_check, f"資料表 '{target_table}' 未在測試資料庫中建立。")

                # 檢查是否有數據被插入 (模擬檔案有3行有效數據)
                count_result = con.execute(f"SELECT COUNT(*) FROM \"{target_table}\"").fetchone()
                self.assertIsNotNone(count_result, "無法查詢測試資料庫中的數據。")
                # 預期模擬檔案中的3行數據都被解析並儲存
                self.assertEqual(count_result[0], 3, f"資料庫中 '{target_table}' 的數據筆數 ({count_result[0]}) 與預期 (3) 不符。")
                print(f"INFO [TestFileProcessorRun]: 資料庫中 '{target_table}' 有 {count_result[0]} 筆數據，符合預期。")

                # 可以進一步抽查數據內容是否正確 (例如某個特定欄位的值)
                # 例如，檢查第一筆數據的 TradeDate 和 StrikePrice
                sample_data = con.execute(f"SELECT \"TradeDate\", \"StrikePrice\", \"OptionType\", \"Volume\" FROM \"{target_table}\" ORDER BY \"TradeDate\", \"StrikePrice\" LIMIT 1").df()
                # print("Sample data from DB:\n", sample_data)
                self.assertEqual(sample_data["TradeDate"][0], pd.Timestamp('2023-11-01').date())
                self.assertEqual(sample_data["StrikePrice"][0], 17000.0)
                self.assertEqual(sample_data["OptionType"][0], "買權") # TXODailyParser 裡 map 成 Call 了
                                                                    # 不對，parser 裡是 {'買權': 'Call', '賣權': 'Put'}
                                                                    # 所以這裡應該是 'Call'
                # 修正：TXODailyParser 確實將 '買權' 轉為 'Call'
                self.assertEqual(sample_data["OptionType"][0], 'Call')
                self.assertEqual(sample_data["Volume"][0], 1234)


        except Exception as e:
            self.fail(f"連接或查詢測試資料庫 {self.TEST_DB_PATH} 時發生錯誤: {e}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}")


    def test_run_script_unsupported_file(self):
        """
        測試當輸入目錄中存在無法被任何解析器處理的檔案時，腳本的行為。
        預期：
        1. 腳本成功執行。
        2. 不支持的檔案被移動到 "failed" (或 "skipped") 子目錄。
        3. 資料庫中不應有此檔案的數據。
        """
        unsupported_filename = "unsupported_file_type.txt"
        unsupported_file_path = os.path.join(self.TEST_INPUT_DIR, unsupported_filename)
        with open(unsupported_file_path, "w", encoding="utf-8") as f:
            f.write("This is a test file that should not be processed.")

        run_py_path = os.path.join(PROJECT_ROOT, "apps", "file_processor", "run.py")
        cmd = [
            sys.executable, run_py_path,
            "--input-dir", self.TEST_INPUT_DIR,
            "--db-path", self.TEST_DB_PATH,
            "--processed-subdir", self.PROCESSED_SUBDIR,
            "--failed-subdir", self.FAILED_SUBDIR
        ]

        print(f"INFO [TestFileProcessorRun]: 執行不支持檔案類型測試: {' '.join(cmd)}")
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
        stdout, stderr = process.communicate(timeout=30)

        print(f"INFO [TestFileProcessorRun]: STDOUT (unsupported file):\n{stdout}")
        if stderr: print(f"ERROR [TestFileProcessorRun]: STDERR (unsupported file):\n{stderr}")

        self.assertEqual(process.returncode, 0, "腳本執行失敗 (unsupported file test)。")

        # 驗證檔案是否被移動到 "failed" 或 "skipped" 目錄
        # run.py 中，未找到解析器的檔案會被移動到 failed_path 並加上 .skipped_timestamp 後綴
        failed_dir_path = os.path.join(self.TEST_INPUT_DIR, self.FAILED_SUBDIR)

        # 檢查 failed_dir_path 中是否有以 unsupported_filename + ".skipped_" 開頭的檔案
        moved_files = [f for f in os.listdir(failed_dir_path) if f.startswith(unsupported_filename + ".skipped_")]
        self.assertTrue(len(moved_files) > 0, f"不支持的檔案未移動到失敗目錄 {failed_dir_path} 或命名不符。")

        self.assertFalse(os.path.exists(unsupported_file_path), "原始不支持檔案未被移動。")
        print(f"INFO [TestFileProcessorRun]: 確認不支持的檔案已移動到失敗目錄。")

        # 清理這個不支持的檔案，避免影響其他測試 (如果 test case 不是獨立執行的話)
        if moved_files:
            os.remove(os.path.join(failed_dir_path, moved_files[0]))


if __name__ == '__main__':
    unittest.main(verbosity=2)
