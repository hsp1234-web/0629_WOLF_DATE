# -*- coding: utf-8 -*-
# 採集官主執行檔
import os
import sys
import argparse
import time
from datetime import datetime, timedelta
import warnings # 用於忽略 openpyxl 的警告，如果原始腳本有
import asyncio
# import aiohttp # v21.0 改用 requests
import multiprocessing
import queue # 用於 queue.Full 例外 和 main 中的測試佇列
from typing import List, Dict, Optional, Any
from tqdm import tqdm
import random
from bs4 import BeautifulSoup # 用於解析HTML (v21.0下載失敗時記錄HTML預覽)
import urllib.parse
import requests # 新增：用於同步HTTP請求

# --- v21.0 匿蹤與適應性下載模組常數 ---
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Firefox/109.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Firefox/109.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/109.0.5414.74",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Edge/109.0.5414.74",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:102.0) Gecko/20100101 Firefox/102.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.2 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 13; SM-G998B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Mobile Safari/537.36"
]

TARGET_QUERY_PAGE_URL = "https://www.taifex.com.tw/cht/3/dlDailyMarketView"

BASE_DELAY_SECONDS = 0.5
JITTER_RANGE_SECONDS = (0.1, 1.0)
MAX_RETRIES = 3
INITIAL_BACKOFF_SECONDS = 2
# --- v21.0 常數定義結束 ---

# --- 路徑自我校正樣板碼 ---
try:
    current_dir = os.path.dirname(os.path.abspath(__file__))
    apps_dir = os.path.dirname(current_dir)
    project_root = os.path.dirname(apps_dir)
    if apps_dir not in sys.path: sys.path.insert(0, apps_dir)
    if project_root not in sys.path: sys.path.insert(0, project_root)
except Exception as e:
    print(f"路徑校正時發生錯誤: {e}", file=sys.stderr)
# --- 路徑自我校正樣板碼結束 ---

warnings.filterwarnings('ignore', category=UserWarning, module='openpyxl')

# --- v21.0 輔助函式 ---
async def _make_stealth_request_async(
    requests_session: requests.Session, method: str, url: str,
    params: Optional[dict] = None, data: Optional[dict] = None,
    current_retry: int = 0, request_description: str = "請求"
) -> Optional[requests.Response]:
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Referer": "https://www.taifex.com.tw/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    if current_retry > 0:
        delay = BASE_DELAY_SECONDS + random.uniform(JITTER_RANGE_SECONDS[0], JITTER_RANGE_SECONDS[1])
        await asyncio.sleep(delay)

    response: Optional[requests.Response] = None
    try:
        response = await asyncio.to_thread(
            requests_session.request, method, url, params=params, data=data, headers=headers, timeout=60, stream=True
        )
        if response.status_code >= 500:
            print(f"⚠️ [{request_description}] 收到伺服器錯誤 HTTP {response.status_code}，將嘗試重試。 URL: {url}")
            response.raise_for_status()
        elif response.status_code >= 400 :
             print(f"ℹ️ [{request_description}] 收到客戶端錯誤 HTTP {response.status_code} (非重試錯誤)，URL: {url}。內容類型: {response.headers.get('Content-Type')}")
        return response
    except requests.exceptions.HTTPError as e:
        response = e.response if e.response is not None else response
        print(f"❌ [{request_description}] {method} 請求失敗 (HTTP {e.response.status_code if e.response else 'N/A'}): {e}。 URL: {url}")
        if current_retry < MAX_RETRIES and (e.response is not None and e.response.status_code >= 500):
            backoff_time = INITIAL_BACKOFF_SECONDS * (2 ** current_retry)
            print(f"↪️  HTTP {e.response.status_code} 錯誤，將在 {backoff_time} 秒後進行第 {current_retry + 1} 次重試...")
            await asyncio.sleep(backoff_time)
            return await _make_stealth_request_async(requests_session, method, url, params, data, current_retry + 1, request_description)
        else:
            status_code_for_log = e.response.status_code if e.response is not None else "N/A"
            print(f"🚫 [{request_description}] 已達最大重試次數或收到不可重試的 HTTP {status_code_for_log}，放棄請求 {url}。")
            return response
    except requests.exceptions.Timeout:
        print(f"❌ [{request_description}] {method} 請求超時。 URL: {url}")
        if current_retry < MAX_RETRIES:
            backoff_time = INITIAL_BACKOFF_SECONDS * (2 ** current_retry)
            print(f"↪️  請求超時，將在 {backoff_time} 秒後進行第 {current_retry + 1} 次重試...")
            await asyncio.sleep(backoff_time)
            return await _make_stealth_request_async(requests_session, method, url, params, data, current_retry + 1, request_description)
        else:
            print(f"🚫 [{request_description}] 已達最大重試次數 ({MAX_RETRIES})，因超時放棄請求 {url}。")
            return None
    except requests.exceptions.RequestException as e:
        print(f"❌ [{request_description}] {method} 請求時發生錯誤: {type(e).__name__} - {e}。 URL: {url}")
        if current_retry < MAX_RETRIES:
            backoff_time = INITIAL_BACKOFF_SECONDS * (2 ** current_retry)
            print(f"↪️  請求錯誤 ({type(e).__name__})，將在 {backoff_time} 秒後進行第 {current_retry + 1} 次重試...")
            await asyncio.sleep(backoff_time)
            return await _make_stealth_request_async(requests_session, method, url, params, data, current_retry + 1, request_description)
        else:
            print(f"🚫 [{request_description}] 已達最大重試次數，因 ({type(e).__name__}) 放棄請求 {url}。")
            return None
    except Exception as e:
        print(f"❌ [{request_description}] {method} 請求時發生未預期錯誤: {type(e).__name__} - {e}。 URL: {url}")
        return None

COMMANDER_TASKS_CONFIG_V3 = {
    'futures_daily_trades': {'description': '期貨每筆成交資料 (Tick Data)', 'folder': 'A_Core_Trading/Futures_Trades', 'url_template': 'https://www.taifex.com.tw/file/taifex/Dailydownload/DailydownloadCSV/Daily_{}.zip'},
    'futures_daily_summary': {'description': '期貨每日交易行情', 'folder': 'A_Core_Trading/Futures_Summary', 'url_template': 'https://www.taifex.com.tw/file/taifex/Daily/Daily_{}.zip'},
    'options_daily_trades': {'description': '選擇權每筆成交資料 (Tick Data)', 'folder': 'A_Core_Trading/Options_Trades', 'url_template': 'https://www.taifex.com.tw/file/taifex/OptionsDailydownload/OptionsDailydownloadCSV/OptionsDaily_{}.zip'},
    'options_daily_summary': {'description': '選擇權每日交易行情', 'folder': 'A_Core_Trading/Options_Summary', 'url_template': 'https://www.taifex.com.tw/file/taifex/OptionsDaily/OptionsDaily_{}.zip'},
    'institutional_investors': {'description': '三大法人交易概況', 'folder': 'B_Market_Sentiment/Institutional_Investors', 'url_template': 'https://www.taifex.com.tw/file/taifex/CHINESE/3/3_1_1_{}.zip'},
    'put_call_ratio': {'description': '選擇權 Put/Call Ratio', 'folder': 'B_Market_Sentiment/Put_Call_Ratio', 'url_template': 'https://www.taifex.com.tw/file/taifex/PCRatio/PCRatio_{}.zip'},
    'final_settlement_price': {'description': '最後結算價', 'folder': 'C_Settlement/Final_Settlement_Price', 'url_template': 'https://www.taifex.com.tw/file/taifex/CHINESE/5/FSP_{}.zip'},
}

async def _execute_download_async(
    requests_session: requests.Session, download_url: str, folder_path: str, file_name: str,
    task_key: str, target_date: datetime, task_queue: Optional[multiprocessing.Queue],
    semaphore: Optional[asyncio.Semaphore]
) -> Dict[str, Any]:
    os.makedirs(folder_path, exist_ok=True)
    local_file_path = os.path.join(folder_path, file_name)
    status_str = "initiated"; error_msg = None; final_http_status = None; response_preview_for_log = ""; total_attempts = 0

    if os.path.exists(local_file_path) and os.path.getsize(local_file_path) > 0:
        status_str = 'exists'; print(f"✅ [檔案已存在] {local_file_path} (類型: {task_key})")
        return {'url': download_url, 'local_path': local_file_path, 'status': status_str, 'file_name': file_name, 'task_key': task_key, 'retries_this_file': 0}

    download_response: Optional[requests.Response] = None
    for attempt in range(MAX_RETRIES + 1):
        total_attempts = attempt + 1
        data_type_desc_for_log = COMMANDER_TASKS_CONFIG_V3.get(task_key, {}).get('description', task_key)
        request_description = f"下載檔案 ({data_type_desc_for_log} - {target_date.strftime('%Y-%m-%d')}, 嘗試 {total_attempts})"
        download_response = await _make_stealth_request_async(requests_session, "GET", download_url, current_retry=attempt, request_description=request_description)

        if download_response:
            final_http_status = download_response.status_code
            if download_response.status_code == 200:
                content_type = download_response.headers.get('Content-Type', '').lower()
                if 'text/html' in content_type:
                    status_str = 'error_unexpected_html_content'; error_msg = "伺服器返回HTML內容，非預期的檔案類型。"
                    try:
                        html_content = download_response.text
                        soup = BeautifulSoup(html_content, "html.parser")
                        title_tag = soup.find("title"); title = title_tag.string.strip() if title_tag else "無標題"
                        response_preview_for_log = f"<title>{title}</title>\n{html_content[:300]}"; error_msg += f" (頁面標題: {title})"
                    except Exception as e_parse: response_preview_for_log = "解析HTML回應預覽時出錯。"; error_msg += f" (解析HTML預覽時出錯: {e_parse})"
                    break
                else:
                    try:
                        content = download_response.content
                        if content:
                            with open(local_file_path, 'wb') as f: f.write(content)
                            if os.path.getsize(local_file_path) > 0:
                                status_str = 'success'; print(f"✅ [下載成功] {local_file_path} (類型: {task_key}, 日期: {target_date.strftime('%Y-%m-%d')}, 嘗試 {total_attempts} 次)")
                            else:
                                status_str = 'error_empty_file_after_write'; error_msg = "檔案寫入後為空。"
                                if os.path.exists(local_file_path):
                                    try: os.remove(local_file_path)
                                    except OSError: pass
                        else: status_str = 'error_empty_content_stream'; error_msg = "伺服器返回內容流為空。"
                        break
                    except Exception as e_read_save:
                        status_str = 'error_read_save_exception'; error_msg = f"讀取或儲存檔案時發生錯誤: {e_read_save}"
                        if os.path.exists(local_file_path):
                            try: os.remove(local_file_path)
                            except OSError: pass
                        break
            elif download_response.status_code == 404:
                status_str = 'error_http_404_not_found'; error_msg = f"HTTP 404 - 檔案在伺服器上未找到。"
                try:
                    html_content = download_response.text
                    soup = BeautifulSoup(html_content, "html.parser")
                    title_tag = soup.find("title"); title = title_tag.string.strip() if title_tag else "無標題"
                    response_preview_for_log = f"<title>{title}</title>\n{html_content[:300]}"; error_msg += f" (頁面標題: {title})"
                except Exception: response_preview_for_log = "無法獲取404頁面預覽。"
                break
            else:
                status_str = f'error_http_{download_response.status_code}'; error_msg = f"HTTP 錯誤狀態: {download_response.status_code}"
                try:
                    html_content = download_response.text
                    soup = BeautifulSoup(html_content, "html.parser")
                    title_tag = soup.find("title"); title = title_tag.string.strip() if title_tag else "無標題"
                    response_preview_for_log = f"<title>{title}</title>\n{html_content[:300]}"; error_msg += f" (頁面標題: {title})"
                except Exception: response_preview_for_log = f"無法獲取HTTP {download_response.status_code}頁面預覽。"
                if download_response.status_code < 500 : break
                break
        else:
            status_str = 'error_all_retries_failed'; error_msg = f"經過 {total_attempts} 次嘗試後，下載請求最終失敗。"
            final_http_status = None; response_preview_for_log = "請求最終失敗，無伺服器回應可預覽。"
            break
    if status_str == 'success':
        if task_queue is not None:
            try: task_queue.put({"file_path": local_file_path, "file_type": task_key})
            except queue.Full: print(f"‼️ [佇列已滿] 無法將 {local_file_path} 加入佇列。")
            except Exception as e_q: print(f"‼️ [佇列錯誤] 將 {local_file_path} 加入佇列時發生錯誤: {e_q}")
    else:
        log_entry = (f"\n==================== ⚠️ 檔案下載失敗報告 ⚠️ ====================\n"
            f"  [目標日期]: {target_date.strftime('%Y-%m-%d')}\n"
            f"  [數據類型]: {COMMANDER_TASKS_CONFIG_V3.get(task_key, {}).get('description', task_key)}\n"
            f"  [嘗試URL]: {download_url}\n"
            f"  [最終HTTP狀態碼]: {final_http_status if final_http_status is not None else 'N/A (請求未成功)'}\n"
            f"  [失敗原因]: {status_str} - {error_msg}\n"
            f"  [伺服器回應預覽]:\n{'-'*20} PREVIEW START {'-'*20}\n{response_preview_for_log if response_preview_for_log else '無回應預覽可用。'}\n{'-'*21} PREVIEW END {'-'*22}\n"
            f"  [重試次數]: {max(0, total_attempts - 1)} (總嘗試 {total_attempts} 次)\n"
            f"======================================================================")
        print(f"[ERROR]{log_entry}")
    return {'url': download_url, 'local_path': local_file_path, 'status': status_str, 'error': error_msg, 'file_name': file_name, 'task_key': task_key, 'retries_this_file': total_attempts -1}

async def download_single_file_async(
    requests_session: requests.Session, download_url: str, folder_path: str, file_name: str,
    task_key: str, target_date: datetime, task_queue: Optional[multiprocessing.Queue],
    semaphore: Optional[asyncio.Semaphore]
) -> Dict[str, Any]:
    return await _execute_download_async(
        requests_session=requests_session, download_url=download_url, folder_path=folder_path,
        file_name=file_name, task_key=task_key, target_date=target_date, task_queue=task_queue,
        semaphore=None
    )

async def download_data_async(
    start_date_str: str, end_date_str: str, data_types_to_download: Dict[str, bool],
    output_path_base: str, task_queue: multiprocessing.Queue, max_concurrent_downloads: int = 5,
    inter_date_delay_seconds: float = 1.0
):
    try:
        start_dt = datetime.strptime(start_date_str, '%Y-%m-%d')
        end_dt = datetime.strptime(end_date_str, '%Y-%m-%d')
    except ValueError:
        print(f"❌ [主程序]日期格式錯誤。應為 YYYY-MM-DD。收到: start_date='{start_date_str}', end_date='{end_date_str}'")
        return []
    if end_dt < start_dt:
        print(f"❌ [主程序]結束日期 '{end_date_str}' 不能早於開始日期 '{start_date_str}'。")
        return []

    print("\n==================================================================================")
    print("      🚀 TAIFEX 數據下載引擎 (v21.0 - URL模板版) 啟動中...         ")
    print("==================================================================================\n")
    print(f"🔎 目標日期範圍: {start_date_str} 至 {end_date_str}")
    print(f"📊 欲下載數據類型: {', '.join([dt for dt, V in data_types_to_download.items() if V])}")
    print(f"🗂️  檔案將儲存至基礎路徑: {os.path.abspath(output_path_base)}")
    print(f"⚙️  最大並行下載數: {max_concurrent_downloads}, 日期間隔延時: {inter_date_delay_seconds}秒\n")

    date_range = [start_dt + timedelta(days=x) for x in range((end_dt - start_dt).days + 1)]
    all_created_download_coroutines = []

    with requests.Session() as requests_session:
        for date_val in date_range:
            date_str_yyyymmdd_for_log = date_val.strftime('%Y-%m-%d') # 用於日誌和資料夾名稱

            print(f"\n🗓️  開始處理日期: {date_str_yyyymmdd_for_log}")

            if date_val != date_range[0]:
                 await asyncio.sleep(inter_date_delay_seconds)

            for task_key, should_download in data_types_to_download.items():
                if not should_download: continue
                if task_key not in COMMANDER_TASKS_CONFIG_V3:
                    print(f"⚠️ [跳過] 數據類型 '{task_key}' 未在 COMMANDER_TASKS_CONFIG_V3 中配置。")
                    continue

                task_config = COMMANDER_TASKS_CONFIG_V3[task_key]
                url_template_str = task_config['url_template']
                data_type_desc = task_config.get("description", task_key)

                current_inter_request_delay = BASE_DELAY_SECONDS + random.uniform(JITTER_RANGE_SECONDS[0], JITTER_RANGE_SECONDS[1])
                await asyncio.sleep(current_inter_request_delay)
                print(f"  ➡️  準備下載 [{data_type_desc}] (延時: {current_inter_request_delay:.2f}s)...")

                if "CHINESE/3/3_1_1" in url_template_str or \
                   "CHINESE/5/FSP" in url_template_str or \
                   "PCRatio/PCRatio" in url_template_str:
                    current_date_str_for_url = date_val.strftime('%Y%m%d')
                else:
                    current_date_str_for_url = date_val.strftime('%Y_%m_%d')
                try:
                    download_url = url_template_str.format(current_date_str_for_url)
                except KeyError: download_url = url_template_str
                except Exception as e_url:
                    print(f"    ❌ 生成 URL 時發生錯誤 ({task_key}, {current_date_str_for_url}): {e_url}")
                    continue

                file_name_from_url = os.path.basename(urllib.parse.urlparse(download_url).path)
                if not file_name_from_url:
                    file_name_from_url = f"{task_key}_{current_date_str_for_url}_{random.randint(1000,9999)}.zip"
                    print(f"    ⚠️ 無法從URL '{download_url}' 解析檔名，自動生成: {file_name_from_url}")

                current_output_folder = os.path.join(output_path_base, task_config['folder'])

                coro = download_single_file_async(
                    requests_session=requests_session, download_url=download_url,
                    folder_path=current_output_folder, file_name=file_name_from_url,
                    task_key=task_key, target_date=date_val, task_queue=task_queue,
                    semaphore=None
                )
                all_created_download_coroutines.append(coro)
                print(f"    ➕ 已為 [{data_type_desc}] 新增下載任務: {download_url} -> {os.path.join(current_output_folder, file_name_from_url)}")

    if not all_created_download_coroutines:
        print("\nℹ️ [任務完成] 沒有建立任何有效的下載任務。請檢查日期範圍、選擇的數據類型、URL模板及網路連線。")
        return []

    all_results = []
    semaphore = asyncio.Semaphore(max_concurrent_downloads)
    async def _wrapped_coro(coro, sem):
        async with sem: return await coro
    tasks_with_semaphore = [_wrapped_coro(coro, semaphore) for coro in all_created_download_coroutines]

    print(f"\n📥 即將執行總共 {len(tasks_with_semaphore)} 個下載任務，最大並行數: {max_concurrent_downloads}...")
    pbar = tqdm(total=len(tasks_with_semaphore), desc="🚀 整體下載進度", unit="檔")
    for future in asyncio.as_completed(tasks_with_semaphore):
        try:
            result = await future
            all_results.append(result)
        except Exception as e_gather:
            print(f"‼️ [嚴重錯誤] 執行下載任務時發生未預期例外: {e_gather}")
            all_results.append({'url': 'N/A', 'local_path': 'N/A', 'status': 'error_coroutine_exception',
                'error': str(e_gather), 'file_name': 'N/A', 'task_key': 'N/A', 'retries_this_file': 0})
        finally:
            pbar.update(1)
            if 'result' in locals() and isinstance(result, dict):
                 pbar.set_postfix_str(f"{result.get('file_name', 'N/A')} -> {result.get('status', 'N/A')}")
            if 'result' in locals(): del result
    pbar.close()

    print("\n===================================================================")
    print("             🎉 全部 v21.0 下載任務執行完畢！ 🎉             ")
    print("===================================================================")
    success_count = sum(1 for r in all_results if isinstance(r, dict) and r.get('status') == 'success')
    exists_count = sum(1 for r in all_results if isinstance(r, dict) and r.get('status') == 'exists')
    error_count = sum(1 for r in all_results if isinstance(r, dict) and 'error' in r.get('status', ''))
    print(f"\n📊 總結：\n  總任務數 (嘗試下載的檔案連結數): {len(all_created_download_coroutines)}\n  成功下載: {success_count}\n  檔案已存在 (跳過): {exists_count}\n  下載失敗 (詳見上方日誌): {error_count}")
    failed_files_summary = []
    for r in all_results:
        if isinstance(r, dict) and r.get('status') not in ['success', 'exists']:
            failed_files_summary.append(f"  - {r.get('file_name', 'N/A')} (URL: {r.get('url', 'N/A')}) -> 狀態: {r.get('status')}, 原因: {r.get('error', '未知')}")
    if failed_files_summary:
        print("\n📋 詳細失敗檔案列表：")
        for item in failed_files_summary: print(item)
    print(f"\n🗂️ 所有下載的檔案（若成功）應位於基礎路徑 '{os.path.abspath(output_path_base)}' 下的日期和類型子目錄中。")
    return all_results

def main():
    parser = argparse.ArgumentParser(description="TAIFEX 匿蹤偵察與適應性下載引擎 (v21.0) - 獨立測試模式。")
    parser.add_argument("--start-date", required=True, help="下載開始日期 (格式: YYYY-MM-DD)。")
    parser.add_argument("--end-date", required=True, help="下載結束日期 (格式: YYYY-MM-DD)。")
    parser.add_argument("--output-path", required=True, help="下載檔案的本地儲存基礎目錄。檔案將儲存於 <output-path>/<YYYY-MM-DD>/<task_key>/ 下。")
    parser.add_argument("--max-concurrent", type=int, default=5, help="最大並行下載數 (預設: 5)。")
    parser.add_argument("--inter-date-delay", type=float, default=1.0, help="處理完一個日期的所有類型後，到下一個日期前的延時（秒） (預設: 1.0)。")
    for key, config in COMMANDER_TASKS_CONFIG_V3.items():
        parser.add_argument(f"--{key.replace('_', '-')}", action='store_true', help=f"是否下載 {config.get('description', key)}。")
    args = parser.parse_args()
    selected_data_types_to_download = {key: getattr(args, key.replace('-', '_')) for key in COMMANDER_TASKS_CONFIG_V3.keys() if hasattr(args, key.replace('-', '_'))}
    if not any(selected_data_types_to_download.values()):
        print("ℹ️ 提示：未選擇任何數據類型進行下載。請使用至少一個 --<數據類型> 參數。")
        print("可用的數據類型參數包括："); [print(f"  --{k.replace('_', '-')}: {v.get('description', k)}") for k, v in COMMANDER_TASKS_CONFIG_V3.items()]; sys.exit(0)

    mp_manager_created = False
    try:
        mp_manager = multiprocessing.Manager(); test_task_queue = mp_manager.Queue(); mp_manager_created = True
        print("ℹ️ [測試模式] 使用 multiprocessing.Manager().Queue() 作為任務佇列。")
    except Exception as e_manager:
        print(f"⚠️ [測試模式] 無法初始化 Manager().Queue() ({e_manager})，回退到標準 queue.Queue (僅限單進程)。")
        test_task_queue = queue.Queue()

    print(f"\n[測試模式] 即將開始下載...\n  開始日期: {args.start_date}\n  結束日期: {args.end_date}\n  輸出路徑: {args.output_path}\n  選擇的數據類型: {{k:v for k,v in selected_data_types_to_download.items() if v}}\n  最大並行數: {args.max_concurrent}\n  日期間隔延時: {args.inter_date_delay}\n")
    try:
        asyncio.run(download_data_async(
            start_date_str=args.start_date, end_date_str=args.end_date,
            data_types_to_download=selected_data_types_to_download, output_path_base=args.output_path,
            task_queue=test_task_queue, max_concurrent_downloads=args.max_concurrent,
            inter_date_delay_seconds=args.inter_date_delay))
    except KeyboardInterrupt: print("\n🚫 操作被使用者中斷。")
    finally:
        queue_had_items = False
        if 'test_task_queue' in locals():
            q_type_str = type(test_task_queue).__name__; is_managed_queue = 'Queue' in q_type_str
            try:
                if is_managed_queue or isinstance(test_task_queue, queue.Queue):
                    if not test_task_queue.empty():
                        queue_had_items = True; print(f"\n[測試模式] 佇列 ({q_type_str}) 中的內容 (最多顯示5項):")
                        count = 0
                        while count < 5:
                            try: item = test_task_queue.get_nowait(); print(f"  - {item}"); count += 1
                            except queue.Empty: break
                            except Exception as e_get: print(f"    讀取佇列項目時出錯: {e_get}"); break
                        if hasattr(test_task_queue, 'qsize'):
                            remaining_items = test_task_queue.qsize()
                            if remaining_items > 0: print(f"  ... 以及其他 {remaining_items} 項。")
                        elif count == 5: print(f"  ... 可能還有更多項目。")
                    else: print(f"\n[測試模式] 任務佇列 ({type(test_task_queue).__name__}) 為空。")
                else: print(f"\n[測試模式] test_task_queue 不是預期的佇列類型: {type(test_task_queue)}。")
            except Exception as e_queue_access:
                print(f"‼️ [測試模式] 最終檢查佇列內容時發生錯誤: {e_queue_access}")
                if not queue_had_items : print(f"\n[測試模式] 任務佇列 ({type(test_task_queue).__name__ if 'test_task_queue' in locals() else '未知類型'}) 的最終狀態未知。")
        if mp_manager_created and 'mp_manager' in locals() and hasattr(mp_manager, 'shutdown'):
            print("ℹ️ [測試模式] 嘗試關閉 multiprocessing Manager...")
            try: mp_manager.shutdown(); print("✅ [測試模式] multiprocessing Manager 已成功關閉。")
            except Exception as e_shutdown: print(f"‼️ [測試模式] 關閉 multiprocessing Manager 時發生錯誤: {e_shutdown}")
        elif not mp_manager_created : print("ℹ️ [測試模式] 未創建或未使用 multiprocessing Manager，無需關閉。")
    print("\n[測試模式] 下載流程執行完畢。"); sys.exit(0)

if __name__ == "__main__":
    main()
