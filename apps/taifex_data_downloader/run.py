# -*- coding: utf-8 -*-
# 採集官主執行檔
import os
import sys
import argparse
import time
from datetime import datetime, timedelta
import warnings # 用於忽略 openpyxl 的警告，如果原始腳本有
import asyncio
import aiohttp
import multiprocessing # 新增
import queue # 新增，用於 queue.Full 例外
from typing import List, Dict, Optional, Any
# from tqdm.asyncio import tqdm as async_tqdm # tqdm 對 asyncio 的支持可能需要這個
# 或者在 gather 後手動更新 tqdm
from tqdm import tqdm


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

# --- 核心下載邏輯 (非同步版本) ---

async def download_single_file_async(
    session: aiohttp.ClientSession,
    url: str,
    folder_path: str,
    file_name: str,
    task_key: str, # 新增: 用於決定 file_type
    task_queue: Optional[multiprocessing.Queue] = None, # 修改類型提示
    semaphore: Optional[asyncio.Semaphore] = None
) -> Dict[str, Any]:
    """
    非同步下載單一檔案。
    返回狀態字典: {'url': url, 'local_path': local_file_path, 'status': status_str, 'error': error_msg}
    """
    if semaphore:
        await semaphore.acquire()

    os.makedirs(folder_path, exist_ok=True)
    local_file_path = os.path.join(folder_path, file_name)
    status_str = "unknown_error"
    error_msg = None

    if os.path.exists(local_file_path) and os.path.getsize(local_file_path) > 0:
        status_str = 'exists'
    else:
        try:
            # print(f"開始下載: {url}")
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as response: # 總超時60秒
                content_type = response.headers.get('Content-Type', '').lower()

                if response.status == 200:
                    if 'text/html' in content_type:
                        status_str = 'not_found_html_content'
                        error_msg = "伺服器返回 HTML 內容 (疑似軟404)"
                    else:
                        content = await response.read()
                        if content:
                            with open(local_file_path, 'wb') as f:
                                f.write(content)
                            if os.path.getsize(local_file_path) > 0: # 再次確認
                                status_str = 'success'
                            else: # 寫入後檔案為空
                                status_str = 'error_empty_file_after_write'
                                error_msg = "檔案寫入後為空"
                                try: os.remove(local_file_path)
                                except OSError: pass
                        else: # response.read() 返回空
                            status_str = 'not_found_empty_content'
                            error_msg = "伺服器返回內容為空"
                elif response.status == 404:
                    status_str = 'not_found'
                    error_msg = f"HTTP 404 - 檔案不存在"
                else:
                    status_str = f'error_http_{response.status}'
                    error_msg = f"HTTP 錯誤狀態: {response.status}"
        except asyncio.TimeoutError:
            status_str = 'error_timeout'
            error_msg = "下載超時"
        except aiohttp.ClientError as e: # 更通用的 aiohttp 客戶端錯誤
            status_str = 'error_aiohttp_client'
            error_msg = f"aiohttp 客戶端錯誤: {type(e).__name__} - {str(e)}"
        except Exception as e:
            status_str = 'error_exception'
            error_msg = f"下載過程中發生未預期錯誤: {type(e).__name__} - {str(e)}"
            if os.path.exists(local_file_path): # 如果出錯但檔案已部分創建，嘗試刪除
                try: os.remove(local_file_path)
                except OSError: pass

    result = {'url': url, 'local_path': local_file_path, 'status': status_str, 'error': error_msg, 'file_name': file_name}

    if status_str == 'success' and task_queue is not None:
        try:
            # 依照計畫書 v18.0 要求，修改入列資料結構
            # file_type 使用 task_key (例如 'futures_trades')
            item_to_queue = {
                "file_path": local_file_path,
                "file_type": task_key
            }
            # 使用 task_queue.put()。注意：這是一個阻塞操作。
            # 在 asyncio 事件迴圈中直接呼叫阻塞操作通常不建議。
            # 理想情況下，應使用 asyncio.to_thread (Python 3.9+) 或類似機制。
            # task_queue.put(item_to_queue, block=False) # 或者使用 put_nowait，但需要處理 queue.Full
            # 根據計畫書指引，先使用 .put()，假設協調器能處理。
            # 如果協調器期望非阻塞，則應使用 put_nowait 並處理 queue.Full。
            task_queue.put(item_to_queue)
            # print(f"成功將 {item_to_queue} 加入任務佇列。") # 調試日誌
        except queue.Full:
            result['queue_error'] = "任務佇列已滿 (queue.Full)，無法加入。"
            print(f"錯誤: 任務佇列已滿，無法將 {local_file_path} (類型: {task_key}) 加入。", file=sys.stderr)
        except Exception as e_queue:
            # 如果放入佇列失敗，記錄錯誤，但不影響下載本身的狀態
            result['queue_error'] = f"放入任務佇列失敗: {e_queue}"
            print(f"錯誤: 無法將 {local_file_path} (類型: {task_key}) 放入任務佇列: {e_queue}", file=sys.stderr)

    if semaphore:
        semaphore.release()

    # print(f"完成下載: {url} -> {status_str}")
    return result


async def download_data_async(
    start_date: str, # 修改：依照計畫書，從 datetime 改為 str
    end_date: str,   # 修改：依照計畫書，從 datetime 改為 str
    data_types_to_download: dict, # 注意：計畫書範例為 list, 但此處保留 dict 以維持與 TASKS_CONFIG 的一致性及現有邏輯的兼容性。協調器需傳遞 dict。
    output_path: str, # 修改：依照計畫書，改名 local_output_base_path
    task_queue: multiprocessing.Queue, # 修改：依照計畫書，明確類型
    max_concurrent_downloads: int = 10, # 並行下載限制 (保留預設值)
    sleep_time_between_batches: float = 0.1 # 批次間的短暫延遲 (保留預設值)
):
    """
    主非同步下載程序。
    """
    # 將日期字串轉換為 datetime 物件
    try:
        start_date_dt = datetime.strptime(start_date, '%Y-%m-%d')
        end_date_dt = datetime.strptime(end_date, '%Y-%m-%d')
    except ValueError:
        print("❌ 錯誤：download_data_async 收到的日期格式不正確，應為 'YYYY-MM-DD'。", file=sys.stderr)
        # 可以考慮拋出例外或返回錯誤狀態
        return [] # 或其他錯誤指示

    if end_date_dt < start_date_dt:
        print("❌ 錯誤：download_data_async 收到的結束日期早於開始日期。", file=sys.stderr)
        return []

    print("======================================================")
    print("      🚀 TAIFEX 非同步數據下載引擎啟動中... (v18.0 相容模式)         ")
    print("======================================================\n")
    print(f"🗂️ 所有下載的檔案將會儲存在以下本地路徑：\n➡️ {os.path.abspath(output_path)}\n")

    date_range = [start_date_dt + timedelta(days=x) for x in range((end_date_dt - start_date_dt).days + 1)]

    TASKS_CONFIG = { # 與舊版一致
        'futures_trades': {'folder': 'A_Core_Trading/Futures_Trades', 'url_template': 'https://www.taifex.com.tw/file/taifex/Dailydownload/DailydownloadCSV/Daily_{}.zip'},
        'futures_summary': {'folder': 'A_Core_Trading/Futures_Summary', 'url_template': 'https://www.taifex.com.tw/file/taifex/Daily/Daily_{}.zip'},
        'options_trades': {'folder': 'A_Core_Trading/Options_Trades', 'url_template': 'https://www.taifex.com.tw/file/taifex/OptionsDailydownload/OptionsDailydownloadCSV/OptionsDaily_{}.zip'},
        'options_summary': {'folder': 'A_Core_Trading/Options_Summary', 'url_template': 'https://www.taifex.com.tw/file/taifex/OptionsDaily/OptionsDaily_{}.zip'},
        'institutional_investors': {'folder': 'B_Market_Sentiment/Institutional_Investors', 'url_template': 'https://www.taifex.com.tw/file/taifex/CHINESE/3/3_1_1_{}.zip'},
        'put_call_ratio': {'folder': 'B_Market_Sentiment/Put_Call_Ratio', 'url_template': 'https://www.taifex.com.tw/file/taifex/PCRatio/PCRatio_{}.zip'},
        'final_settlement_price': {'folder': 'C_Settlement/Final_Settlement_Price', 'url_template': 'https://www.taifex.com.tw/file/taifex/CHINESE/5/FSP_{}.zip'},
    }

    download_tasks_to_create = []
    for date_val in date_range:
        date_str_for_url = date_val.strftime('%Y_%m_%d')
        for task_key, should_download in data_types_to_download.items():
            if should_download and task_key in TASKS_CONFIG:
                config = TASKS_CONFIG[task_key]
                url = config['url_template'].format(date_str_for_url)
                file_name = os.path.basename(url)
                folder_path = os.path.join(output_path, config['folder']) # 使用 output_path
                # 將 task_key 加入到任務參數中，以便 download_single_file_async 知道 file_type
                download_tasks_to_create.append({'url': url, 'folder_path': folder_path, 'file_name': file_name, 'task_key': task_key})

    if not download_tasks_to_create:
        print("ℹ️ 沒有有效的下載任務被建立。請檢查日期範圍和選擇的數據類型。")
        return []

    all_results = []
    semaphore = asyncio.Semaphore(max_concurrent_downloads) # 控制並行數

    # 設定 aiohttp ClientSession 的 User-Agent
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
    async with aiohttp.ClientSession(headers=headers) as session:
        tasks_for_gather = []
        for task_params in download_tasks_to_create:
            # 將 task_queue 和 task_key 傳遞給 download_single_file_async
            coro = download_single_file_async(
                session,
                task_params['url'],
                task_params['folder_path'],
                task_params['file_name'],
                task_params['task_key'], # 傳入 task_key
                task_queue,
                semaphore
            )
            tasks_for_gather.append(coro)

        # 使用 tqdm 包裝 asyncio.gather 以顯示進度
        # all_results = await async_tqdm.gather(*tasks_for_gather, desc="📥 非同步下載進度")
        # tqdm.asyncio 可能會有問題，改用手動更新或在完成後處理

        print(f"總共建立 {len(tasks_for_gather)} 個下載任務，並行上限 {max_concurrent_downloads}。")

        # 分批執行並行任務，以允許小的延遲，避免瞬間請求過多
        # 這裡可以簡化為直接 gather，因為 semaphore 已經控制了並行數
        # 如果要分批次，可以這樣：
        # batch_size = max_concurrent_downloads * 2 # 例如一次 gather 這麼多任務
        # for i in range(0, len(tasks_for_gather), batch_size):
        #     batch = tasks_for_gather[i:i+batch_size]
        #     batch_results = await asyncio.gather(*batch)
        #     all_results.extend(batch_results)
        #     if sleep_time_between_batches > 0 and i + batch_size < len(tasks_for_gather):
        #         await asyncio.sleep(sleep_time_between_batches)

        # 直接 gather 所有任務，由 semaphore 控制實際並行
        # 並使用 tqdm 手動更新進度條
        pbar = tqdm(total=len(tasks_for_gather), desc="📥 非同步下載進度", unit="檔")
        for coro_future in asyncio.as_completed(tasks_for_gather): # as_completed 可以逐個獲取結果
            result = await coro_future
            all_results.append(result)
            pbar.update(1)
            pbar.set_postfix_str(f"{os.path.basename(result.get('local_path', 'N/A'))} -> {result.get('status', 'N/A')}")
            # 短暫釋放控制權，讓其他任務可以運行，也模擬微小延遲
            await asyncio.sleep(0.01)
        pbar.close()

    # 輸出總結
    print("\n======================================================")
    print("             🎉 全部非同步下載任務執行完畢！ 🎉             ")
    print("======================================================")

    success_count = sum(1 for r in all_results if r['status'] == 'success')
    exists_count = sum(1 for r in all_results if r['status'] == 'exists')
    not_found_count = sum(1 for r in all_results if r['status'].startswith('not_found'))
    error_count = sum(1 for r in all_results if r['status'].startswith('error'))

    print(f"總結：成功 {success_count}，已存在 {exists_count}，未找到 {not_found_count}，錯誤 {error_count}")

    for r in all_results:
        if r['status'] not in ['success', 'exists']:
            log_msg = f"  - {r.get('file_name', '未知檔案')}: {r['status']}"
            if r.get('error'):
                log_msg += f" ({r['error']})"
            print(log_msg)
        if 'queue_error' in r: # 檢查是否有佇列錯誤並打印
             print(f"  - {r.get('file_name', '未知檔案')}: 佇列錯誤 - {r['queue_error']}")


    print(f"\n所有檔案嘗試儲存至您的指定本地路徑：\n➡️ **{os.path.abspath(output_path)}**") # 使用 output_path
    return all_results


def main():
    # 此 main 函式主要用於獨立測試。在 v18.0 架構下，download_data_async 將由協調器作為模組導入並調用。
    # 因此，此處的 asyncio.run(...) 將被註解掉或移除。
    parser = argparse.ArgumentParser(description="TAIFEX 非同步數據採集官 (v18.0 支援模組)：從期交所官方網站批量下載指定日期範圍的原始數據至本地。")

    parser.add_argument("--start-date", required=True, help="下載開始日期 (格式: YYYY-MM-DD)。")
    parser.add_argument("--end-date", required=True, help="下載結束日期 (格式: YYYY-MM-DD)。")
    parser.add_argument("--output-path", required=True, help="下載檔案的本地儲存根目錄。") # 參數名與 download_data_async 一致
    parser.add_argument("--max-concurrent", type=int, default=10, help="最大並行下載數 (預設: 10)。")
    parser.add_argument("--sleep-batch", type=float, default=0.05, help="每個下載任務完成後的小延遲 (預設: 0.05s)。")


    data_types_choices_map = { # 與舊版一致
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
        print("ℹ️ 提示：未選擇任何數據類型進行下載。若要下載，請至少指定一個數據類型參數 (例如 --futures-trades)。", file=sys.stderr)
        sys.exit(0)

    # 為了獨立測試 downloader，這裡的 task_queue 暫時設為 None 或一個模擬的 Queue
    # 在 v18.0 協調器中，此 main 函式不會被執行，download_data_async 會被直接調用。
    # 因此，以下 asyncio.run(...) 部分將被註解。

    # print("警告：此 main() 函式僅供獨立測試 downloader。在 v18.0 整合模式下不會執行。")
    # print("若要測試，請確保提供所有必要參數。task_queue 將為 None。")

    # # 模擬的 task_queue (可選，用於測試佇列功能)
    # # from multiprocessing import Manager
    # # manager = Manager()
    # # test_q = manager.Queue()

    # asyncio.run(download_data_async(
    #     start_date=args.start_date, # 傳遞字串
    #     end_date=args.end_date,     # 傳遞字串
    #     output_path=args.output_path,
    #     data_types_to_download=selected_data_types_dict,
    #     task_queue=None, # 在獨立測試中設為 None 或使用 test_q
    #     max_concurrent_downloads=args.max_concurrent,
    #     sleep_time_between_batches=args.sleep_batch
    # ))
    # sys.exit(0)
    print("程式進入點 (main) 已被v18.0架構取代。請透過協調器呼叫 `download_data_async`。")
    sys.exit(0)

if __name__ == "__main__":
    main()
