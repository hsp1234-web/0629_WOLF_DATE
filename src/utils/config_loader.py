import yaml
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

def load_project_config(path: str) -> Dict[str, Any]:
    """
    載入並解析 YAML 設定檔。

    Args:
        path (str): 設定檔的路徑。

    Returns:
        Dict[str, Any]: 解析後的設定內容 (字典形式)。

    Raises:
        FileNotFoundError: 如果指定的設定檔路徑未找到。
        yaml.YAMLError: 如果設定檔內容不是有效的 YAML 格式。
        TypeError: 如果解析後的設定檔內容不是一個字典。
    """
    logger.debug(f"嘗試從路徑 '{path}' 載入設定檔...")
    try:
        with open(path, 'r', encoding='utf-8') as f:
            config_data = yaml.safe_load(f)

        if not isinstance(config_data, dict):
            err_msg = f"設定檔 '{path}' 的內容不是有效的字典格式。"
            logger.error(err_msg)
            raise TypeError(err_msg)

        logger.info(f"設定檔 '{path}' 載入並驗證成功。")
        return config_data
    except FileNotFoundError:
        logger.error(f"設定檔錯誤：在路徑 '{path}' 未找到設定檔。")
        raise
    except yaml.YAMLError as e:
        logger.error(f"設定檔錯誤：解析 YAML 設定檔 '{path}' 失敗: {e}")
        raise
    except Exception as e:
        logger.error(f"載入設定檔 '{path}' 時發生未預期的錯誤: {e}", exc_info=True)
        raise
