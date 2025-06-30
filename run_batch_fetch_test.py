# 檔案: run_batch_fetch_test.py
# 目的: 快速、批量地測試核心金融數據的 API 抓取能力，並將結果直接存為 CSV 檔案。

import os
import sys
import logging
import yfinance as yf
from fredapi import Fred
from datetime import datetime, timedelta

# --- 路徑自我校正 ---
try:
    project_root = os.path.dirname(os.path.abspath(__file__))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from src.utils.logger import setup_logger
    print("[Setup] Batch fetch test modules imported successfully.")
except ImportError as e:
    print(f"[FATAL] Failed to import modules: {e}")
    sys.exit(1)

def run_batch_test():
    """執行批量數據抓取測試"""
    log_file_path = os.path.join(project_root, "data_workspace", "logs", f"batch_fetch_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    logger = setup_logger("BatchFetchTest", log_file_path_str=log_file_path)
    logger.info("="*50)
    logger.info("Starting Batch Data Fetch Test")
    logger.info("="*50)

    # --- 測試目標清單 (根據您的文件和分析報告的重要性) ---
    # yfinance (免費，不需金鑰)
    YFINANCE_TARGETS = {
        'VIX': '^VIX',
        'MOVE': '^MOVE',
        'SP500': '^GSPC',
    }

    # FRED (需要金鑰)
    FRED_TARGETS = {
        'SOFR': 'SOFR',
        'EFFR': 'EFFR',
        'US_10Y_Treasury': 'DGS10',
        'US_2Y_Treasury': 'DGS2',
        'FED_Funds_Rate': 'FEDFUNDS',
    }

    # --- 執行 yfinance 測試 ---
    logger.info("--- Testing yfinance Tickers (No API Key needed) ---")
    output_dir = os.path.join(project_root, 'data_workspace', 'raw_data')
    os.makedirs(output_dir, exist_ok=True)

    for name, ticker in YFINANCE_TARGETS.items():
        try:
            logger.info(f"Fetching {name} ({ticker})...")
            data_df = yf.download(ticker, period="5y", progress=False)
            if data_df.empty:
                logger.error(f"FAILED to fetch {name}. API returned empty data.")
                continue

            file_path = os.path.join(output_dir, f"{name}.csv")
            data_df.to_csv(file_path)
            logger.info(f"SUCCESS: Saved {name} data to {file_path}")

        except Exception as e:
            logger.error(f"FAILED to fetch {name} with an exception: {e}", exc_info=True)

    # --- 執行 FRED 測試 ---
    logger.info("--- Testing FRED Series (API Key required) ---")
    fred_api_key = os.getenv('API_KEY_FRED')
    if not fred_api_key:
        logger.critical("SKIPPING FRED TESTS: Environment variable 'API_KEY_FRED' is not set.")
        return 1 # 返回錯誤碼表示部分測試未執行

    fred = Fred(api_key=fred_api_key)
    for name, series_id in FRED_TARGETS.items():
        try:
            logger.info(f"Fetching {name} ({series_id})...")
            data_series = fred.get_series(series_id)
            if data_series.empty:
                logger.error(f"FAILED to fetch {name}. API returned empty data.")
                continue

            file_path = os.path.join(output_dir, f"{name}.csv")
            data_series.to_csv(file_path)
            logger.info(f"SUCCESS: Saved {name} data to {file_path}")

        except Exception as e:
            logger.error(f"FAILED to fetch {name} with an exception: {e}", exc_info=True)

    logger.info("Batch Data Fetch Test Completed.")
    return 0

if __name__ == "__main__":
    exit_code = run_batch_test()
    if exit_code == 0:
        print("\n[SUCCESS] Batch fetch test finished. Please check the 'data_workspace/raw_data/' directory for CSV files.")
    else:
        print("\n[WARNING] Batch fetch test finished with warnings or skipped tests. Check logs for details.")
    sys.exit(exit_code)
