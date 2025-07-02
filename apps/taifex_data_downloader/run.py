# -*- coding: utf-8 -*-
# 採集官主執行檔 (v16.0 同步版本)
import os
import sys
import argparse
import requests
import time
from datetime import datetime, timedelta
from tqdm import tqdm
import warnings

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

warnings.filterwarnings('ignore', category=UserWarning, module='openpyxl') # 來自原始 Colab

# --- 核心下載邏輯 (同步版本) ---
def download_single_file(url: str, folder_path: str, file_name: str, session: requests.Session) -> str:
    os.makedirs(folder_path, exist_ok=True)
    file_path = os.path.join(folder_path, file_name)

    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
        return 'exists'
    try:
        response = session.get(url, stream=True, timeout=30)
        content_type = response.headers.get('Content-Type', '').lower()

        if response.status_code == 200:
            if 'text/html' in content_type:
                return 'not_found_html_content'

            with open(file_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            if os.path.getsize(file_path) > 0:
                return 'success'
            else:
                try: os.remove(file_path)
                except OSError: pass
                return 'not_found_empty_file'
        elif response.status_code == 404:
            return 'not_found'
        else:
            return f'error_http_{response.status_code}'
    except requests.exceptions.Timeout:
        return 'error_timeout'
    except requests.exceptions.RequestException:
        return 'error_request'

def download_data(start_date_dt: datetime, end_date_dt: datetime, output_base_path: str, data_types_to_download: dict, sleep_time: float = 0.5):
    print("======================================================")
    print("          🚀 TAIFEX 同步數據下載引擎啟動中...         ")
    print("======================================================\n")
    print(f"🗂️ 所有下載的檔案將會儲存在以下路徑：\n➡️ {os.path.abspath(output_base_path)}\n")

    date_range = [start_date_dt + timedelta(days=x) for x in range((end_date_dt - start_date_dt).days + 1)]

    TASKS_CONFIG = {
        'futures_trades': {'folder': 'A_Core_Trading/Futures_Trades', 'url_template': 'https://www.taifex.com.tw/file/taifex/Dailydownload/DailydownloadCSV/Daily_{}.zip'},
        'futures_summary': {'folder': 'A_Core_Trading/Futures_Summary', 'url_template': 'https://www.taifex.com.tw/file/taifex/Daily/Daily_{}.zip'},
        'options_trades': {'folder': 'A_Core_Trading/Options_Trades', 'url_template': 'https://www.taifex.com.tw/file/taifex/OptionsDailydownload/OptionsDailydownloadCSV/OptionsDaily_{}.zip'},
        'options_summary': {'folder': 'A_Core_Trading/Options_Summary', 'url_template': 'https://www.taifex.com.tw/file/taifex/OptionsDaily/OptionsDaily_{}.zip'},
        'institutional_investors': {'folder': 'B_Market_Sentiment/Institutional_Investors', 'url_template': 'https://www.taifex.com.tw/file/taifex/CHINESE/3/3_1_1_{}.zip'},
        'put_call_ratio': {'folder': 'B_Market_Sentiment/Put_Call_Ratio', 'url_template': 'https://www.taifex.com.tw/file/taifex/PCRatio/PCRatio_{}.zip'},
        'final_settlement_price': {'folder': 'C_Settlement/Final_Settlement_Price', 'url_template': 'https://www.taifex.com.tw/file/taifex/CHINESE/5/FSP_{}.zip'},
    }

    active_tasks_definitions = {
        task_key: TASKS_CONFIG[task_key]
        for task_key, should_download in data_types_to_download.items()
        if should_download and task_key in TASKS_CONFIG
    }

    if not active_tasks_definitions:
        print("ℹ️ 沒有選擇任何數據類型進行下載。")
        return

    total_iterations = len(date_range) * len(active_tasks_definitions)
    if total_iterations == 0:
        print("ℹ️ 沒有任何下載任務需要執行。")
        return

    overall_progress_bar = tqdm(total=total_iterations, desc="📅 總體進度", unit="檔")

    with requests.Session() as session:
        session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'})

        for current_date in date_range:
            date_str_for_url = current_date.strftime('%Y_%m_%d')
            date_str_for_log = current_date.strftime('%Y-%m-%d')

            for task_key, params in active_tasks_definitions.items():
                task_display_name = task_key.replace('_', ' ').title()
                overall_progress_bar.set_postfix_str(f"{date_str_for_log} - {task_display_name}")

                folder_path = os.path.join(output_base_path, params['folder'])
                url = params['url_template'].format(date_str_for_url)
                file_name = os.path.basename(url)

                status = download_single_file(url, folder_path, file_name, session)

                log_symbol = "❓"
                if status == 'exists': log_symbol = "☑️ (已存在)"
                elif status == 'success': log_symbol = "✅ (成功)"
                elif status == 'not_found': log_symbol = "➖ (未找到)"
                elif status == 'not_found_html_content': log_symbol = "⚠️ (HTML內容)"
                elif status == 'not_found_empty_file': log_symbol = "⚠️ (空檔案)"
                elif status == 'error_timeout': log_symbol = "❌ (超時)"
                elif status.startswith('error_http_'): log_symbol = f"❌ (HTTP {status.split('_')[-1]})"
                elif status == 'error_request': log_symbol = "❌ (請求錯誤)"
                else: log_symbol = f"❌ ({status})"

                # 確保日誌打印到 stdout 或 stderr 以便 subprocess 捕獲
                log_message = f"  [{date_str_for_log}] {task_display_name:<30} -> {file_name:<30} ... {log_symbol}"
                tqdm.write(log_message) # tqdm.write 輸出到 stderr
                # print(log_message, file=sys.stdout) # 如果需要 stdout 輸出

                overall_progress_bar.update(1)
                time.sleep(sleep_time)

    overall_progress_bar.close()
    print("\n======================================================")
    print("             🎉 全部同步下載任務執行完畢！ 🎉             ")
    print("======================================================")
    print("\n**圖例說明**：")
    print("✅: 成功 | ☑️: 已存在 | ➖: 未找到 | ⚠️: HTML內容/空檔案 | ❌: 錯誤")
    print(f"\n所有檔案嘗試儲存至您的指定本地路徑：\n➡️ **{os.path.abspath(output_base_path)}**")

def main():
    parser = argparse.ArgumentParser(description="TAIFEX 同步數據採集官：從期交所官方網站批量下載指定日期範圍的原始數據至本地。")

    parser.add_argument("--start-date", required=True, help="下載開始日期 (格式: YYYY-MM-DD)。")
    parser.add_argument("--end-date", required=True, help="下載結束日期 (格式: YYYY-MM-DD)。")
    parser.add_argument("--output-path", required=True, help="下載檔案的本地儲存根目錄。")
    parser.add_argument("--sleep", type=float, default=0.2, help="每次下載請求之間的延遲秒數 (預設: 0.2)。")

    data_types_choices_map = {
        'futures_trades': '期貨逐筆成交', 'options_trades': '選擇權逐筆成交',
        'institutional_investors': '三大法人交易概況', 'put_call_ratio': '選擇權 Put/Call Ratio',
        'futures_summary': '期貨每日交易行情', 'options_summary': '選擇權每日交易行情',
        'final_settlement_price': '最後結算價'
    }
    for key, desc in data_types_choices_map.items():
        parser.add_argument(f"--{key.replace('_', '-')}", action='store_true', help=f"是否下載 {desc}。")

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

    selected_data_types_dict = {key: getattr(args, key) for key in data_types_choices_map.keys()}

    if not any(selected_data_types_dict.values()):
        print("ℹ️ 提示：未選擇任何數據類型進行下載。", file=sys.stderr)
        sys.exit(0)

    download_data(start_dt, end_dt, args.output_path, selected_data_types_dict, sleep_time=args.sleep)
    sys.exit(0)

if __name__ == "__main__":
    main()
