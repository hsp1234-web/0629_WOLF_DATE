# -*- coding: utf-8 -*-
import argparse
import os
import sys
from datetime import datetime

# 將 downloader.py 所在的目錄加入 sys.path，以便導入
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

try:
    from downloader import perform_download_tasks, SUPPORTED_DOWNLOAD_TYPES, logger
except ImportError:
    # 備用導入路徑，如果 run.py 是從 wolf_analyzer_reborn/apps/ 目錄執行的
    sys.path.insert(0, os.path.join(current_dir, "taifex_data_downloader"))
    from downloader import perform_download_tasks, SUPPORTED_DOWNLOAD_TYPES, logger

try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False
    logger.info("tqdm 模組未找到，將不顯示進度條。可透過 'pip install tqdm' 安裝。")

def main():
    parser = argparse.ArgumentParser(
        description="「採集官」微應用：從臺灣期交所 (TAIFEX) 下載指定日期範圍和類型的公開數據。",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--start-date",
        required=True,
        type=str,
        help="必要參數：下載開始日期 (格式: YYYY-MM-DD)。"
    )
    parser.add_argument(
        "--end-date",
        required=True,
        type=str,
        help="必要參數：下載結束日期 (格式: YYYY-MM-DD)。"
    )
    parser.add_argument(
        "--output-path",
        required=True,
        type=str,
        help="必要參數：下載檔案的根儲存路徑。"
    )

    type_choices_str = "\n可用的下載類型:\n" + "\n".join([f"  - {key}: {val['description']}" for key, val in SUPPORTED_DOWNLOAD_TYPES.items()])
    parser.add_argument(
        "--download-types",
        required=True,
        type=str,
        help=f"必要參數：要下載的資料類型，以逗號分隔 (例如：futures_daily_trades,institutional_investors)。{type_choices_str}"
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="每次下載請求之間的延遲秒數 (預設: 0.5 秒)。"
    )

    args = parser.parse_args()

    # 驗證日期格式
    try:
        datetime.strptime(args.start_date, '%Y-%m-%d')
        datetime.strptime(args.end_date, '%Y-%m-%d')
    except ValueError:
        logger.error("❌ 錯誤：開始日期或結束日期格式不正確，請使用 'YYYY-MM-DD'。")
        sys.exit(1)

    # 解析 download_types
    selected_types = [item.strip() for item in args.download_types.split(',') if item.strip()]
    if not selected_types:
        logger.error("❌ 錯誤：未提供任何有效的下載類型。")
        sys.exit(1)

    # 驗證選擇的類型是否有效
    invalid_types = [stype for stype in selected_types if stype not in SUPPORTED_DOWNLOAD_TYPES]
    if invalid_types:
        logger.error(f"❌ 錯誤：以下為無效的下載類型: {', '.join(invalid_types)}。")
        logger.info(type_choices_str)
        sys.exit(1)

    logger.info("🚀 「採集官」下載任務啟動...")
    logger.info(f"  開始日期: {args.start_date}")
    logger.info(f"  結束日期: {args.end_date}")
    logger.info(f"  輸出路徑: {os.path.abspath(args.output_path)}")
    logger.info(f"  下載類型: {', '.join(selected_types)}")
    logger.info(f"  請求延遲: {args.delay} 秒")

    # 這裡不直接使用 tqdm 遍歷日期，因為 perform_download_tasks 內部會遍歷
    # 我們只調用核心函數
    results = perform_download_tasks(
        args.start_date,
        args.end_date,
        args.output_path,
        selected_types,
        download_delay_sec=args.delay
    )

    # 處理和打印結果摘要
    logger.info("\n--- 下載任務總結 ---")
    status_counts = {'success': 0, 'exists': 0, 'not_found': 0, 'error': 0, 'config_error': 0, 'no_tasks': 0}
    if not results:
        logger.warning("任務未返回任何結果。")
    else:
        for res in results:
            status_counts[res['status']] = status_counts.get(res['status'], 0) + 1
            if res['status'] == 'error' or res['status'] == 'config_error':
                logger.error(f"  類型: {res.get('task_type', 'N/A')}, 日期: {res.get('date_processed', 'N/A')}, 狀態: {res['status']}, 訊息: {res['message']}")
            elif res['status'] == 'not_found':
                logger.warning(f"  類型: {res.get('task_type', 'N/A')}, 日期: {res.get('date_processed', 'N/A')}, 狀態: {res['status']}, 訊息: {res['message']}")
            else: # success, exists, no_tasks
                 logger.info(f"  類型: {res.get('task_type', 'N/A')}, 日期: {res.get('date_processed', 'N/A')}, 狀態: {res['status']}, 檔案: {res.get('file_path', 'N/A')}")


    logger.info("\n--- 統計 ---")
    logger.info(f"  成功下載: {status_counts['success']}")
    logger.info(f"  已存在檔案 (跳過): {status_counts['exists']}")
    logger.info(f"  找不到檔案 (404): {status_counts['not_found']}")
    logger.info(f"  下載/配置錯誤: {status_counts['error'] + status_counts['config_error']}")
    if status_counts['no_tasks'] > 0 :
         logger.info(f"  無任務執行: {status_counts['no_tasks']}")

    logger.info("✅ 「採集官」下載任務執行完畢。")

if __name__ == "__main__":
    main()
