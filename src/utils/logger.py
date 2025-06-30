import logging
import sys

def setup_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """
    設定並返回一個日誌記錄器 Logger。

    Args:
        name (str): Logger 的名稱。
        level (int, optional): Logger 的級別。預設為 logging.INFO。

    Returns:
        logging.Logger: 配置好的 Logger 物件。
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # 關鍵：防止重複添加 handler，避免日誌重複輸出
    if not logger.handlers:
        # 建立一個 StreamHandler，將日誌輸出到標準輸出 (sys.stdout)
        handler = logging.StreamHandler(sys.stdout)

        # 建立一個 Formatter 並設定日誌格式
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - [%(levelname)s] - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S' # 自訂日期時間格式
        )

        # 將 formatter 賦予 handler
        handler.setFormatter(formatter)

        # 將 handler 加入 logger
        logger.addHandler(handler)

    return logger

if __name__ == '__main__':
    # 測試 logger 功能
    test_logger_main = setup_logger("MainModuleLogger_Test")
    test_logger_main.info("這是來自 MainModuleLogger 的 INFO 級別日誌。")
    test_logger_main.warning("這是來自 MainModuleLogger 的 WARNING 級別日誌。")

    test_logger_util = setup_logger("UtilModuleLogger_Test", level=logging.DEBUG)
    test_logger_util.debug("這是來自 UtilModuleLogger 的 DEBUG 級別日誌。")
    test_logger_util.info("這是 UtilModuleLogger 的 INFO，應該也會顯示。")

    # 模擬重複呼叫，驗證 handler 不會重複添加
    test_logger_main_again = setup_logger("MainModuleLogger_Test")
    test_logger_main_again.info("再次呼叫 MainModuleLogger，日誌不應重複。")

    print(f"MainModuleLogger_Test handlers: {test_logger_main.handlers}")
    print(f"UtilModuleLogger_Test handlers: {test_logger_util.handlers}")
