# -*- coding: utf-8 -*-
"""
整合測試腳本 for apps.hf_data_ingestor.run

此腳本旨在驗證 hf_data_ingestor 應用的核心端到端流程：
1.  解析命令列參數。
2.  使用 YFinanceClient 抓取數據 (可能會使用快取)。
3.  使用 DBManager 將數據儲存到 DuckDB 資料庫。
4.  檢查資料庫中是否已成功寫入數據。

注意：
- 此測試會實際呼叫 yfinance API (可能受網路和 API 限制影響)。
- 此測試會在本機建立一個暫時的 DuckDB 資料庫檔案。
- 測試完成後，相關的快取檔案和資料庫檔案應被清理。
"""
import unittest
import subprocess
import os
import sys
import duckdb
import shutil
import time

# --- 設定專案路徑，確保 run.py 可以正確匯入其依賴 ---
# 這對於直接執行 _test_run.py 或透過測試框架執行都很重要
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 從 run.py 匯入 (如果需要直接呼叫 main 函數，但通常整合測試會跑 subprocess)
# from apps.hf_data_ingestor import run as hf_ingestor_run

class TestHfDataIngestorRun(unittest.TestCase):
    """
    測試 hf_data_ingestor.run.py 腳本的整合案例。
    """
    TEST_DB_NAME = "test_market_data_integration.duckdb"
    TEST_DB_PATH = os.path.join("data_workspace", "temp_test_dbs", TEST_DB_NAME) # 將測試 DB 放在特定臨時目錄
    TEST_CACHE_DIR_BASE = os.path.join("data_workspace", "cache", "yfinance_integration_test")

    # 使用一個在 yfinance 中通常有效的股票代碼和一個較短的間隔
    # 注意：選擇的 ticker 應該是常見且數據穩定的，以減少因外部因素導致的測試失敗。
    # 例如，SPY (S&P 500 ETF) 通常比單個公司股票更穩定。
    # 為避免 API 請求過於頻繁，測試應盡可能使用快取或 mock。
    # 但這裡作為整合測試，我們允許一次真實的 API call。
    TEST_TICKER = "SPY" # 使用 SPY 作為測試標的，較為穩定
    TEST_INTERVAL = "1d" # 日線數據，較為穩定且資料量適中

    @classmethod
    def setUpClass(cls):
        """
        在所有測試開始前執行一次，用於設定測試環境。
        """
        print(f"INFO [TestHfDataIngestorRun]: 設定測試環境...")
        # 確保測試資料庫的目錄存在
        os.makedirs(os.path.dirname(cls.TEST_DB_PATH), exist_ok=True)
        # 確保測試快取目錄存在 (YFinanceClient 會自行處理，但這裡可以預先建立)
        os.makedirs(cls.TEST_CACHE_DIR_BASE, exist_ok=True)

        # 清理可能存在的舊測試檔案
        cls.clean_up_test_files()

    @classmethod
    def tearDownClass(cls):
        """
        在所有測試結束後執行一次，用於清理測試環境。
        """
        print(f"INFO [TestHfDataIngestorRun]: 清理測試環境...")
        cls.clean_up_test_files()

    @staticmethod
    def clean_up_test_files():
        """
        輔助方法：清理測試產生的檔案。
        """
        # 刪除測試資料庫
        if os.path.exists(TestHfDataIngestorRun.TEST_DB_PATH):
            try:
                os.remove(TestHfDataIngestorRun.TEST_DB_PATH)
                print(f"INFO [TestHfDataIngestorRun]: 已刪除測試資料庫 {TestHfDataIngestorRun.TEST_DB_PATH}")
            except OSError as e:
                print(f"警告 [TestHfDataIngestorRun]: 無法刪除測試資料庫 {TestHfDataIngestorRun.TEST_DB_PATH}: {e}")
                # DuckDB 可能會產生 .wal 檔案，一併嘗試刪除
                wal_file = TestHfDataIngestorRun.TEST_DB_PATH + ".wal"
                if os.path.exists(wal_file):
                    try:
                        os.remove(wal_file)
                        print(f"INFO [TestHfDataIngestorRun]: 已刪除 WAL 檔案 {wal_file}")
                    except OSError as e_wal:
                         print(f"警告 [TestHfDataIngestorRun]: 無法刪除 WAL 檔案 {wal_file}: {e_wal}")


        # 刪除測試快取目錄
        # 注意：YFinanceClient 預設的快取路徑是 data_workspace/cache/yfinance
        # 如果 run.py 中 YFinanceClient 的實例化沒有指定不同的 cache_dir,
        # 則這裡需要清理的是預設路徑下的相關檔案，或者在執行 run.py 時傳遞不同的快取路徑。
        # 為了隔離，YFinanceClient 應該允許配置 cache_dir。
        # 假設 YFinanceClient 的快取目錄是基於 ticker 和 interval 建立的，
        # 我們可以嘗試刪除特定的快取檔案或整個測試快取目錄。
        # 為了簡單起見，如果 YFinanceClient 總是寫入其預設的 cache_dir，
        # 我們可能需要更精確地定位測試產生的快取檔案。
        # 更好的做法是在測試時讓 run.py 使用一個完全獨立的快取目錄。
        # 不過，由於 run.py 目前沒有參數來設定 cache_dir，
        # yfinance_client.py 中的 cache_dir 是固定的 "data_workspace/cache/yfinance"。
        # 我們將清理這個目錄下由測試 Ticker 產生的快取。

        default_client_cache_dir = os.path.join(PROJECT_ROOT, "data_workspace", "cache", "yfinance")
        test_cache_file_name = f"{TestHfDataIngestorRun.TEST_TICKER.replace('^', '')}_{TestHfDataIngestorRun.TEST_INTERVAL}.parquet"
        test_cache_file_path = os.path.join(default_client_cache_dir, test_cache_file_name)

        if os.path.exists(test_cache_file_path):
            try:
                os.remove(test_cache_file_path)
                print(f"INFO [TestHfDataIngestorRun]: 已刪除測試快取檔案 {test_cache_file_path}")
            except OSError as e:
                print(f"警告 [TestHfDataIngestorRun]: 無法刪除測試快取檔案 {test_cache_file_path}: {e}")

        # 如果測試中 YFinanceClient 被修改為可配置 cache_dir，則可以清理 TEST_CACHE_DIR_BASE
        # if os.path.exists(TestHfDataIngestorRun.TEST_CACHE_DIR_BASE):
        #     shutil.rmtree(TestHfDataIngestorRun.TEST_CACHE_DIR_BASE)
        #     print(f"INFO [TestHfDataIngestorRun]: 已刪除測試快取目錄 {TestHfDataIngestorRun.TEST_CACHE_DIR_BASE}")


    def test_run_script_success_and_data_ingested(self):
        """
        測試 hf_data_ingestor.run.py 腳本能否成功執行，
        並將數據寫入指定的 DuckDB 資料庫。
        """
        run_py_path = os.path.join(PROJECT_ROOT, "apps", "hf_data_ingestor", "run.py")

        # 第一次執行，應該會從 API 抓取並建立快取
        cmd_fetch = [
            sys.executable,  # 使用當前的 Python 解釋器
            run_py_path,
            "--tickers", self.TEST_TICKER,
            "--interval", self.TEST_INTERVAL,
            "--db-path", self.TEST_DB_PATH
        ]

        print(f"INFO [TestHfDataIngestorRun]: 執行第一次數據抓取: {' '.join(cmd_fetch)}")
        process_fetch = subprocess.Popen(cmd_fetch, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout_fetch, stderr_fetch = process_fetch.communicate(timeout=120) # 設定超時以防 API 卡住

        print(f"INFO [TestHfDataIngestorRun]: 第一次抓取 STDOUT:\n{stdout_fetch}")
        if stderr_fetch:
            print(f"ERROR [TestHfDataIngestorRun]: 第一次抓取 STDERR:\n{stderr_fetch}")

        self.assertEqual(process_fetch.returncode, 0, f"hf_data_ingestor.run.py 首次執行失敗，返回碼 {process_fetch.returncode}")

        # 驗證資料庫中是否有數據
        self.assertTrue(os.path.exists(self.TEST_DB_PATH), f"測試資料庫檔案 {self.TEST_DB_PATH} 未建立。")

        try:
            with duckdb.connect(self.TEST_DB_PATH, read_only=True) as con:
                # 檢查資料表是否存在
                table_check = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='futures_ohlcv';").fetchone()
                self.assertIsNotNone(table_check, "資料表 'futures_ohlcv' 未在測試資料庫中建立。")

                # 檢查是否有 TEST_TICKER 的數據被插入
                count_result = con.execute(f"SELECT COUNT(*) FROM futures_ohlcv WHERE ticker = '{self.TEST_TICKER}'").fetchone()
                self.assertIsNotNone(count_result, "無法查詢測試資料庫中的數據。")
                self.assertGreater(count_result[0], 0, f"資料庫中未找到 ticker '{self.TEST_TICKER}' 的數據。")
                print(f"INFO [TestHfDataIngestorRun]: 第一次抓取後，資料庫中 '{self.TEST_TICKER}' 有 {count_result[0]} 筆數據。")
        except Exception as e:
            self.fail(f"連接或查詢測試資料庫 {self.TEST_DB_PATH} 時發生錯誤: {e}")

        # 為了確保測試的獨立性和可重複性，通常在每次測試運行前清理快取。
        # 但這裡我們想測試快取是否被使用。
        # yfinance_client.py 中的快取過期時間預設為1小時。
        # 如果我們立即再次運行，它應該會使用快取。

        # 短暫停頓，確保檔案系統操作完成
        time.sleep(1)

        # 第二次執行，應該會從快取讀取 (如果快取邏輯正確且未過期)
        # 為了確保測試快取，我們可以在 YFinanceClient 中將快取過期時間設得很長，
        # 或者在測試前手動放置一個"過期"的快取，然後檢查它是否被刷新。
        # 這裡假設快取有效，且 yfinance_client.py 中的快取邏輯會打印 "從快取讀取"
        print(f"INFO [TestHfDataIngestorRun]: 執行第二次數據抓取 (應使用快取): {' '.join(cmd_fetch)}")
        process_cache = subprocess.Popen(cmd_fetch, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout_cache, stderr_cache = process_cache.communicate(timeout=60)

        print(f"INFO [TestHfDataIngestorRun]: 第二次抓取 STDOUT:\n{stdout_cache}")
        if stderr_cache:
            print(f"ERROR [TestHfDataIngestorRun]: 第二次抓取 STDERR:\n{stderr_cache}")

        self.assertEqual(process_cache.returncode, 0, f"hf_data_ingestor.run.py 第二次執行 (快取) 失敗，返回碼 {process_cache.returncode}")

        # 檢查 STDOUT 中是否包含從快取讀取的訊息
        self.assertIn(f"從快取讀取 {self.TEST_TICKER}", stdout_cache, "第二次執行未從快取讀取數據 (未找到預期訊息)。")
        print(f"INFO [TestHfDataIngestorRun]: 第二次抓取確認使用了快取。")

        # 再次驗證資料庫中的數據量是否一致 (因為是 upsert，重複執行不應導致數據量劇增)
        try:
            with duckdb.connect(self.TEST_DB_PATH, read_only=True) as con:
                count_result_after_cache = con.execute(f"SELECT COUNT(*) FROM futures_ohlcv WHERE ticker = '{self.TEST_TICKER}'").fetchone()
                self.assertIsNotNone(count_result_after_cache)
                # 數據量可能因 yfinance 返回的數據在兩次調用間略有更新而有微小變化 (如果 period 不是固定的歷史範圍)
                # 但對於 '1d' 和 '7d' period，通常變化不大或不變。
                # 這裡我們檢查數據量是否與第一次抓取後大致相同或略多 (如果剛好跨日更新)。
                # 為了更穩定的測試，可以比較欄位數量等。
                # 由於 upsert 機制，如果數據完全相同，筆數應該不變。
                self.assertEqual(count_result_after_cache[0], count_result[0],
                                 f"第二次執行後數據筆數 ({count_result_after_cache[0]}) 與第一次 ({count_result[0]}) 不一致。")
                print(f"INFO [TestHfDataIngestorRun]: 第二次抓取後，資料庫中 '{self.TEST_TICKER}' 數據筆數仍為 {count_result_after_cache[0]}，符合預期。")
        except Exception as e:
            self.fail(f"第二次查詢測試資料庫 {self.TEST_DB_PATH} 時發生錯誤: {e}")


    def test_run_script_invalid_ticker(self):
        """
        測試使用無效的 ticker 執行腳本時，腳本應能優雅處理並退出。
        """
        run_py_path = os.path.join(PROJECT_ROOT, "apps", "hf_data_ingestor", "run.py")
        invalid_ticker = "THIS_IS_A_COMPLETELY_INVALID_TICKER_XYZ123"

        cmd_invalid = [
            sys.executable,
            run_py_path,
            "--tickers", invalid_ticker,
            "--interval", self.TEST_INTERVAL,
            "--db-path", self.TEST_DB_PATH # 仍然使用測試 DB，但不應寫入數據
        ]

        print(f"INFO [TestHfDataIngestorRun]: 執行無效 Ticker 數據抓取: {' '.join(cmd_invalid)}")
        process_invalid = subprocess.Popen(cmd_invalid, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout_invalid, stderr_invalid = process_invalid.communicate(timeout=60)

        print(f"INFO [TestHfDataIngestorRun]: 無效 Ticker STDOUT:\n{stdout_invalid}")
        if stderr_invalid: # yfinance 對於無效 ticker 可能會打一些訊息到 stderr
            print(f"WARNING [TestHfDataIngestorRun]: 無效 Ticker STDERR:\n{stderr_invalid}") # 注意：yfinance 有時會將 "No data found" 訊息打到 stderr

        self.assertEqual(process_invalid.returncode, 0, f"hf_data_ingestor.run.py 使用無效 ticker 執行失敗，返回碼 {process_invalid.returncode}")

        # 驗證 STDOUT 中是否包含警告訊息
        # YFinanceClient 中的警告訊息是 "警告：{ticker} 返回空數據。" 或 "標的 {ticker} 可能不存在或無數據"
        self.assertTrue(
            f"警告: {invalid_ticker} 返回空數據" in stdout_invalid or
            f"INFO: 標的 {invalid_ticker} 可能不存在或無數據" in stdout_invalid or
            f"INFO: {invalid_ticker} 在指定期間内沒有可用的新數據。" in stdout_invalid, # run.py 的訊息
            "使用無效 ticker 執行時，未在 STDOUT 中找到預期的警告訊息。"
        )
        print(f"INFO [TestHfDataIngestorRun]: 無效 Ticker 測試確認了預期的警告訊息。")

        # 驗證資料庫中沒有這個無效 ticker 的數據
        try:
            with duckdb.connect(self.TEST_DB_PATH, read_only=True) as con:
                # 確保表存在 (可能由之前的測試創建)
                table_check = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='futures_ohlcv';").fetchone()
                if table_check: # 只有表存在時才查詢
                    count_result = con.execute(f"SELECT COUNT(*) FROM futures_ohlcv WHERE ticker = '{invalid_ticker}'").fetchone()
                    self.assertIsNotNone(count_result)
                    self.assertEqual(count_result[0], 0, f"資料庫中不應存在無效 ticker '{invalid_ticker}' 的數據。")
                else:
                    # 如果之前的測試沒有運行或失敗，表可能不存在，這也是可接受的，因為無效 ticker 不應導致表被創建。
                    print(f"INFO [TestHfDataIngestorRun]: futures_ohlcv 資料表不存在，這對於無效 ticker 測試是可接受的 (如果這是第一個運行的測試)。")

        except Exception as e:
            self.fail(f"查詢測試資料庫 {self.TEST_DB_PATH} 以驗證無效 ticker 時發生錯誤: {e}")


if __name__ == '__main__':
    # 為了能直接執行此測試腳本：
    # 確保在執行前，PYTHONPATH 包含專案根目錄
    # 或者在腳本開頭如上所示動態修改 sys.path
    print(f"INFO: Current sys.path: {sys.path}")
    print(f"INFO: Project root determined as: {PROJECT_ROOT}")

    # 運行測試
    unittest.main(verbosity=2)
