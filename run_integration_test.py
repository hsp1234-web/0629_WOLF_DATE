# 檔案: run_integration_test.py
# 目的: 執行核心數據管道的端到端整合測試。

import os
import sys
import logging # logging 模組本身還是需要的
from datetime import datetime, timedelta

# --- 路徑自我校正 ---
try:
    project_root = os.path.dirname(os.path.abspath(__file__))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    # 使用 print 進行早期階段的導入成功/失敗提示，因為 logger 可能尚未完全設定
    print(f"[INFO][Setup] Project root for integration_test.py: '{project_root}'")

    from src.utils.logger import setup_logger
    print("[INFO][Setup] Successfully imported 'setup_logger'.")

    from apps.stress_report_app import data_fetcher, calculator
    print("[INFO][Setup] Successfully imported 'data_fetcher' and 'calculator'.")

    from src.database.duckdb_repository import DuckDBRepository
    print("[INFO][Setup] Successfully imported 'DuckDBRepository'.")

    print("[INFO][Setup] Integration test modules imported successfully.")

except ImportError as e:
    print(f"[FATAL][Setup] Failed to import modules for integration_test.py: {e}")
    print(f"[FATAL][Setup] Current sys.path: {sys.path}")
    print(f"[FATAL][Setup] Current working directory: {os.getcwd()}")
    sys.exit(1)

def run_test():
    """執行數據管道整合測試"""
    log_file_name = f"integration_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    log_file_path = os.path.join(project_root, "data_workspace", "logs", log_file_name)
    logger = setup_logger("IntegrationTest", log_file_path_str=log_file_path, level=logging.DEBUG) # Use DEBUG for more details

    logger.info("="*50)
    logger.info("PHASE 1: Starting Core Data Pipeline Test")
    logger.info(f"Log file for this test: {log_file_path}")
    logger.info("="*50)

    # 1. 準備測試參數 (小範圍)
    end_date_dt = datetime.now().date() # Use datetime.date object for consistency if fetcher expects it
    start_date_dt = end_date_dt - timedelta(days=30)
    config_path = 'config/project_config.yaml'

    # 檢查設定檔是否存在
    full_config_path = os.path.join(project_root, config_path)
    if not os.path.exists(full_config_path):
        logger.critical(f"Configuration file not found at: {full_config_path}. Test cannot proceed.")
        return 1
    logger.debug(f"Using configuration file: {full_config_path}")

    try:
        repo = DuckDBRepository() # 預設資料庫路徑
        logger.debug(f"DuckDBRepository initialized. DB path: {repo.db_path}")
    except Exception as e:
        logger.critical(f"Failed to initialize DuckDBRepository: {e}", exc_info=True)
        return 1

    # 2. 執行數據獲取
    raw_data_df = None
    try:
        logger.info(f"Step 1/3: Fetching data from {start_date_dt.strftime('%Y-%m-%d')} to {end_date_dt.strftime('%Y-%m-%d')}")
        # 確認 data_fetcher.fetch_all_data 接受 date 物件還是字串
        # 假設它接受 date 物件，如果不是，需要轉換 start_date_dt 和 end_date_dt
        raw_data_df = data_fetcher.fetch_all_data(start_date_dt, end_date_dt, config_path)

        if raw_data_df is None: # 檢查 None
            logger.error("Data fetching returned None. This is unexpected. Test failed.")
            return 1
        if raw_data_df.empty:
            logger.warning("Data fetching returned an empty dataframe. This might be an issue or expected for the date range/config. Continuing calculation step to check for errors.")
            # 不直接失敗，讓計算步驟去處理空 dataframe，看是否報錯
        else:
            logger.info(f"Data fetching successful. Shape of raw_data_df: {raw_data_df.shape}")
        logger.debug(f"Raw data columns: {raw_data_df.columns.tolist() if not raw_data_df.empty else 'N/A (empty df)'}")

    except Exception as e:
        logger.critical(f"Data fetching (Step 1/3) failed with an exception: {e}", exc_info=True)
        return 1

    # 3. 執行指標計算
    calculated_df = None
    try:
        logger.info("Step 2/3: Calculating stress metrics...")
        if raw_data_df.empty:
            logger.warning("Skipping metrics calculation as raw_data_df is empty. This might indicate an issue in data fetching or an expected lack of data for the period.")
            # 根據需求，這裡可以選擇創建一個空的帶有預期欄位的 calculated_df，或者直接返回成功/失敗
            # 為了測試儲存邏輯，我們允許一個空的 (或帶有結構的空) DataFrame 被傳遞
            # 假設 calculator.calculate_all_metrics 可以處理空的 DataFrame 並返回一個結構正確的空 DataFrame
            # 或者，如果它應該報錯，那麼這個 try-except 會捕獲它

        calculated_df = calculator.calculate_all_metrics(raw_data_df, config_path) # raw_data_df 可能是空的

        if calculated_df is None: # 檢查 None
             logger.error("Metrics calculation returned None. This is unexpected. Test failed.")
             return 1
        if calculated_df.empty and not raw_data_df.empty: # 如果原始數據非空，但計算結果為空，可能是一個問題
            logger.warning("Metrics calculation returned an empty dataframe, but raw data was not empty. This might be an issue.")
        elif calculated_df.empty and raw_data_df.empty:
            logger.info("Metrics calculation correctly handled an empty raw_data_df and returned an empty calculated_df.")
        else:
            logger.info(f"Metrics calculation successful. Shape of calculated_df: {calculated_df.shape}")
        logger.debug(f"Calculated data columns: {calculated_df.columns.tolist() if not calculated_df.empty else 'N/A (empty df)'}")

    except Exception as e:
        logger.critical(f"Metrics calculation (Step 2/3) failed with an exception: {e}", exc_info=True)
        return 1

    # 4. 執行數據儲存
    try:
        logger.info("Step 3/3: Saving data to DuckDB...")
        table_name = "master_stress_data_test" # 使用測試特定的表名，避免污染生產表
        logger.info(f"Attempting to save to table: {table_name}")

        if calculated_df.empty:
            logger.warning(f"Calculated dataframe is empty. Attempting to save an empty dataframe with schema (if possible) or skipping save to table '{table_name}'.")
            # DuckDBRepository 的 save_dataframe 應該能處理空 dataframe (可能不寫入任何東西，或創建空表)
            # 這裡的 "成功" 意味著 save_dataframe 沒有拋出異常

        # 確保 repo.save_dataframe 存在並且能處理 if_exists
        if hasattr(repo, 'save_dataframe'):
            repo.save_dataframe(calculated_df, table_name, if_exists="replace") # 用 replace 確保測試冪等性
            logger.info(f"Data saving to table '{table_name}' attempted (inspect DB manually if needed for empty df case).")

            # (可選) 驗證步驟：嘗試讀回少量數據或檢查表是否存在
            if not calculated_df.empty:
                try:
                    conn = repo.get_connection()
                    retrieved_df = conn.execute(f"SELECT * FROM {table_name} LIMIT 5").fetchdf()
                    conn.close()
                    logger.info(f"Successfully retrieved {len(retrieved_df)} rows from '{table_name}' for verification.")
                    if retrieved_df.empty and not calculated_df.empty : # 寫入了非空數據，但讀不出來
                         logger.error(f"Verification failed: Saved non-empty data to '{table_name}' but retrieved an empty set.")
                         # return 1 # 根據嚴格程度決定是否因此失敗
                except Exception as ve:
                    logger.warning(f"Could not verify data in '{table_name}': {ve}")
            else:
                logger.info(f"Skipped direct data verification for empty calculated_df saved to '{table_name}'.")

        else:
            logger.error("DuckDBRepository does not have a 'save_dataframe' method. Data saving step skipped/failed.")
            return 1 # 或者根據實際情況處理

        logger.info("Data saving process completed.")
    except Exception as e:
        logger.critical(f"Data saving (Step 3/3) failed with an exception: {e}", exc_info=True)
        return 1

    logger.info("PHASE 1: Core Data Pipeline Test PASSED.")
    return 0

if __name__ == "__main__":
    # 檢查 API 金鑰 (FRED 是必須的)
    api_key_fred = os.getenv('API_KEY_FRED')
    if not api_key_fred:
        print("[FATAL] Environment variable 'API_KEY_FRED' is not set. This test requires it for data fetching.")
        sys.exit(1)
    else:
        print("[INFO] API_KEY_FRED found.")
        # 不直接打印金鑰值

    # 提示 Gemini 金鑰是可選的，但 data_fetcher 可能會嘗試使用它
    api_key_gemini = os.getenv('API_KEY_GEMINI')
    if not api_key_gemini:
        print("[WARN] Environment variable 'API_KEY_GEMINI' is not set. Some data sources in data_fetcher might be unavailable.")
    else:
        print("[INFO] API_KEY_GEMINI found.")

    exit_code = run_test()

    # Final console messages
    if exit_code == 0:
        print("\n[SUCCESS] Phase 1: Core Data Pipeline Test Completed Successfully.")
    else:
        print("\n[FAILURE] Phase 1: Core Data Pipeline Test Failed. Check logs for details.")

    # Ensure logger flushes
    logger_instance = logging.getLogger("IntegrationTest")
    for handler in logger_instance.handlers:
        handler.flush()
        handler.close()

    sys.exit(exit_code)
```
