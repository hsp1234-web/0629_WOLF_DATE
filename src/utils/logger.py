#
# 檔案: src/utils/logger.py
# 目的: 提供一個中心化的、可同時設定控制台與檔案輸出的日誌記錄器。
#
import logging
import sys
import os # Needed for os.makedirs
from logging.handlers import RotatingFileHandler

def setup_logger(name, log_file_path_str=None, level=logging.INFO):
    """
    設定一個 logger，可選擇性地將日誌輸出到檔案。

    Args:
        name (str): logger 的名稱。
        log_file_path_str (str, optional): 日誌檔案的路徑。若提供，則會啟用檔案日誌。 Defaults to None.
        level (int, optional): 日誌記錄的級別。 Defaults to logging.INFO.

    Returns:
        logging.Logger: 已設定好的 logger 實例。
    """
    # 避免重複添加 handler
    logger = logging.getLogger(name)

    # 檢查是否已經有 handler，如果有，並且日誌級別也相同，則直接返回
    # 這樣可以允許在不同地方用相同的名字和級別獲取logger而不會重複設定或丟失之前的設定
    if logger.hasHandlers() and logger.level == level:
        # 如果提供了新的 log_file_path_str，且之前沒有檔案 handler，或者路徑不同，則可能需要添加
        # 但為了簡化，這裡的邏輯是：一旦設定過，就不再輕易改變 handlers
        # 如果需要動態增減 handler 或改變路徑，需要更複雜的邏輯
        # 此處假設初次設定時決定好 handler

        # 更安全的做法是，如果 logger 已存在但 level 不同，則更新 level
        # 但 handler 的添加應該是冪等的。目前的 if logger.hasHandlers() return logger 策略更簡單。
        # 為了允許後續呼叫（如果首次未設定file handler）能加上 file handler，我們調整一下邏輯：
        # 只在沒有 handler 時設定 level 和 console handler。
        # File handler 可以後續添加（如果提供了 log_file_path_str 且之前未設定過同路徑的 file handler）

        pass # 繼續執行，以便可以按需添加 file handler 或調整


    if not logger.handlers: # 只有在完全沒有 handler 時才設定基礎 level 和 console
        logger.setLevel(level)
        # 建立一個通用的格式化器
        formatter = logging.Formatter('%(asctime)s - %(name)s - [%(levelname)s] - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

        # 1. 設定控制台 Handler (總是啟用)
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)
    else: # logger 已有 handler，獲取其 formatter 以便新的 file handler 使用
        # 假設所有 handler 使用相同的 formatter，取第一個 handler 的 formatter
        formatter = logger.handlers[0].formatter
        # 確保 logger level 與請求的 level 一致（允許調低，但不輕易調高已有的）
        if logger.level > level: # 如果現有 level 更高（更不詳細），則更新為請求的更詳細的 level
            logger.setLevel(level)


    # 2. 設定檔案 Handler (如果提供了路徑)
    if log_file_path_str:
        # 檢查是否已存在相同路徑的 FileHandler，避免重複添加
        has_matching_file_handler = False
        for handler in logger.handlers:
            if isinstance(handler, RotatingFileHandler) and handler.baseFilename == os.path.abspath(log_file_path_str):
                has_matching_file_handler = True
                break

        if not has_matching_file_handler:
            try:
                # 確保日誌檔案所在的目錄存在
                log_dir = os.path.dirname(os.path.abspath(log_file_path_str))
                if not os.path.exists(log_dir):
                    os.makedirs(log_dir, exist_ok=True)
                    # logger.info(f"Log directory created: {log_dir}") # 這條日誌可能在 logger 完全設定好之前發出

                # 使用 RotatingFileHandler 避免日誌檔無限增大
                # 這裡設定每個檔案最大 5MB，保留 5 個備份檔
                file_handler = RotatingFileHandler(
                    log_file_path_str, maxBytes=5*1024*1024, backupCount=5, encoding='utf-8'
                )
                file_handler.setFormatter(formatter) # 使用已有的或新建立的 formatter
                file_handler.setLevel(level) # 確保 file handler 也遵循請求的 level
                logger.addHandler(file_handler)
                # 這條日誌現在應該可以正常工作了
                # logger.info(f"File logging enabled. Log file at: {log_file_path_str}")
                # 為了避免在 setup_logger 內部產生過多日誌，可以考慮由呼叫者記錄此訊息
            except Exception as e:
                # 如果設定檔案日誌失敗，至少控制台日誌還在
                logger.error(f"Failed to set up file handler at {log_file_path_str}: {e}", exc_info=True)
        # else:
            # logger.debug(f"File handler for {log_file_path_str} already exists.")

    return logger

if __name__ == '__main__':
    # 測試 logger 功能
    # 第一次設定，應同時有控制台和檔案
    logger1 = setup_logger("TestApp1", log_file_path_str="logs/app_test1.log", level=logging.DEBUG)
    logger1.debug("Debug message for TestApp1 - to console and file.")
    logger1.info("Info message for TestApp1 - to console and file.")

    # 第二次獲取同一個 logger，不應重複 handler，日誌級別應保持
    logger1_again = setup_logger("TestApp1", log_file_path_str="logs/app_test1.log", level=logging.INFO)
    logger1_again.info("Info message again for TestApp1 - should not have double handlers.")
    logger1_again.debug("Debug message again for TestApp1 - should still appear if level was DEBUG.")


    # 測試只設定控制台
    logger_console_only = setup_logger("ConsoleOnlyApp", level=logging.INFO)
    logger_console_only.info("This message is for console only.")
    # logger_console_only.debug("This debug message for console only should not appear.") #因為 level 是 INFO

    # 測試後續為已有的 console-only logger 添加檔案 handler
    logger_console_only_add_file = setup_logger("ConsoleOnlyApp", log_file_path_str="logs/console_app_now_with_file.log", level=logging.INFO)
    logger_console_only_add_file.info("This message for ConsoleOnlyApp should now also go to file.")
    logger_console_only_add_file.error("An error message for ConsoleOnlyApp - to console and file.")

    # 測試 RotatingFileHandler (手動執行多次並查看 logs/ 目錄)
    logger_rotate = setup_logger("RotateTest", log_file_path_str="logs/rotate_test.log", level=logging.INFO)
    for i in range(10): # 模擬產生一些日誌
        logger_rotate.info(f"Rotation test message {i+1}")

    print("\nLoggers configured. Check 'logs/' directory for output files (app_test1.log, console_app_now_with_file.log, rotate_test.log).")
    print(f"Logger 'TestApp1' handlers: {logging.getLogger('TestApp1').handlers}")
    print(f"Logger 'ConsoleOnlyApp' handlers: {logging.getLogger('ConsoleOnlyApp').handlers}")
    print(f"Logger 'RotateTest' handlers: {logging.getLogger('RotateTest').handlers}")

    # 測試不同 level 的獲取
    logger_info = setup_logger("MultiLevelTest", level=logging.INFO)
    logger_info.info("Info level set for MultiLevelTest.")
    logger_debug_later = setup_logger("MultiLevelTest", level=logging.DEBUG) # 嘗試設定更詳細的 level
    logger_debug_later.debug("Debug level for MultiLevelTest - should appear now.")
    print(f"Logger 'MultiLevelTest' level: {logging.getLogger('MultiLevelTest').level} (expected {logging.DEBUG})")
    print(f"Logger 'MultiLevelTest' handlers: {logging.getLogger('MultiLevelTest').handlers}")

    # 測試路徑不存在時自動建立
    logger_new_dir = setup_logger("NewDirLogger", log_file_path_str="new_log_dir/new_dir_test.log", level=logging.INFO)
    logger_new_dir.info("Testing log creation in a new directory.")
    print(f"Logger 'NewDirLogger' handlers: {logging.getLogger('NewDirLogger').handlers}")
    print("Check for 'new_log_dir/new_dir_test.log'.")

    # 測試設定 file handler 失敗的情況 (例如權限問題，這裡用一個不太可能失敗的路徑模擬)
    # 在沙箱中可能難以模擬真實的權限錯誤，但至少錯誤處理路徑被包含了
    logger_fail_safe = setup_logger("FailSafeLogger", log_file_path_str="/hopefully_non_writable/test.log", level=logging.INFO)
    logger_fail_safe.info("This info message should appear on console even if file logging failed.")
    print(f"Logger 'FailSafeLogger' handlers (should only have StreamHandler if path was bad): {logging.getLogger('FailSafeLogger').handlers}")

# ```
