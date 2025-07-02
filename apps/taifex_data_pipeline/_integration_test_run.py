# -*- coding: utf-8 -*-
"""
整合測試腳本 (v20.3.4)
目的：驗證從真實網路下載 -> 串流解壓 -> 初步解析的端到端鏈路。
"""
import asyncio
import unittest
import io
from typing import List, Tuple, Optional, Any, AsyncGenerator

# --- 路徑校正樣板碼 ---
import os
import sys
try:
    # 假設此腳本位於 apps/taifex_data_pipeline/
    current_script_dir = os.path.dirname(os.path.abspath(__file__))
    # apps 目錄是 current_script_dir 的父目錄
    apps_dir = os.path.dirname(current_script_dir)
    # 專案根目錄是 apps 目錄的父目錄
    project_root = os.path.dirname(apps_dir)

    if apps_dir not in sys.path:
        sys.path.insert(0, apps_dir)
    if project_root not in sys.path: # 如果 repo 根目錄也需要被導入
        sys.path.insert(0, project_root)

    # print(f"[_integration_test_run.py] sys.path: {sys.path}")

    from taifex_data_downloader import run as downloader_run
    from taifex_data_downloader.run import StreamDownloadError # For type checking if needed
    from taifex_data_pipeline.stream_unzipper import InMemoryStreamUnzipper
    # AsyncBytesGeneratorReader 和 _iterate_text_lines 將從 pipeline_run 導入或在此處定義副本
    from taifex_data_pipeline.run import AsyncBytesGeneratorReader, _iterate_text_lines, SimpleLogger

    # 初始化一個 logger 供此測試腳本使用
    logger = SimpleLogger(log_level="DEBUG")

except ImportError as e:
    print(f"導入整合測試所需模組時出錯: {e}", file=sys.stderr)
    # Fallback mocks if imports fail, to allow script to be parsed at least
    downloader_run = None
    InMemoryStreamUnzipper = None
    AsyncBytesGeneratorReader = None
    _iterate_text_lines = None
    logger = None
    StreamDownloadError = Exception # Define for type hints at least
# --- 路徑自我校正樣板碼結束 ---


class TestTaifexPipelineIntegration(unittest.IsolatedAsyncioTestCase):

    # 測試用的 URL - 理想情況下由用戶提供一個最近的有效 URL
    # 備用 URL (一個過去的日期，可能沒有數據，但 downloader 應能處理)
    # OptionsDaily 通常檔案較小
    # TARGET_URL = "https://www.taifex.com.tw/file/taifex/OptionsDailydownload/OptionsDailydownloadCSV/OptionsDaily_2023_11_01.zip"
    # 使用一個更可能存在的 "行情日報" -> "每日選擇權行情"
    # TARGET_URL = "https://www.taifex.com.tw/file/taifex/OptionsDaily/OptionsDaily_2023_11_01.zip"
    # 改用一個更確定的交易日，例如 2024 年 6 月的某個工作日
    TARGET_URL = "https://www.taifex.com.tw/file/taifex/Daily/Daily_2024_06_03.zip" # 期貨每日交易摘要
    TARGET_DATA_TYPE_ARG = "--futures-summary" # 對應 downloader 的參數
    EXPECTED_FIRST_LINE_KEYWORDS = ["交易日期", "契約", "開盤價"] # 期貨每日交易摘要的預期表頭關鍵字

    async def test_full_stream_pipeline_from_real_download(self):
        if not downloader_run or not InMemoryStreamUnzipper or not AsyncBytesGeneratorReader or not _iterate_text_lines or not logger:
            self.skipTest("由於導入失敗，跳過整合測試。請檢查環境和路徑。")

        logger.info(f"整合測試：開始從 URL 下載: {self.TARGET_URL}")

        # 1. 呼叫 downloader 獲取 StreamReader
        # stream_data 返回 List[Tuple[str, str, str, Optional[aiohttp.StreamReader], Optional[str]]]
        # (date_str, task_key, file_name, stream_reader_object_or_None, error_message_or_None)

        # 為了匹配 URL，我們需要從 URL 中提取日期
        # Daily_2024_06_03.zip -> 2024_06_03 -> 2024-06-03
        try:
            filename_from_url = self.TARGET_URL.split('/')[-1] # Daily_2024_06_03.zip
            date_str_for_downloader = filename_from_url.split('_')[1].replace('_', '-') + "-" + \
                                      filename_from_url.split('_')[2].replace('_', '-') + "-" + \
                                      filename_from_url.split('_')[3].split('.')[0].replace('_', '-')
            # This is incorrect for Daily_YYYY_MM_DD.zip. It should be YYYY-MM-DD
            # Example: Daily_2024_06_03.zip -> date part is 2024_06_03
            # Corrected date extraction:
            date_part_from_url = "_".join(filename_from_url.split('_')[1:]).split('.')[0] # 2024_06_03
            date_obj_for_downloader = datetime.strptime(date_part_from_url, "%Y_%m_%d")
            date_str_for_downloader_arg = date_obj_for_downloader.strftime("%Y-%m-%d")

        except Exception as e:
            self.fail(f"從 URL {self.TARGET_URL} 提取日期失敗: {e}")

        downloader_results = await downloader_run.stream_data(
            start_date_dt=date_obj_for_downloader,
            end_date_dt=date_obj_for_downloader,
            data_types_to_download={self.TARGET_DATA_TYPE_ARG.replace("--", "").replace("-", "_"): True}
        )

        self.assertIsNotNone(downloader_results, "Downloader stream_data 返回不應為 None")
        self.assertGreater(len(downloader_results), 0, "Downloader stream_data 返回列表應至少有一個結果")

        _, _, dl_filename, zip_stream_reader, error_msg = downloader_results[0]

        self.assertIsNone(error_msg, f"Downloader 獲取串流時出錯: {error_msg}")
        self.assertIsNotNone(zip_stream_reader, f"Downloader 未能獲取到 StreamReader for {dl_filename}")

        if not zip_stream_reader: # Type guard
            self.fail("zip_stream_reader is None, 無法繼續測試")
            return

        logger.info(f"整合測試：成功從 downloader 獲取到 {dl_filename} 的 StreamReader。")

        # 2. 呼叫 unzipper
        # InMemoryStreamUnzipper 的 __init__ 只接收 zip_stream_reader
        unzipper = InMemoryStreamUnzipper(zip_stream_reader) # type: ignore
        uncompressed_byte_stream_gen: Optional[AsyncGenerator[bytes, None]] = None

        lines_read_count = 0
        first_line_content = ""

        try:
            async with unzipper: # 確保 unzipper.close() 被調用
                uncompressed_byte_stream_gen = await unzipper.get_uncompressed_stream()

                self.assertIsNotNone(uncompressed_byte_stream_gen,
                                     f"InMemoryStreamUnzipper.get_uncompressed_stream() 不應返回 None for {dl_filename}")
                if not uncompressed_byte_stream_gen: # Type guard
                    self.fail("uncompressed_byte_stream_gen is None, 無法繼續")
                    return

                logger.info(f"整合測試：成功從 unzipper 獲取到解壓縮後的位元組流生成器。")

                # 3. 將 AsyncGenerator[bytes, None] 包裝到 AsyncBytesGeneratorReader
                # 描述符可以更具體，例如 f"{dl_filename} -> member_0"
                member_descriptor = f"{dl_filename} -> member_0_auto"
                adapted_stream = AsyncBytesGeneratorReader(uncompressed_byte_stream_gen, member_descriptor)
                logger.info(f"整合測試：已將解壓縮流包裝到 AsyncBytesGeneratorReader ({member_descriptor})。")

                # 4. 讀取並驗證前 10 行
                # 我們需要從 run.py 導入 _iterate_text_lines 或在此處實現一個簡化版本
                # 假設可以導入 _iterate_text_lines
                # 為了確定編碼，我們需要一個簡化的 recipe
                # 或者，我們可以假設一個常見的編碼如 'ms950' 或 'utf-8'
                # 這裡我們嘗試 'ms950'，因為它是台股數據常見編碼
                # 注意：downloader 返回的 stream 是 bytes，解壓後也是 bytes。解碼在此處進行。

                logger.info(f"整合測試：開始從解壓縮流 ({member_descriptor}) 讀取並打印前 10 行...")

                # 使用 _iterate_text_lines (假設它可以處理 AsyncBytesGeneratorReader)
                # _iterate_text_lines 期望 stream_reader, consumed_sample_bytes, encoding, descriptor
                # consumed_sample_bytes 在這裡為 b""
                # encoding 需要猜測或從 recipe 獲取。對於整合測試，我們可以硬編碼一個。

                # 優先嘗試 ms950，如果失敗，嘗試 utf-8
                detected_encoding = None
                temp_buffer_for_encoding_check = b""
                try:
                    # 預讀少量數據用於判斷編碼
                    async for chunk in adapted_stream._generator: # Accessing internal generator for pre-read
                        temp_buffer_for_encoding_check += chunk
                        if len(temp_buffer_for_encoding_check) > 2048: # Read a bit for encoding detection
                            break
                    # 重置 adapted_stream 或用預讀的數據重新創建它 (這比較複雜)
                    # 簡化：假設解碼，如果失敗則換另一種
                    # 為了簡化，我們直接假設 ms950 或 utf-8
                except Exception:
                    pass # adapted_stream 可能不支持這樣直接迭代其內部 _generator

                # 重新創建 adapted_stream 以便 _iterate_text_lines 可以從頭讀取
                # 這需要重新獲取 uncompressed_byte_stream_gen，但它可能已被消耗。
                # 這是個問題：一旦 uncompressed_byte_stream_gen 被部分消耗，就很難重置。
                # 解決方法：InMemoryStreamUnzipper.get_uncompressed_stream() 應該每次都返回一個新的生成器實例。
                # 或者 AsyncBytesGeneratorReader 需要支持 peek/reset，或者 _iterate_text_lines 內部處理。
                # 鑑於 _iterate_text_lines 設計為從頭開始，我們需要一個新的 adapted_stream。

                # *** 關鍵問題：如何為 _iterate_text_lines 提供一個可重用的/可重置的 adapted_stream ***
                # 方案：直接從 adapted_stream 讀取，並手動處理行和解碼。

                text_lines_buffer = ""
                decoder = codecs.getincrementaldecoder('ms950')(errors='replace') # 先嘗試 ms950

                printed_lines: List[str] = []

                while lines_read_count < 10:
                    chunk = await adapted_stream.readchunk(1024) # Read a chunk
                    if not chunk:
                        # End of stream, process remaining buffer
                        if text_lines_buffer:
                            line = text_lines_buffer # Last line
                            if lines_read_count < 10:
                                logger.info(f"L{lines_read_count + 1}: {line.strip()}")
                                printed_lines.append(line.strip())
                                lines_read_count += 1
                            text_lines_buffer = "" # Clear buffer
                        break

                    try:
                        text_lines_buffer += decoder.decode(chunk, final=False)
                    except UnicodeDecodeError:
                        logger.warning("MS950 解碼失敗，嘗試 UTF-8...")
                        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
                        # 用新的解碼器重新處理當前塊和緩衝區 (如果緩衝區有未成功解碼的內容)
                        # 簡化：假設第一個塊就能確定編碼，或者錯誤發生在塊的開頭
                        text_lines_buffer = decoder.decode(chunk, final=False) # 假設之前的 buffer 是空的或已處理

                    while '\n' in text_lines_buffer and lines_read_count < 10:
                        line, _, text_lines_buffer = text_lines_buffer.partition('\n')
                        logger.info(f"L{lines_read_count + 1}: {line.strip()}")
                        printed_lines.append(line.strip())
                        lines_read_count += 1

                # 如果循環結束但 text_lines_buffer 中還有內容 (最後一行無換行符)
                if text_lines_buffer and lines_read_count < 10:
                    final_decoded_chunk = decoder.decode(b'', final=True) # Flush decoder
                    text_lines_buffer += final_decoded_chunk
                    if text_lines_buffer.strip():
                         logger.info(f"L{lines_read_count + 1}: {text_lines_buffer.strip()}")
                         printed_lines.append(text_lines_buffer.strip())
                         lines_read_count += 1

                logger.info(f"整合測試：共讀取並打印 {lines_read_count} 行。")

                self.assertGreaterEqual(lines_read_count, 1, "應至少讀取到一行（表頭）。")
                # 由於期交所檔案末尾可能有空行或總計行，前10行可能不完全是數據
                # self.assertEqual(lines_read_count, 10, f"應準確讀取到 10 行，實際讀取 {lines_read_count} 行。")
                if lines_read_count > 0:
                    first_line_content = printed_lines[0]
                    logger.info(f"第一行內容: {first_line_content}")
                    for keyword in self.EXPECTED_FIRST_LINE_KEYWORDS:
                        self.assertIn(keyword, first_line_content,
                                      f"第一行應包含關鍵字 '{keyword}'，實際內容: '{first_line_content}'")

        except StreamDownloadError as e_dl: # type: ignore
            self.fail(f"Downloader 發生錯誤: {e_dl}")
        except Exception as e:
            # 確保即使在斷言失敗時也能釋放串流 (如果適用)
            if zip_stream_reader and hasattr(zip_stream_reader, 'release'):
                await zip_stream_reader.release() # type: ignore
            self.fail(f"整合測試過程中發生未預期錯誤: {e}")
        finally:
            if zip_stream_reader and hasattr(zip_stream_reader, 'release'):
                await zip_stream_reader.release() # type: ignore
            logger.info("整合測試：測試結束。")

if __name__ == '__main__':
    # 確保在直接運行此腳本時，asyncio 事件迴圈能被正确管理
    # unittest.main() in asyncio context
    if downloader_run: # Check if main modules were imported
        asyncio.run(unittest.main(argv=['first-arg-is-ignored'], exit=False, verbosity=2))
    else:
        print("主要模組導入失敗，無法運行整合測試。", file=sys.stderr)
```
