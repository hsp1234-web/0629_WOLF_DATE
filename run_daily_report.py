#
# 檔案: run_daily_report.py
# 目的: 在 Jules 沙箱中，以原子化、線性的方式執行完整的壓力報告生成流程，並包含詳細的錯誤處理。
#
import os
import sys
import argparse
import logging # logging 模組本身還是需要的
from datetime import datetime, timedelta

# --- 路徑自我校正 (關鍵步驟) ---
# 確保無論從何處執行，都能找到專案內的模組。
try:
    # 假設此腳本在專案根目錄，將當前目錄加入 sys.path
    project_root = os.path.dirname(os.path.abspath(__file__))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    # print(f"[INFO][Setup] Project root '{project_root}' added to sys.path.") # 使用 print 供早期調試

    # 導入SOP中定義的標準日誌記錄器
    from src.utils.logger import setup_logger
    # print("[INFO][Setup] Successfully imported 'setup_logger'.")

    # 延後導入 app，先設定好 logger
    # from apps.stress_report_app import app as stress_report_app
except ImportError as e:
    # 在 logger 設定好之前，只能用 print
    print(f"[FATAL][Setup] Failed to import 'setup_logger' or project root issue: {e}")
    print(f"[FATAL][Setup] Current sys.path: {sys.path}")
    print(f"[FATAL][Setup] Current working directory: {os.getcwd()}")
    print(f"[FATAL][Setup] project_root variable: {project_root if 'project_root' in locals() else 'Not defined'}")
    print("[FATAL][Setup] Please ensure this script is in the project root and all '__init__.py' files are in place.")
    sys.exit(1)

# --- 主執行函數 ---
def run_pipeline():
    # 1. 初始化日誌記錄器
    # 根據SOP v4.0，日誌是追蹤和除錯的關鍵
    # project_root 應該已經在上面定義好了
    log_file_name = f"daily_run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    log_file_path = os.path.join(project_root, "data_workspace", "logs", log_file_name)

    # 現在可以安全地使用 setup_logger
    # 注意：如果 setup_logger 內部有 logger.info 關於檔案路徑的訊息，它會被記錄
    logger = setup_logger("DailyReportPipeline", log_file_path_str=log_file_path, level=logging.INFO) # Use INFO for production

    logger.info("="*50)
    logger.info("Starting Daily Report Generation Pipeline in Jules Sandbox")
    logger.info(f"Log file for this run: {log_file_path}")
    logger.info("="*50)

    # 現在導入應用程式模組，因為 logger 已經設定好
    try:
        from apps.stress_report_app import app as stress_report_app
        logger.debug("Successfully imported 'apps.stress_report_app.app'")
    except ImportError as e:
        logger.critical(f"Failed to import 'stress_report_app.app' after logger setup: {e}", exc_info=True)
        return 1


    # 2. 設置與解析參數
    parser = argparse.ArgumentParser(description="Daily Stress Report Generation Pipeline for Jules.")
    parser.add_argument('--days-back', type=int, default=1095, help="從今天回溯的天數，作為分析的開始日期 (約三年)。")
    parser.add_argument('--config-path', type=str, default='config/project_config.yaml', help="設定檔路徑。")
    parser.add_argument('--output-format', type=str, default='html', choices=['html', 'json', 'gsheet'], help="輸出的報告格式。") # Added choices
    parser.add_argument('--use-ai-refine', action='store_true', help="啟用 AI 潤飾功能。")
    parser.add_argument('--log-level', type=str, default='INFO', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], help="設定日誌級別。")

    args = parser.parse_args()

    # 根據參數調整 logger 級別 (如果提供了 --log-level)
    # 這需要在 setup_logger 後再次設定，或者修改 setup_logger 使其能接受 args
    # 簡單起見，如果提供了命令行參數，就更新 logger 實例的級別
    numeric_log_level = getattr(logging, args.log_level.upper(), None)
    if not isinstance(numeric_log_level, int):
        logger.warning(f"Invalid log level: {args.log_level}. Defaulting to INFO.")
    else:
        # 更新所有 handlers 的級別，以及 logger 本身的級別
        logger.setLevel(numeric_log_level)
        for handler in logger.handlers:
            handler.setLevel(numeric_log_level)
        logger.info(f"Log level set to {args.log_level.upper()}")


    # 計算日期範圍
    end_date = datetime.now()
    start_date = end_date - timedelta(days=args.days_back)
    start_date_str = start_date.strftime('%Y-%m-%d')
    end_date_str = end_date.strftime('%Y-%m-%d')

    logger.info("Report Parameters:")
    logger.info(f"  - Date Range: {start_date_str} to {end_date_str}")
    logger.info(f"  - Config File: {args.config_path}")
    logger.info(f"  - Output Format: {args.output_format}")
    logger.info(f"  - AI Refinement: {'Enabled' if args.use_ai_refine else 'Disabled'}")
    logger.info(f"  - Effective Log Level: {logging.getLevelName(logger.getEffectiveLevel())}")


    # 3. 檢查 API 金鑰環境變數
    fred_api_key = os.getenv('API_KEY_FRED')
    gemini_api_key = os.getenv('API_KEY_GEMINI') # Used by stress_report_app internally

    if not fred_api_key:
        logger.critical("CRITICAL ERROR: Environment variable 'API_KEY_FRED' is not set. Cannot proceed.")
        return 1

    logger.info("API_KEY_FRED found.")
    if not gemini_api_key: # stress_report_app 會自行處理 Gemini Key 的缺失
        logger.warning("WARNING: Environment variable 'API_KEY_GEMINI' is not set. AI refinement features in the app might be affected or skipped.")
    else:
        logger.info("API_KEY_GEMINI found (its usage depends on app's internal logic and --use-ai-refine flag).")


    # 4. 模擬 stress_report_app.app.main() 的執行流程
    original_argv = None # Initialize to ensure it's defined
    try:
        original_argv = list(sys.argv) # Make a copy
        sys.argv = [
            'apps/stress_report_app/app.py', # Script name, as expected by argparse in app.py
            '--start-date', start_date_str,
            '--end-date', end_date_str,
            '--config', args.config_path, # Assuming app.py uses '--config'
            '--output-format', args.output_format,
            # '--log-level', args.log_level # Pass log level to the app as well
        ]
        if args.use_ai_refine:
            sys.argv.append('--use-ai-refine')

        # Add log-level to app's arguments if it's not INFO
        # This assumes the app itself can also handle --log-level
        # If not, this can be removed, and the app will use its own default or config.
        if args.log_level.upper() != 'INFO':
             sys.argv.extend(['--log-level', args.log_level.upper()])


        logger.info(f"Invoking stress_report_app.main() with args: {' '.join(sys.argv)}")

        # 呼叫應用容器的主函數
        exit_code = stress_report_app.main() # This should use the modified sys.argv

        if exit_code == 0:
            logger.info("SUCCESS: The stress_report_app pipeline completed successfully.")
        else:
            logger.error(f"FAILURE: The stress_report_app pipeline exited with a non-zero exit code: {exit_code}.")
            # No need to return exit_code here if we want the main script's return to be distinct
            # Or, we can return it to make this script's exit code match the app's.
            # The current plan is to return it.
            return exit_code

    except Exception as e:
        logger.critical(f"CRITICAL ERROR: An unhandled exception occurred while running the pipeline: {e}", exc_info=True)
        return 1 # Indicate critical failure
    finally:
        # 恢復 sys.argv
        if original_argv is not None:
            sys.argv = original_argv
        logger.debug("Restored original sys.argv.")

    return 0 # Success

if __name__ == "__main__":
    # 執行主工作流
    # Note: project_root is defined globally at the script start for pathing.
    # Logger is configured inside run_pipeline().
    # If run_pipeline() itself fails before logger setup (e.g. initial imports),
    # errors will go to stderr via print().

    pipeline_exit_code = run_pipeline()

    # Final messages to console might be useful regardless of logger,
    # as logger might have been for a file.
    # These print statements are for the direct user invoking the script.
    if pipeline_exit_code == 0:
        print("\n[SUCCESS] Daily report generation pipeline finished successfully.")
    else:
        print(f"\n[FAILURE] Daily report generation pipeline failed with exit code {pipeline_exit_code}. Check logs for details.")

    # Ensure logger flushes all handlers, especially file handlers
    if 'logger' in locals() and isinstance(logger, logging.Logger): # Check if logger was initialized
        for handler in logger.handlers:
            handler.flush()
            handler.close() # Good practice to close handlers

    sys.exit(pipeline_exit_code)
```
