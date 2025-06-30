# 檔案: run_backfill_robustness_test.py
# 目的: 測試歷史回填邏輯在面對有數據和無數據年份時的穩健性。

import os
import sys
import logging # logging 模組本身還是需要的
import yfinance as yf
from datetime import datetime

# --- 路徑自我校正 ---
try:
    project_root = os.path.dirname(os.path.abspath(__file__))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    print(f"[INFO][Setup] Project root for backfill_robustness_test.py: '{project_root}'")

    from src.utils.logger import setup_logger
    print("[INFO][Setup] Successfully imported 'setup_logger'.")

    print("[INFO][Setup] Backfill robustness test modules imported successfully.")
except ImportError as e:
    print(f"[FATAL][Setup] Failed to import modules for backfill_robustness_test.py: {e}")
    print(f"[FATAL][Setup] Current sys.path: {sys.path}")
    print(f"[FATAL][Setup] Current working directory: {os.getcwd()}")
    sys.exit(1)

def run_robustness_test(year_to_test):
    """對單一年份執行回填測試"""
    log_file_name = f"backfill_test_year_{year_to_test}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    log_file_path = os.path.join(project_root, "data_workspace", "logs", log_file_name)
    # 每個年份的測試使用獨立的 logger 名稱，以防在同一 Python 會話中執行多次時 handler 衝突
    logger_name = f"BackfillTest_{year_to_test}"
    logger = setup_logger(logger_name, log_file_path_str=log_file_path, level=logging.DEBUG) # DEBUG for yfinance details

    logger.info("="*50)
    logger.info(f"PHASE 3: Starting Backfill Robustness Test for year {year_to_test}")
    logger.info(f"Log file for this test: {log_file_path}")
    logger.info("="*50)

    # 測試目標：VIX 指數
    ticker_symbol = "^VIX"
    start_date_str = f"{year_to_test}-01-01"
    end_date_str = f"{year_to_test}-12-31"

    # yfinance 的詳細日誌可能會很多，可以考慮在呼叫 download 前暫時調高 yfinance logger 的級別
    # logging.getLogger('yfinance').setLevel(logging.WARNING) # Or INFO

    try:
        logger.info(f"Attempting to fetch '{ticker_symbol}' for period: {start_date_str} to {end_date_str}...")

        # yfinance download parameters:
        # progress=False: 關閉進度條打印到控制台
        # actions=False: 不下載股息和股票分割數據 (對指數可能無關)
        # auto_adjust=False: 不自動調整開高低收價格 (對指數通常不需要)
        # timeout=30: 設定超時 (秒)
        data_df = yf.download(
            tickers=ticker_symbol,
            start=start_date_str,
            end=end_date_str,
            progress=False,
            actions=False,
            auto_adjust=False, # 通常指數不需要調整
            timeout=30
        )

        if data_df is None: # yfinance 在某些錯誤情況下可能返回 None
            logger.error(f"yf.download returned None for '{ticker_symbol}' in {year_to_test}. This indicates an issue with the download process itself.")
            logger.info(f"PHASE 3: Test for year {year_to_test} FAILED (yf.download returned None).")
            return 1

        if data_df.empty:
            # 這是預期中的「成功」，代表 yfinance 正確地識別了無數據的情況並返回空 DataFrame
            logger.warning(f"SUCCESSFUL yf.download call but NO DATA returned for '{ticker_symbol}' in {year_to_test}. This is an EXPECTED outcome for very old years or periods with no trading.")
            logger.info(f"PHASE 3: Test for year {year_to_test} PASSED (Handled no data gracefully by returning empty DataFrame).")
            return 0 # 視為成功，因為 yfinance API 本身沒有崩潰

        logger.info(f"Successfully fetched {len(data_df)} records for '{ticker_symbol}' in {year_to_test}.")
        logger.debug(f"Data columns: {data_df.columns.tolist()}")
        logger.debug(f"First 3 rows of data for {year_to_test}:\n{data_df.head(3)}")
        logger.info(f"PHASE 3: Test for year {year_to_test} PASSED (Data found and fetched).")
        return 0

    except Exception as e:
        # 任何未預期的異常都視為失敗
        logger.critical(f"Test for year {year_to_test} FAILED with an unexpected exception during yf.download or processing: {e}", exc_info=True)
        return 1
    finally:
        # 確保 logger handlers 被關閉，以便下一個年份的測試（如果以不同方式調用）可以創建新的日誌檔案
        # 雖然 setup_logger 已有冪等性，但良好實踐
        for handler in logger.handlers:
            handler.flush()
            handler.close()
        # logging.shutdown() # 如果這是腳本的絕對末尾，可以考慮，但如果還有其他日誌操作則不應使用


if __name__ == "__main__":
    if len(sys.argv) != 2:
        # 使用 print 因為 logger 可能還沒設定
        print("Usage: python run_backfill_robustness_test.py <year>")
        sys.exit(1)

    year_arg = sys.argv[1]
    try:
        year = int(year_arg)
        if not (1900 <= year <= datetime.now().year + 1): # 簡單的年份範圍檢查
             print(f"Error: Year {year} is out of a reasonable range (1900-{datetime.now().year + 1}).")
             sys.exit(1)

        exit_code = run_robustness_test(year)

        # Final console messages
        if exit_code == 0:
            print(f"\n[SUCCESS] Phase 3: Backfill Robustness Test for year {year} Completed Successfully.")
        else:
            print(f"\n[FAILURE] Phase 3: Backfill Robustness Test for year {year} Failed. Check logs for details.")

        sys.exit(exit_code)
    except ValueError:
        print(f"Error: Year argument '{year_arg}' must be an integer.")
        sys.exit(1)
    except Exception as e_main:
        # 捕獲 run_robustness_test 之外的意外錯誤
        print(f"[FATAL][main] An unexpected error occurred: {e_main}")
        sys.exit(2) # 使用不同的退出碼
```
