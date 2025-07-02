# -*- coding: utf-8 -*-
"""
整合測試腳本 (v20.3.4)
目的：驗證從真實網路下載 -> 串流解壓 -> 初步解析的端到端鏈路。
"""
import asyncio
import unittest
import io
from typing import List, Tuple, Optional, Any, AsyncGenerator
from datetime import datetime # <--- 新增導入
import codecs # <--- 新增導入

# --- 路徑校正樣板碼 ---
# (保持不變)
import os
import sys
import aiohttp # 新增導入 for get_stream_directly_from_url

# --- 自給自足的下載器與HTML解析器 ---
async def get_stream_with_content_type(session: aiohttp.ClientSession, url: str) -> Tuple[aiohttp.ClientResponse, str]:
    """
    使用提供的 session 下載數據並返回 ClientResponse 和 Content-Type。
    調用者負責管理 session 的生命週期。
    ClientResponse 也應由調用者在使用完畢後釋放。
    """
    # print(f"DEBUG: get_stream_with_content_type called for URL: {url}") # DEBUG
    response = await session.get(url)
    # print(f"DEBUG: Response status: {response.status} for URL: {url}") # DEBUG
    response.raise_for_status() # 如果狀態碼不是 2xx，則拋出異常
    content_type = response.headers.get('Content-Type', '').lower()
    # print(f"DEBUG: Content-Type: {content_type} for URL: {url}") # DEBUG
    return response, content_type


async def parse_csv_from_html(response: aiohttp.ClientResponse) -> AsyncGenerator[bytes, None]:
    """
    從 HTML 內容 (aiohttp.ClientResponse) 中提取 <pre> 標籤內的 CSV 數據，
    並將其作為 AsyncGenerator[bytes, None] 返回。
    """
    extracted_csv_text = "" # 用於調試
    try:
        html_text = await response.text(encoding='utf-8', errors='replace')
        logger.debug(f"HTML 解析器：接收到的 HTML (前 500 字元): {html_text[:500]}...") # 打印 HTML 頭部

        # 簡單的 <pre> 提取
        pre_start_tag = '<pre>'
        pre_end_tag = '</pre>'

        start_idx_find = html_text.find(pre_start_tag)
        if start_idx_find != -1:
            start_index = start_idx_find + len(pre_start_tag)
            end_index = html_text.find(pre_end_tag, start_index)
            if end_index != -1:
                extracted_csv_text = html_text[start_index:end_index].strip()
                logger.debug(f"HTML 解析器：從 <pre> 提取的 CSV 文本 (前 300 字元): {extracted_csv_text[:300]}...")
                if extracted_csv_text:
                    # 假設提取的 CSV 內容是 MS950 編碼兼容的，或者期交所<pre>輸出就是BIG5/MS950
                    csv_bytes = extracted_csv_text.encode('ms950', errors='replace')
                    logger.debug(f"HTML 解析器：CSV 文本編碼為 MS950 bytes，長度: {len(csv_bytes)}")
                    chunk_size = 8192
                    for i in range(0, len(csv_bytes), chunk_size):
                        yield csv_bytes[i:i+chunk_size]
                    # yield 完成後，產生器自然結束
                else:
                    logger.warning("HTML 解析器：從 <pre> 標籤提取的 CSV 文本為空。")
            else:
                logger.warning("HTML 解析器：找到了 <pre> 開始標籤，但未找到結束標籤。")
        else:
            logger.warning("HTML 解析器：未在 HTML 中找到 <pre> 開始標籤。")

        # 如果沒有 yield 任何數據 (例如 extracted_csv_text 為空或未找到標籤)，
        # 這個產生器將不產生任何值就結束，AsyncBytesGeneratorReader 會正確處理為 EOF。
        # 不需要額外的 if False: yield b""

    except Exception as e:
        logger.error(f"從 HTML 解析 CSV 時出錯: {e}")
        # 讓異常傳播，或者也可以選擇 yield b'' 來表示空流
    finally:
        if response and not response.closed:
             response.release()

# async def get_stream_directly_from_url(url: str, chunk_size: int = 8192) -> AsyncGenerator[bytes, None]:
#     """
#     一個內嵌於測試腳本的、自足的下載器。
#     它直接從 URL 下載數據，並以 AsyncGenerator[bytes, None] 的形式產生數據塊。
#     """
#     try:
#         while True:
#             chunk = await stream_reader.read(chunk_size)
#             if not chunk:
#                 break
#             yield chunk
#     except Exception as e:
#         logger.error(f"適配器從 aiohttp.StreamReader 讀取數據時出錯: {e}")
#         raise

try:
    current_script_dir = os.path.dirname(os.path.abspath(__file__))
    apps_dir = os.path.dirname(current_script_dir)
    project_root = os.path.dirname(apps_dir)
    if apps_dir not in sys.path: sys.path.insert(0, apps_dir)
    if project_root not in sys.path: sys.path.insert(0, project_root)

    # 不再導入 taifex_data_downloader
    # from taifex_data_downloader import run as downloader_run
    # from taifex_data_downloader.run import StreamDownloadError

    # InMemoryStreamUnzipper 現在直接在 run.py 中定義
    from taifex_data_pipeline.run import InMemoryStreamUnzipper, AsyncBytesGeneratorReader, SimpleLogger

    logger = SimpleLogger(log_level="DEBUG")

except ImportError as e:
    print(f"導入整合測試所需模組時出錯: {e}", file=sys.stderr)
    # downloader_run = None # 已移除
    InMemoryStreamUnzipper = None # type: ignore
    AsyncBytesGeneratorReader = None # type: ignore
    logger = None # type: ignore
    # StreamDownloadError = Exception # type: ignore # 已移除
# --- 路徑自我校正樣板碼結束 ---


class TestTaifexPipelineIntegration(unittest.IsolatedAsyncioTestCase):
    # Directive-v20.7: 自給自足測試
    TARGET_URL = "https://www.taifex.com.tw/data_gov/taifex_future_dat_2025_06_27.zip"
    EXPECTED_FIRST_LINE_KEYWORDS = ["成交日期", "商品代號", "到期月份(週別)", "成交時間", "成交價格", "成交數量(B+S)"]
    # TARGET_DATE_OBJ is no longer a class attribute, will be derived in test from TARGET_URL

    async def test_full_stream_pipeline_from_real_download(self):
        # 移除 downloader_run 的檢查
        if not InMemoryStreamUnzipper or not AsyncBytesGeneratorReader or not logger:
            self.skipTest("由於核心模組導入失敗，跳過整合測試。請檢查環境和路徑。")

        logger.info(f"整合測試（自給自足下載）：開始直接從 URL 下載: {self.TARGET_URL}")

        # 從 URL 中提取日期 "2025_06_27" 以便後續驗證
        try:
            filename_from_url = self.TARGET_URL.split('/')[-1] # taifex_future_dat_2025_06_27.zip
            if "taifex_future_dat_" in filename_from_url:
                date_part_str = filename_from_url.split('taifex_future_dat_')[-1].split('.')[0] # 2025_06_27
            else:
                self.fail(f"無法從 URL {self.TARGET_URL} 的檔名中識別日期格式。")
                return

            target_date_obj = datetime.strptime(date_part_str, "%Y_%m_%d")
            expected_date_str_in_file_variant1 = target_date_obj.strftime("%Y%m%d")
            expected_date_str_in_file_variant2 = target_date_obj.strftime("%Y/%m/%d")
        except Exception as e:
            self.fail(f"從 URL {self.TARGET_URL} 提取日期用於驗證時失敗: {e}")

        input_stream_to_process: Optional[AsyncGenerator[bytes, None]] = None
        dl_filename = self.TARGET_URL.split('/')[-1] # 用於日誌記錄
        response_obj: Optional[aiohttp.ClientResponse] = None
        unzipper: Optional[InMemoryStreamUnzipper] = None # 初始化 unzipper

        async with aiohttp.ClientSession() as session:
            try:
                logger.info(f"測試：調用 get_stream_with_content_type('{self.TARGET_URL}')...")
                # get_stream_with_content_type 現在返回整個 response 和 content_type
                response_obj, content_type = await get_stream_with_content_type(session, self.TARGET_URL)

                self.assertIsNotNone(response_obj.content, f"get_stream_with_content_type 未能獲取到 StreamReader for {self.TARGET_URL}")
                logger.info(f"整合測試：從 URL 獲取到數據流，Content-Type: '{content_type}'")

                if 'application/zip' in content_type:
                    logger.info("偵測到 ZIP 檔案，準備 InMemoryStreamUnzipper...")

                    async def zip_stream_adapter_gen() -> AsyncGenerator[bytes, None]:
                        """將 response.content (StreamReader) 適配為 AsyncGenerator[bytes, None]"""
                        try:
                            while True:
                                chunk = await response_obj.content.read(8192) # 使用 response_obj
                                if not chunk: break
                                yield chunk
                        finally:
                            if response_obj and not response_obj.closed:
                                response_obj.release() # 確保 response 在 stream 被消耗後釋放

                    unzipper = InMemoryStreamUnzipper(zip_stream_adapter_gen())
                    input_stream_to_process = await unzipper.get_uncompressed_stream()

                elif 'text/html' in content_type:
                    logger.info("偵測到 HTML 內容，啟動 HTML 解析器...")
                    # parse_csv_from_html 應接收 response_obj 以便在其內部釋放
                    input_stream_to_process = parse_csv_from_html(response_obj)
                else:
                    # 未知類型，確保釋放 response
                    if response_obj and not response_obj.closed: await response_obj.release()
                    self.fail(f"未知的內容類型: {content_type} 從 URL: {self.TARGET_URL}")
                    return

                self.assertIsNotNone(input_stream_to_process, "未能從下載內容中準備好用於處理的數據流。")

            except aiohttp.ClientResponseError as e_http:
                if response_obj and not response_obj.closed: await response_obj.release()
                self.fail(f"直接從 URL {self.TARGET_URL} 下載時發生 HTTP 錯誤: {e_http.status} {e_http.message}")
                return
            except Exception as e_download:
                if response_obj and not response_obj.closed: await response_obj.release()
                self.fail(f"直接從 URL {self.TARGET_URL} 下載或初步處理時發生未知錯誤: {e_download}")
                return

        # session 的 async with 塊結束於此，response_obj 如果未被上述邏輯釋放，則可能會有問題
        # 但由於 response_obj 的釋放已整合到 zip_stream_adapter_gen 和 parse_csv_from_html 的 finally 中，
        # 或在錯誤/未知類型分支中明確調用，理論上應已處理。

        uncompressed_byte_stream_gen = input_stream_to_process # uncompressed_byte_stream_gen 現在是最終要處理的流

        printed_lines: List[str] = []
        header_line_content = ""
        data_rows_for_date_check: List[List[str]] = []

        # 現在 uncompressed_byte_stream_gen 已經是處理好的流 (來自ZIP解壓或HTML解析)
        # 直接用它來創建 AsyncBytesGeneratorReader
        # 移除之前針對 ZIP 路徑的 unzipper.get_uncompressed_stream() 調用和相關 assert

        try:
            # self.assertIsNotNone(uncompressed_byte_stream_gen, # 這個斷言已在前面處理 input_stream_to_process 時完成
            #                      f"Data stream for processing should not be None for {dl_filename}")
            if not uncompressed_byte_stream_gen: # Type guard, 應該不會觸發，因為前面有 assert
                self.fail("uncompressed_byte_stream_gen is None before creating AsyncBytesGeneratorReader, this should not happen.")
                return

            # dl_filename 已在外部定義
            member_descriptor = f"{dl_filename} (Processed Stream) -> member_0"
            adapted_stream = AsyncBytesGeneratorReader(uncompressed_byte_stream_gen, member_descriptor)
            logger.info(f"整合測試：已將解壓縮流包裝到 AsyncBytesGeneratorReader ({member_descriptor})。")
            logger.info(f"整合測試：開始從解壓縮流 ({member_descriptor}) 讀取並打印前幾行...")

            text_lines_buffer = ""
            # taifex_future_dat_YYYY_MM_DD.zip 內的 CSV 檔案通常是 BIG5/MS950 編碼
            decoder = codecs.getincrementaldecoder('ms950')(errors='replace')
            encoding_used = 'ms950'
            lines_processed_count = 0
            max_lines_to_process = 1 + 3 # 表頭 + 3行數據

            while lines_processed_count < max_lines_to_process:
                chunk = await adapted_stream.readchunk(4096)

                if not chunk and not text_lines_buffer:
                    break

                try:
                    text_lines_buffer += decoder.decode(chunk, final=False if chunk else True)
                except UnicodeDecodeError as ude_decode:
                    logger.error(f"{encoding_used} 解碼時發生嚴重錯誤: {ude_decode}。已讀取內容: '{text_lines_buffer[:200]}...' + chunk: '{chunk[:200]}...'")
                    self.fail(f"{encoding_used} 解碼失敗。檔案可能不是預期的編碼，或包含無效字元。錯誤: {ude_decode}")


                while '\n' in text_lines_buffer and lines_processed_count < max_lines_to_process:
                    line, _, text_lines_buffer = text_lines_buffer.partition('\n')
                    line_content = line.strip()
                    if not line_content: continue

                    printed_lines.append(line_content)
                    logger.info(f"L{lines_processed_count + 1} ({encoding_used}): {line_content}")

                    if lines_processed_count == 0:
                        header_line_content = line_content
                    elif len(data_rows_for_date_check) < 3:
                        data_rows_for_date_check.append([field.strip() for field in line_content.split(',')])

                    lines_processed_count += 1

                if not chunk:
                    if text_lines_buffer.strip() and lines_processed_count < max_lines_to_process:
                        line_content = text_lines_buffer.strip()
                        printed_lines.append(line_content)
                        logger.info(f"L{lines_processed_count + 1} ({encoding_used}) (final): {line_content}")
                        if lines_processed_count == 0:
                             header_line_content = line_content
                        elif len(data_rows_for_date_check) < 3:
                             data_rows_for_date_check.append([field.strip() for field in line_content.split(',')])
                        lines_processed_count += 1
                        text_lines_buffer = ""
                    break

            logger.info(f"整合測試：共處理了 {lines_processed_count} 行 (使用 {encoding_used} 編碼)。")

            self.assertGreaterEqual(lines_processed_count, 1, "應至少讀取到一行（表頭）。")
            self.assertTrue(header_line_content, "未能獲取到解壓縮數據的表頭行。")

            logger.info(f"表頭內容 (使用 {encoding_used} 編碼): {header_line_content}")
            for keyword in self.EXPECTED_FIRST_LINE_KEYWORDS:
                self.assertIn(keyword, header_line_content,
                              f"表頭應包含關鍵字 '{keyword}' (編碼 {encoding_used})，實際表頭: '{header_line_content}'")

            self.assertFalse(not data_rows_for_date_check and lines_processed_count > 1,
                             "讀取了超過一行的數據（可能只有表頭），但未能捕獲任何用於日期驗證的數據行。")

            if data_rows_for_date_check:
                header_cols_list = [col.strip() for col in header_line_content.split(',')]
                try:
                    date_col_idx = header_cols_list.index("成交日期")
                except ValueError:
                    self.fail("表頭中未找到 '成交日期' 欄位，無法驗證數據行日期。")

                logger.info(f"將對 {len(data_rows_for_date_check)} 行數據進行成交日期驗證 (期望日期: {expected_date_str_in_file_variant1} 或 {expected_date_str_in_file_variant2})。")
                for i, data_row_list in enumerate(data_rows_for_date_check):
                    self.assertTrue(date_col_idx < len(data_row_list), f"數據行 {i+1} (內容: {','.join(data_row_list)}) 的欄位數 ({len(data_row_list)}) 少于 '成交日期' 欄位的索引 ({date_col_idx})，無法檢查。")
                    actual_date_in_row = data_row_list[date_col_idx]
                    logger.info(f"數據行 {i+1} 的 '成交日期' 欄位值: '{actual_date_in_row}'")
                    self.assertTrue(
                        actual_date_in_row == expected_date_str_in_file_variant1 or \
                        actual_date_in_row == expected_date_str_in_file_variant2,
                        f"數據行 {i+1} 的成交日期應為 '{expected_date_str_in_file_variant1}' 或 '{expected_date_str_in_file_variant2}', 實際為 '{actual_date_in_row}'"
                    )
            elif lines_processed_count > 1 :
                 logger.warning("沒有捕獲到任何數據行進行日期驗證（僅處理了表頭或數據行解析問題）。請檢查日誌中的行內容。")

        # 移除了 StreamDownloadError，因為不再使用 downloader
        # aiohttp.ClientResponseError 已在下載部分處理
        except aiohttp.ClientError as e_http_specific: # 捕獲更廣泛的 aiohttp 客戶端錯誤
            self.fail(f"測試過程中發生 aiohttp 客戶端錯誤: {e_http_specific}")
        except Exception as e: # 捕獲所有其他未預期錯誤
            # 打印已讀取的行，以便調試
            logger.error(f"整合測試過程中發生未預期錯誤: {e}")
            if printed_lines:
                logger.info("發生錯誤前已打印的行:")
                for i, line_err_print in enumerate(printed_lines):
                    logger.info(f"ErrPrint L{i+1}: {line_err_print}")
            raise # 重新拋出異常，讓測試框架捕獲
        finally:
            if 'unzipper' in locals() and unzipper:
                unzipper.close()
                logger.info("整合測試：InMemoryStreamUnzipper 已關閉。")

            # zip_stream_reader (即 aiohttp_stream_reader) 的釋放由 aiohttp.ClientSession 的上下文管理器處理
            # 無需手動釋放 aiohttp_stream_reader
            # if aiohttp_stream_reader and hasattr(aiohttp_stream_reader, 'release') and callable(aiohttp_stream_reader.release):
            #     try:
            #         # aiohttp.StreamReader 通常沒有顯式的 release() 方法，它的生命週期由 response 控制
            #         pass
            #     except Exception as e_release:
            #         logger.warning(f"釋放 aiohttp_stream_reader 時出錯: {e_release}")
            logger.info("整合測試：測試結束。")


if __name__ == '__main__':
    # 移除了 downloader_run 的檢查
    # if downloader_run:
    unittest.main(verbosity=2)
    # else:
    #     print("主要模組導入失敗，無法運行整合測試。", file=sys.stderr)
