# -*- coding: utf-8 -*-
# 串流供應器主執行檔 (v20.1 非同步版本)
import os
import sys
import argparse
import asyncio
import aiohttp
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional, Any
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

# --- 自定義異常 ---
class StreamDownloadError(Exception):
    """串流下載過程中發生的錯誤"""
    def __init__(self, message: str, url: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.url = url
        self.status_code = status_code

# --- 核心串流邏輯 (非同步版本) ---
async def get_stream_from_url(url: str, session: aiohttp.ClientSession) -> aiohttp.StreamReader:
    """
    從指定的 URL 獲取數據串流。

    Args:
        url: 要下載的 URL。
        session: 用於發起請求的 aiohttp.ClientSession 物件。

    Returns:
        aiohttp.StreamReader 物件，代表 HTTP 回應的內容串流。

    Raises:
        StreamDownloadError: 如果請求失敗或回應不符合預期。
    """
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
            content_type = response.headers.get('Content-Type', '').lower()

            if response.status == 200:
                # 根據需求，TAIFEX 的 .zip 檔案 Content-Type 通常是 'application/zip' 或 'application/octet-stream'
                # 如果是 'text/html'，通常表示該日期沒有數據 (期交所風格)
                if 'text/html' in content_type:
                    # 讀取少量內容以確認是否為期交所的 "查無資料" 頁面
                    # 這裡不直接完整讀取，而是嘗試 peek，但 aiohttp StreamReader 沒有直接 peek
                    # 因此，我們這裡先假設 text/html 就是一個明確的失敗信號，符合原始邏輯
                    raise StreamDownloadError(
                        f"內容類型為 HTML，可能表示該日期無資料或頁面錯誤。",
                        url=url,
                        status_code=response.status
                    )

                # 嚴禁讀取 response.read() 將內容完整載入記憶體
                return response.content # response.content 就是 StreamReader
            else:
                raise StreamDownloadError(
                    f"HTTP 錯誤狀態：{response.status}",
                    url=url,
                    status_code=response.status
                )
    except aiohttp.ClientError as e: # 捕獲 aiohttp 的通用客戶端錯誤，例如 TimeoutError, ConnectionError
        raise StreamDownloadError(f"請求期間發生客戶端錯誤: {e}", url=url) from e
    except asyncio.TimeoutError: # 確保 TimeoutError 被正確捕獲並包裝
        raise StreamDownloadError(f"請求超時", url=url)


async def stream_data(
    start_date_dt: datetime,
    end_date_dt: datetime,
    data_types_to_download: Dict[str, bool]
) -> List[Tuple[str, str, str, Optional[aiohttp.StreamReader], Optional[str]]]:
    """
    為指定的日期範圍和數據類型非同步獲取數據串流。

    Args:
        start_date_dt: 開始日期。
        end_date_dt: 結束日期。
        data_types_to_download: 一個字典，鍵為數據類型，值為是否下載該類型。

    Returns:
        一個元組列表，每個元組包含:
        (日期字串, 任務鍵名, 檔案名稱, aiohttp.StreamReader 物件 (成功時) 或 None, 錯誤訊息 (失敗時) 或 None)
    """
    print("======================================================")
    print("       🚀 TAIFEX 非同步數據串流供應器啟動中...        ")
    print("======================================================\n")

    date_range = [start_date_dt + timedelta(days=x) for x in range((end_date_dt - start_date_dt).days + 1)]

    TASKS_CONFIG = {
        'futures_trades': {'url_template': 'https://www.taifex.com.tw/file/taifex/Dailydownload/DailydownloadCSV/Daily_{}.zip'},
        'futures_summary': {'url_template': 'https://www.taifex.com.tw/file/taifex/Daily/Daily_{}.zip'},
        'options_trades': {'url_template': 'https://www.taifex.com.tw/file/taifex/OptionsDailydownload/OptionsDailydownloadCSV/OptionsDaily_{}.zip'},
        'options_summary': {'url_template': 'https://www.taifex.com.tw/file/taifex/OptionsDaily/OptionsDaily_{}.zip'},
        'institutional_investors': {'url_template': 'https://www.taifex.com.tw/file/taifex/CHINESE/3/3_1_1_{}.zip'},
        'put_call_ratio': {'url_template': 'https://www.taifex.com.tw/file/taifex/PCRatio/PCRatio_{}.zip'},
        'final_settlement_price': {'url_template': 'https://www.taifex.com.tw/file/taifex/CHINESE/5/FSP_{}.zip'},
    }

    active_tasks_definitions = {
        task_key: TASKS_CONFIG[task_key]
        for task_key, should_download in data_types_to_download.items()
        if should_download and task_key in TASKS_CONFIG
    }

    if not active_tasks_definitions:
        print("ℹ️ 沒有選擇任何數據類型進行串流。")
        return []

    results: List[Tuple[str, str, str, Optional[aiohttp.StreamReader], Optional[str]]] = []

    async with aiohttp.ClientSession(headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}) as session:
        tasks_to_run = []
        for current_date in date_range:
            date_str_for_url = current_date.strftime('%Y_%m_%d')
            date_str_for_log = current_date.strftime('%Y-%m-%d')

            for task_key, params in active_tasks_definitions.items():
                url = params['url_template'].format(date_str_for_url)
                file_name = os.path.basename(url) # 仍然獲取檔案名稱以供參考
                tasks_to_run.append(
                    (date_str_for_log, task_key, file_name, get_stream_from_url(url, session))
                )

        # 使用 asyncio.gather 執行所有串流獲取任務
        # gather 返回的結果順序與輸入的 awaitables 順序一致
        # 但我們需要處理每個任務可能引發的異常

        print(f"⏳ 準備請求 {len(tasks_to_run)} 個數據串流...")

        for date_str, task_key, file_name, coro in tasks_to_run:
            stream_reader = None
            error_message = None
            task_display_name = task_key.replace('_', ' ').title()
            log_symbol = "❓"
            try:
                stream_reader = await coro
                log_symbol = "✅ (串流已獲取)"
                print(f"  [{date_str}] {task_display_name:<30} -> {file_name:<30} ... {log_symbol}")
            except StreamDownloadError as e:
                error_message = str(e)
                if e.status_code == 404:
                    log_symbol = "➖ (未找到)"
                elif "HTML" in error_message:
                    log_symbol = "⚠️ (HTML內容)"
                elif "超時" in error_message:
                     log_symbol = "❌ (超時)"
                elif e.status_code:
                    log_symbol = f"❌ (HTTP {e.status_code})"
                else:
                    log_symbol = "❌ (請求錯誤)"
                print(f"  [{date_str}] {task_display_name:<30} -> {file_name:<30} ... {log_symbol} ({error_message})")
            except Exception as e: # 其他未預期錯誤
                error_message = f"未預期錯誤: {str(e)}"
                log_symbol = "💥 (內部錯誤)"
                print(f"  [{date_str}] {task_display_name:<30} -> {file_name:<30} ... {log_symbol} ({error_message})")

            results.append((date_str, task_key, file_name, stream_reader, error_message))

    print("\n======================================================")
    print("           🎉 所有非同步串流請求處理完畢！ 🎉           ")
    print("======================================================")
    print("\n**圖例說明**：")
    print("✅: 串流已獲取 | ➖: 未找到 | ⚠️: HTML內容 | ❌: 錯誤 | 💥: 內部錯誤")
    return results

async def main():
    parser = argparse.ArgumentParser(description="TAIFEX 非同步數據串流供應器：從期交所官方網站獲取指定日期範圍的數據串流。")

    parser.add_argument("--start-date", required=True, help="開始日期 (格式: YYYY-MM-DD)。")
    parser.add_argument("--end-date", required=True, help="結束日期 (格式: YYYY-MM-DD)。")
    # 移除 --output-path 和 --sleep

    data_types_choices_map = {
        'futures_trades': '期貨逐筆成交', 'options_trades': '選擇權逐筆成交',
        'institutional_investors': '三大法人交易概況', 'put_call_ratio': '選擇權 Put/Call Ratio',
        'futures_summary': '期貨每日交易行情', 'options_summary': '選擇權每日交易行情',
        'final_settlement_price': '最後結算價'
    }
    for key, desc in data_types_choices_map.items():
        parser.add_argument(f"--{key.replace('_', '-')}", action='store_true', help=f"是否串流 {desc}。")

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
        print("ℹ️ 提示：未選擇任何數據類型進行串流。", file=sys.stderr)
        # 在此處，我們不直接 sys.exit(0)，因為此模組可能被其他程式導入
        # 若作為主程式執行，沒有選擇類型則 stream_data 會返回空列表，然後主程式結束
        # return # 如果 main 不打算做任何事，可以提前返回

    stream_results = await stream_data(start_dt, end_dt, selected_data_types_dict)

    print("\n--- 串流結果演示 ---")
    if not stream_results:
        print("沒有獲取到任何串流。")
    else:
        for i, (date_str, task_key, file_name, stream, error) in enumerate(stream_results):
            print(f"\n結果 {i+1}:")
            print(f"  日期: {date_str}, 類型: {task_key}, 檔案名: {file_name}")
            if stream:
                print(f"  狀態: 成功 ✅")
                try:
                    # 演示：嘗試讀取一小塊數據
                    chunk = await stream.readchunk(1024) # 讀取最多 1024 bytes
                    if chunk:
                        print(f"  演示: 成功從串流中讀取 {len(chunk)} 位元組。")
                        # 注意：不要在這裡保持串流開啟太久，或讀取整個串流
                        # 實際應用中，串流的消費者會負責處理它
                    else:
                        print(f"  演示: 從串流中讀取到空數據塊 (可能已結束或檔案為空)。")
                    # 釋放串流，防止資源洩漏
                    # 在實際應用中，StreamReader 的消費者應該負責 release
                    # 但在此演示 main 函數中，我們獲取了它，所以我們示範釋放
                    if hasattr(stream, 'release') and callable(stream.release):
                         await stream.release()
                    print(f"  串流已釋放 (演示目的)。")

                except Exception as e:
                    print(f"  演示時讀取串流 '{file_name}' 失敗: {e}")
            elif error:
                print(f"  狀態: 失敗 ❌")
                print(f"  錯誤: {error}")
            else:
                print(f"  狀態: 未知 (無串流也無錯誤訊息)")

    # sys.exit(0) # 若作為腳本執行，可以保留以指示成功結束
    # 但作為可導入模組，通常不應有 sys.exit

if __name__ == "__main__":
    # asyncio.run(main()) # Python 3.7+
    # For older versions or specific event loop management:
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main())
    finally:
        # Consider loop cleanup if necessary, though run_until_complete usually handles it.
        pass
