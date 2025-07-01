# -*- coding: utf-8 -*-
# 採集官主執行檔
import os
import sys
import argparse
import requests
import time
from datetime import datetime, timedelta
from tqdm import tqdm
import warnings # 用於忽略 openpyxl 的警告，如果原始腳本有

# --- 路徑自我校正樣板碼 ---
try:
    current_dir = os.path.dirname(os.path.abspath(__file__))
    apps_dir = os.path.dirname(current_dir)
    project_root = os.path.dirname(apps_dir)

    if apps_dir not in sys.path:
        sys.path.insert(0, apps_dir)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
except Exception as e:
    print(f"路徑校正時發生錯誤: {e}", file=sys.stderr)
    pass
# --- 路徑自我校正樣板碼結束 ---

# --- 忽略特定警告 (如果需要) ---
warnings.filterwarnings('ignore', category=UserWarning, module='openpyxl')

# --- 核心下載邏輯 ---
def download_single_file(url: str, folder_path: str, file_name: str, session: requests.Session) -> str:
    """
    下載單一檔案的通用函式。
    返回狀態: 'success', 'exists', 'not_found', 'error'
    """
    os.makedirs(folder_path, exist_ok=True)
    file_path = os.path.join(folder_path, file_name)

    if os.path.exists(file_path) and os.path.getsize(file_path) > 0: # 檢查檔案是否存在且非空
        return 'exists'

    try:
        # 使用傳入的 session 對象
        response = session.get(url, stream=True, timeout=30) # 增加 timeout

        content_type = response.headers.get('Content-Type', '').lower()

        if response.status_code == 200:
            # 檢查 Content-Type 是否表明這是一個 HTML 頁面 (可能是軟 404)
            if 'text/html' in content_type:
                # print(f"警告: URL {url} 返回了 HTML 內容，疑似軟 404 錯誤。將視為 'not_found'。", file=sys.stderr)
                return 'not_found_html_content'

            # 檢查 Content-Type 是否符合預期的 ZIP 檔案類型
            # 預期的 ZIP Content-Type 可能是 'application/zip', 'application/x-zip-compressed', 'application/octet-stream'
            # 如果 Content-Type 明顯不符，也可能需要處理，但這裡先主要處理 text/html
            # if not any(zip_ct in content_type for zip_ct in ['application/zip', 'application/x-zip-compressed', 'octet-stream']):
            #     print(f"警告: URL {url} 返回的 Content-Type 為 {content_type}，可能不是預期的 ZIP 檔案。", file=sys.stderr)
            #     # 根據情況決定是否繼續下載或標記為錯誤/not_found

            with open(file_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            if os.path.getsize(file_path) > 0:
                return 'success'
            else:
                try: os.remove(file_path)
                except OSError: pass
                return 'not_found_empty_file' # 下載了空檔案
        elif response.status_code == 404:
            return 'not_found'
        else:
            return f'error_http_{response.status_code}'
    except requests.exceptions.Timeout:
        return 'error_timeout'
    except requests.exceptions.RequestException as e:
        return 'error_request'


def download_data(start_date_dt: datetime, end_date_dt: datetime, output_base_path: str, data_types: dict, sleep_time: float = 0.5):
    """
    主下載程序。
    data_types 是一個字典，鍵是如 'futures_trades' 的標識，值是布林表示是否下載。
    """
    print("======================================================")
    print("          🚀 TAIFEX 數據下載引擎啟動中...         ")
    print("======================================================\n")
    print(f"🗂️ 所有下載的檔案將會儲存在以下路徑：\n➡️ {os.path.abspath(output_base_path)}\n")

    date_range = [start_date_dt + timedelta(days=x) for x in range((end_date_dt - start_date_dt).days + 1)]

    # 任務清單模板
    # 結構: '內部名稱': {'enabled_key': '對應data_types的鍵', 'folder': '子資料夾名', 'url_template': 'URL模板'}
    TASKS_CONFIG = {
        'futures_trades': {
            'enabled_key': 'futures_trades',
            'folder': 'A_Core_Trading/Futures_Trades',
            'url_template': 'https://www.taifex.com.tw/file/taifex/Dailydownload/DailydownloadCSV/Daily_{}.zip'
        },
        'futures_summary': {
            'enabled_key': 'futures_summary',
            'folder': 'A_Core_Trading/Futures_Summary',
            'url_template': 'https://www.taifex.com.tw/file/taifex/Daily/Daily_{}.zip'
        },
        'options_trades': {
            'enabled_key': 'options_trades',
            'folder': 'A_Core_Trading/Options_Trades',
            'url_template': 'https://www.taifex.com.tw/file/taifex/OptionsDailydownload/OptionsDailydownloadCSV/OptionsDaily_{}.zip'
        },
        'options_summary': {
            'enabled_key': 'options_summary',
            'folder': 'A_Core_Trading/Options_Summary',
            'url_template': 'https://www.taifex.com.tw/file/taifex/OptionsDaily/OptionsDaily_{}.zip'
        },
        'institutional_investors': {
            'enabled_key': 'institutional_investors',
            'folder': 'B_Market_Sentiment/Institutional_Investors',
            'url_template': 'https://www.taifex.com.tw/file/taifex/CHINESE/3/3_1_1_{}.zip'
        },
        'put_call_ratio': {
            'enabled_key': 'put_call_ratio',
            'folder': 'B_Market_Sentiment/Put_Call_Ratio',
            'url_template': 'https://www.taifex.com.tw/file/taifex/PCRatio/PCRatio_{}.zip'
        },
        'final_settlement_price': {
            'enabled_key': 'final_settlement_price',
            'folder': 'C_Settlement/Final_Settlement_Price',
            'url_template': 'https://www.taifex.com.tw/file/taifex/CHINESE/5/FSP_{}.zip'
        },
    }

    active_tasks = {name: params for name, params in TASKS_CONFIG.items() if data_types.get(params['enabled_key'], False)}

    if not active_tasks:
        print("ℹ️ 沒有選擇任何數據類型進行下載。")
        return

    total_days = len(date_range)
    total_tasks_per_day = len(active_tasks)
    overall_progress_bar = tqdm(total=total_days * total_tasks_per_day, desc="📅 總體進度", unit="檔")

    # 使用 Session 對象以實現連接重用和可能的性能提升
    with requests.Session() as session:
        # 設定 User-Agent，模擬瀏覽器請求，有些伺服器可能需要
        session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'})

        for current_date_idx, current_date in enumerate(date_range):
            date_str_for_url = current_date.strftime('%Y_%m_%d')
            date_str_for_log = current_date.strftime('%Y-%m-%d')

            # tqdm.write(f"\nProcessing Date: {date_str_for_log}") # 換用 overall_progress_bar 的 set_postfix

            for task_internal_name, params in active_tasks.items():
                task_display_name = task_internal_name.replace('_', ' ').title()
                overall_progress_bar.set_postfix_str(f"{date_str_for_log} - {task_display_name}")

                folder_path = os.path.join(output_base_path, params['folder'])
                url = params['url_template'].format(date_str_for_url)
                file_name = os.path.basename(url)

                status = download_single_file(url, folder_path, file_name, session)

                log_symbol = "❓"
                if status == 'exists': log_symbol = "☑️ (已存在)"
                elif status == 'success': log_symbol = "✅ (成功)"
                elif status == 'not_found': log_symbol = "➖ (無資料)"
                elif status == 'error_timeout': log_symbol = "❌ (超時)"
                elif status.startswith('error_http_'): log_symbol = f"❌ (HTTP {status.split('_')[-1]})"
                elif status == 'error_request': log_symbol = "❌ (請求錯誤)"
                else: log_symbol = f"❌ ({status})" # 其他錯誤

                log_message = f"  [{date_str_for_log}] {task_display_name:<30} -> {file_name:<30} ... {log_symbol}"
                tqdm.write(log_message)
                print(log_message, file=sys.stderr) # 新增行：將日誌也印到 stderr
                overall_progress_bar.update(1)

                if status not in ['exists', 'success', 'not_found', 'not_found_html_content', 'not_found_empty_file']: # 對於錯誤情況，可以增加延遲
                    time.sleep(sleep_time * 2) # 發生錯誤時，延遲時間加倍
                else:
                    time.sleep(sleep_time) # 友善伺服器，每次請求後延遲

    overall_progress_bar.close()
    print("\n======================================================")
    print("             🎉 全部下載任務執行完畢！ 🎉             ")
    print("======================================================")
    print("\n**圖例說明**：")
    print("✅: 下載成功 | ☑️: 檔案已存在且非空，跳過 | ➖: 當日無資料(可能為假日或尚未提供) | ❌: 發生錯誤 (超時/HTTP錯誤/請求錯誤)")
    print(f"\n所有檔案皆已儲存至您的指定路徑：\n➡️ **{os.path.abspath(output_base_path)}**")

def main():
    parser = argparse.ArgumentParser(description="TAIFEX 數據採集官：從期交所官方網站批量下載指定日期範圍的原始數據。")

    # 日期參數
    parser.add_argument("--start-date", required=True, help="下載開始日期 (格式: YYYY-MM-DD)。")
    parser.add_argument("--end-date", required=True, help="下載結束日期 (格式: YYYY-MM-DD)。")
    parser.add_argument("--output-path", required=True, help="下載檔案的儲存根目錄。")

    # 數據類型開關 (預設為 False)
    # group = parser.add_argument_group('數據類型選擇 (預設全為否)')
    data_types_choices = {
        'futures_trades': '期貨逐筆成交 (Daily_{Y_M_D}.zip)',
        'options_trades': '選擇權逐筆成交 (OptionsDaily_{Y_M_D}.zip)',
        'institutional_investors': '三大法人交易概況 (3_1_1_{Y_M_D}.zip)',
        'put_call_ratio': '選擇權 Put/Call Ratio (PCRatio_{Y_M_D}.zip)',
        'futures_summary': '期貨每日交易行情 (Daily_Futures_Summary_{Y_M_D}.zip)', # 檔名可能不同，依實際為準
        'options_summary': '選擇權每日交易行情 (OptionsDaily_Summary_{Y_M_D}.zip)', # 檔名可能不同
        'final_settlement_price': '最後結算價 (FSP_{Y_M_D}.zip)'
    }
    for key, desc in data_types_choices.items():
        parser.add_argument(f"--{key.replace('_', '-')}", action='store_true', help=f"是否下載 {desc}。")

    parser.add_argument("--sleep", type=float, default=0.5, help="每次下載請求之間的延遲秒數 (預設: 0.5)。")


    args = parser.parse_args()

    try:
        start_dt = datetime.strptime(args.start_date, '%Y-%m-%d')
        end_dt = datetime.strptime(args.end_date, '%Y-%m-%d')
    except ValueError:
        print("❌ 錯誤：日期格式不正確，請使用 'YYYY-MM-DD'。", file=sys.stderr)
        sys.exit(1)

    if end_dt < start_dt:
        print("❌ 錯誤：結束日期不能早於開始日期。", file=sys.stderr)
        sys.exit(1)

    # 將 argparse 的命名空間轉換為字典給 download_data
    selected_data_types = {key: getattr(args, key) for key in data_types_choices.keys()}

    if not any(selected_data_types.values()):
        print("ℹ️ 提示：未選擇任何數據類型進行下載。若要下載，請至少指定一個數據類型參數 (例如 --futures-trades)。", file=sys.stderr)
        # 即使沒有選擇任何數據類型，也讓程式正常結束 (exit 0)，因為這不是一個執行錯誤
        # 或者，可以提示使用者並 exit 1，這裡選擇前者
        sys.exit(0)

    download_data(start_dt, end_dt, args.output_path, selected_data_types, sleep_time=args.sleep)
    sys.exit(0)

if __name__ == "__main__":
    main()
