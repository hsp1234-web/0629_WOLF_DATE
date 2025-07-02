# -*- coding: utf-8 -*-
import requests
import time
import os
from datetime import datetime, timedelta
import logging

# --- 設定日誌 ---
import sys # 確保導入 sys
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout) # 明確指定 stdout
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# --- 下載任務定義 ---
# 將原 Colab 中的 tasks 字典結構化，以便 run.py 可以傳遞選擇
SUPPORTED_DOWNLOAD_TYPES = {
    'futures_daily_trades': { # 對應原 futures_daily_trades
        'folder_segment': 'A_Core_Trading/Futures_Trades',
        'url_template': 'https://www.taifex.com.tw/file/taifex/Dailydownload/DailydownloadCSV/Daily_{}.zip',
        'description': '期貨每筆成交資料 (Tick Data)'
    },
    'futures_daily_summary': { # 對應原 futures_daily_summary
        'folder_segment': 'A_Core_Trading/Futures_Summary',
        'url_template': 'https://www.taifex.com.tw/file/taifex/Daily/Daily_{}.zip',
        'description': '期貨每日交易行情'
    },
    'options_daily_trades': { # 對應原 options_daily_trades
        'folder_segment': 'A_Core_Trading/Options_Trades',
        'url_template': 'https://www.taifex.com.tw/file/taifex/OptionsDailydownload/OptionsDailydownloadCSV/OptionsDaily_{}.zip',
        'description': '選擇權每筆成交資料 (Tick Data)'
    },
    'options_daily_summary': { # 對應原 options_daily_summary
        'folder_segment': 'A_Core_Trading/Options_Summary',
        'url_template': 'https://www.taifex.com.tw/file/taifex/OptionsDaily/OptionsDaily_{}.zip',
        'description': '選擇權每日交易行情'
    },
    'institutional_investors': { # 對應原 institutional_investors
        'folder_segment': 'B_Market_Sentiment/Institutional_Investors',
        'url_template': 'https://www.taifex.com.tw/file/taifex/CHINESE/3/3_1_1_{}.zip',
        'description': '三大法人交易概況'
    },
    'put_call_ratio': { # 對應原 put_call_ratio
        'folder_segment': 'B_Market_Sentiment/Put_Call_Ratio',
        'url_template': 'https://www.taifex.com.tw/file/taifex/PCRatio/PCRatio_{}.zip',
        'description': '選擇權 Put/Call Ratio'
    },
    'final_settlement_price': { # 對應原 final_settlement_price
        'folder_segment': 'C_Settlement/Final_Settlement_Price',
        'url_template': 'https://www.taifex.com.tw/file/taifex/CHINESE/5/FSP_{}.zip',
        'description': '最後結算價'
    },
}

# --- 核心下載函式 ---
def download_single_file(url: str, full_file_path: str, timeout: int = 30) -> dict:
    """
    下載單一檔案。
    :param url: 要下載的 URL。
    :param full_file_path: 包含路徑和檔案名稱的完整儲存位置。
    :param timeout: request timeout 秒數。
    :return: 一個包含下載狀態和訊息的字典。
             {'status': 'success'/'exists'/'not_found'/'error', 'message': str, 'file_path': str}
    """
    result = {'status': '', 'message': '', 'file_path': full_file_path}

    if os.path.exists(full_file_path):
        result['status'] = 'exists'
        result['message'] = f"檔案已存在於 {full_file_path}，跳過下載。"
        logger.info(result['message'])
        return result

    try:
        logger.info(f"準備下載: {url} 至 {full_file_path}")
        response = requests.get(url, stream=True, timeout=timeout)

        # 檢查 HTTP Content-Type (簡易版，TAIFEX主要都是zip或csv，但zip本身可能Content-Type不明確)
        # content_type = response.headers.get('Content-Type', '').lower()
        # if response.status_code == 200 and 'application/zip' not in content_type and 'text/csv' not in content_type:
        #     # 這裡可以更細緻地處理，但TAIFEX的ZIP有時Content-Type不標準
        #     logger.warning(f"URL {url} 的 Content-Type 為 {content_type}，可能不是預期的檔案類型。仍嘗試下載...")

        if response.status_code == 200:
            os.makedirs(os.path.dirname(full_file_path), exist_ok=True)
            with open(full_file_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            result['status'] = 'success'
            result['message'] = f"成功下載檔案: {full_file_path}"
            logger.info(result['message'])
        elif response.status_code == 404:
            result['status'] = 'not_found'
            result['message'] = f"找不到檔案 (404): {url}"
            logger.warning(result['message'])
        else:
            result['status'] = 'error'
            result['message'] = f"下載失敗，HTTP 狀態碼: {response.status_code} URL: {url}"
            logger.error(result['message'])

    except requests.exceptions.Timeout:
        result['status'] = 'error'
        result['message'] = f"下載超時: {url}"
        logger.error(result['message'])
    except requests.exceptions.RequestException as e:
        result['status'] = 'error'
        result['message'] = f"下載時發生請求錯誤: {e} URL: {url}"
        logger.error(result['message'])
    except IOError as e:
        result['status'] = 'error'
        result['message'] = f"寫入檔案時發生IO錯誤: {e} 路徑: {full_file_path}"
        logger.error(result['message'])
    except Exception as e: # 捕獲其他未知錯誤
        result['status'] = 'error'
        result['message'] = f"下載過程中發生未知錯誤: {e} URL: {url}"
        logger.error(result['message'], exc_info=True)

    return result

def perform_download_tasks(
    start_date_str: str,
    end_date_str: str,
    output_base_path: str,
    download_types_selected: list[str],
    download_delay_sec: float = 0.5
    ) -> list[dict]:
    """
    執行選定的下載任務。
    :param start_date_str: 開始日期字串 (YYYY-MM-DD)。
    :param end_date_str: 結束日期字串 (YYYY-MM-DD)。
    :param output_base_path: 下載檔案的根儲存路徑。
    :param download_types_selected: 一個包含要下載的類型鍵名列表 (來自 SUPPORTED_DOWNLOAD_TYPES)。
    :param download_delay_sec: 每次請求後的延遲秒數。
    :return: 一個包含所有下載嘗試結果的字典列表。
    """
    all_download_results = []

    try:
        start_dt = datetime.strptime(start_date_str, '%Y-%m-%d')
        end_dt = datetime.strptime(end_date_str, '%Y-%m-%d')
    except ValueError:
        err_msg = "❌ 錯誤：日期格式不正確，請使用 'YYYY-MM-DD'。"
        logger.error(err_msg)
        # 返回一個表示配置錯誤的特殊結果
        return [{'status': 'config_error', 'message': err_msg, 'file_path': None}]

    if start_dt > end_dt:
        err_msg = "❌ 錯誤：開始日期不能晚於結束日期。"
        logger.error(err_msg)
        return [{'status': 'config_error', 'message': err_msg, 'file_path': None}]

    date_range = [start_dt + timedelta(days=x) for x in range((end_dt - start_dt).days + 1)]

    active_tasks = {}
    for type_key in download_types_selected:
        if type_key in SUPPORTED_DOWNLOAD_TYPES:
            active_tasks[type_key] = SUPPORTED_DOWNLOAD_TYPES[type_key]
        else:
            logger.warning(f"未知的下載類型 '{type_key}'，將被忽略。")
            all_download_results.append({
                'status': 'config_error',
                'message': f"未知的下載類型 '{type_key}'",
                'file_path': None,
                'type_key': type_key
            })


    if not active_tasks:
        warn_msg = "沒有選擇任何有效的下載類型。"
        logger.warning(warn_msg)
        # 返回一個表示沒有任務的結果
        return [{'status': 'no_tasks', 'message': warn_msg, 'file_path': None}]

    logger.info(f"將處理從 {start_date_str} 到 {end_date_str} 的數據。")
    logger.info(f"選定的下載類型: {', '.join(active_tasks.keys())}")
    logger.info(f"檔案將儲存至基路徑: {os.path.abspath(output_base_path)}")

    for current_date in date_range:
        date_str_for_url = current_date.strftime('%Y_%m_%d')
        logger.info(f"--- 開始處理日期: {current_date.strftime('%Y-%m-%d')} ---")

        for task_key, task_params in active_tasks.items():
            # 構造儲存路徑
            # output_base_path / task_params['folder_segment'] / YYYY / MM / DD / file_name.zip
            # 或者更簡單的 output_base_path / task_params['folder_segment'] / file_name.zip
            # 依照原 Colab 腳本，是 output_base_path / task_params['folder_segment'] / file_name.zip

            folder_path_for_type = os.path.join(output_base_path, task_params['folder_segment'])

            url = task_params['url_template'].format(date_str_for_url)
            file_name = os.path.basename(url) # e.g., Daily_2023_01_01.zip
            full_file_path = os.path.join(folder_path_for_type, file_name)

            download_result = download_single_file(url, full_file_path)
            # 為結果添加額外信息
            download_result['date_processed'] = current_date.strftime('%Y-%m-%d')
            download_result['task_type'] = task_key
            download_result['url_attempted'] = url
            all_download_results.append(download_result)

            if download_delay_sec > 0:
                time.sleep(download_delay_sec) # 友善伺服器

    logger.info("所有日期的下載任務處理完畢。")
    return all_download_results

if __name__ == '__main__':
    # 簡易測試 (實際由 run.py 驅動)
    logger.info("執行 downloader.py 的 __main__ 進行簡易測試...")

    # 測試參數
    test_start_date = "2023-01-01"
    test_end_date = "2023-01-02" # 下載兩天
    test_output_path = "temp_downloader_output"
    # 選擇一個通常有數據的類型進行測試，例如三大法人
    test_types = ['institutional_investors']

    if os.path.exists(test_output_path):
        import shutil
        logger.info(f"清理舊的測試輸出目錄: {test_output_path}")
        shutil.rmtree(test_output_path)

    results = perform_download_tasks(test_start_date, test_end_date, test_output_path, test_types)

    logger.info("\n--- 測試下載結果 ---")
    successful_downloads = 0
    for res in results:
        logger.info(
            f"  日期: {res.get('date_processed', 'N/A')}, "
            f"類型: {res.get('task_type', 'N/A')}, "
            f"狀態: {res['status']}, "
            f"檔案: {res.get('file_path', 'N/A')}, "
            f"訊息: {res['message']}"
        )
        if res['status'] == 'success':
            successful_downloads +=1
            assert os.path.exists(res['file_path']), f"測試失敗：宣告成功但檔案不存在 {res['file_path']}"

    if not results or results[0].get('status') in ['config_error', 'no_tasks']:
         logger.error("測試設定有誤或沒有任務執行。")
    elif successful_downloads > 0:
        logger.info(f"簡易測試完成，有 {successful_downloads} 個檔案成功下載（或已存在）。")
    else:
        logger.warning("簡易測試完成，但沒有檔案被成功下載。請檢查日期範圍和網路連線。")

    # 再次清理 (可選)
    # if os.path.exists(test_output_path):
    #     logger.info(f"清理測試輸出目錄: {test_output_path}")
    #     shutil.rmtree(test_output_path)
