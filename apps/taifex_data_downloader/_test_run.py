# -*- coding: utf-8 -*-
# 串流供應器測試檔 (v20.1 非同步版本)
import os
import sys
import unittest
import asyncio
import aiohttp
from datetime import datetime
from typing import Dict, Any, Optional, List # List hinzugefügt
from unittest.mock import patch, MagicMock # 用於 mock 外部 URL

# --- 路徑自我校正樣板碼 ---
# 確保能從 apps 目錄導入 run 模組
try:
    current_downloader_dir = os.path.dirname(os.path.abspath(__file__))
    apps_dir = os.path.dirname(current_downloader_dir) # 'apps' 資料夾
    project_root = os.path.dirname(apps_dir) # 專案根目錄

    if apps_dir not in sys.path:
        sys.path.insert(0, apps_dir)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    # 匯入被測試的模組
    from taifex_data_downloader import run as downloader_run
    from taifex_data_downloader.run import StreamDownloadError #明確導入
except ImportError as e:
    print(f"導入模組時發生錯誤: {e}", file=sys.stderr)
    downloader_run = None # type: ignore
    StreamDownloadError = Exception # type: ignore
except Exception as e:
    print(f"路徑校正或導入時發生其他錯誤: {e}", file=sys.stderr)
    downloader_run = None # type: ignore
    StreamDownloadError = Exception # type: ignore
# --- 路徑自我校正樣板碼結束 ---


# 為了測試 StreamReader 的內容，我們需要一個 mock 的 StreamReader
def create_mock_stream_reader(content: bytes, chunk_size: int = 1024) -> MagicMock:
    """
    創建一個 MagicMock 物件，其行為類似 aiohttp.StreamReader，
    用於測試目的。
    """
    mock_stream = MagicMock(spec=aiohttp.StreamReader)

    # 將內容分塊
    chunks: List[bytes] = [content[i:i + chunk_size] for i in range(0, len(content), chunk_size)]

    async def readchunk_impl(*args, **kwargs) -> bytes:
        if not chunks:
            return b''
        return chunks.pop(0)

    async def read_impl(n: int = -1) -> bytes:
        if not chunks:
            return b''

        if n == -1: # 讀取所有剩餘內容
            all_remaining_content = b"".join(chunks)
            chunks.clear()
            return all_remaining_content
        else: # 讀取指定數量的位元組
            collected_bytes = b""
            while len(collected_bytes) < n and chunks:
                current_chunk = chunks.pop(0)
                needed = n - len(collected_bytes)

                if len(current_chunk) <= needed:
                    collected_bytes += current_chunk
                else: # current_chunk 比需要的長
                    collected_bytes += current_chunk[:needed]
                    chunks.insert(0, current_chunk[needed:]) # 將剩餘部分放回
                    break
            return collected_bytes

    async def readline_impl() -> bytes: # 簡化版 readline
        if not chunks:
            return b''
        first_chunk = chunks.pop(0)
        newline_pos = first_chunk.find(b'\n')
        if newline_pos != -1:
            line = first_chunk[:newline_pos+1]
            remainder = first_chunk[newline_pos+1:]
            if remainder:
                chunks.insert(0, remainder)
            return line
        else: # 沒有換行符，返回整個塊
            return first_chunk

    async def release_impl():
        chunks.clear()
        # print(f"Mock stream ({id(mock_stream)}) released.")

    def at_eof_impl():
        return not bool(chunks)

    mock_stream.readchunk = MagicMock(side_effect=readchunk_impl)
    mock_stream.read = MagicMock(side_effect=read_impl)
    mock_stream.readline = MagicMock(side_effect=readline_impl)
    mock_stream.release = MagicMock(side_effect=release_impl)
    mock_stream.at_eof = MagicMock(side_effect=at_eof_impl)

    # 為了讓 isinstance(mock_stream, aiohttp.StreamReader) 返回 True
    # 我們需要讓 mock_stream 的 __class__ 指向 aiohttp.StreamReader
    # 這是一個更深層的 mock，但可以幫助 isinstance 檢查
    # 不過，更安全的做法是避免 isinstance 檢查，而是檢查行為 (duck typing)
    # 或者接受它是一個 MagicMock。
    # 在這裡，我們不改變 __class__，而是調整測試中的斷言。

    return mock_stream

class TestTaifexDataStreamerAsync(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.mock_session = MagicMock(spec=aiohttp.ClientSession)
        self.mock_session.__aenter__.return_value = self.mock_session
        self.mock_session.__aexit__.return_value = None
        self.test_output_dir = "test_temp_output_async"
        # setUp 中不進行檔案系統操作，確保測試獨立性

    async def asyncTearDown(self):
        # tearDown 中也不應有檔案系統操作，除非是測試特意創建的臨時檔案
        if os.path.exists(self.test_output_dir):
            # 如果意外創建了目錄，可以考慮移除，但最好是測試不創建
            # import shutil
            # if os.listdir(self.test_output_dir): # 如果目錄非空
            #     print(f"警告：測試目錄 {self.test_output_dir} 在 teardown 時非空，將被移除。")
            #     shutil.rmtree(self.test_output_dir)
            # elif not os.listdir(self.test_output_dir): # 如果目錄為空
            #     os.rmdir(self.test_output_dir)
            pass


    @patch('aiohttp.ClientSession')
    async def test_stream_data_successful_retrieval(self, MockClientSession):
        MockClientSession.return_value = self.mock_session

        test_date_str = "2023-11-01"
        start_dt = datetime.strptime(test_date_str, "%Y-%m-%d")
        end_dt = start_dt
        data_types_to_download = {'options_summary': True}
        expected_url_part = "OptionsDaily_2023_11_01.zip"

        mock_response = MagicMock(spec=aiohttp.ClientResponse)
        mock_response.status = 200
        mock_response.headers = {'Content-Type': 'application/zip'}

        test_content_bytes = b"PK\x03\x04This is zip content for test." # 更明確的內容
        # 將 mock_response.content 設置為 create_mock_stream_reader 的結果
        mock_response.content = create_mock_stream_reader(test_content_bytes)

        mock_get_context_manager = MagicMock()
        mock_get_context_manager.__aenter__.return_value = mock_response
        mock_get_context_manager.__aexit__.return_value = None
        self.mock_session.get.return_value = mock_get_context_manager

        results = await downloader_run.stream_data(start_dt, end_dt, data_types_to_download)

        self.assertEqual(len(results), 1)
        date_res, task_key_res, file_name_res, stream_reader_res, error_res = results[0]

        self.assertEqual(date_res, test_date_str)
        self.assertEqual(task_key_res, 'options_summary')
        self.assertTrue(expected_url_part in file_name_res)
        self.assertIsNone(error_res, f"不應有錯誤訊息，但收到: {error_res}")
        self.assertIsNotNone(stream_reader_res, "StreamReader (mock) 不應為 None")

        # 由於 stream_reader_res 現在是 MagicMock，isinstance 檢查會失敗。
        # 我們可以檢查它是否具有預期的方法，或者信任我們的 mock 設置。
        # self.assertIsInstance(stream_reader_res, aiohttp.StreamReader, "返回的應為 StreamReader 實例")
        self.assertTrue(hasattr(stream_reader_res, 'readchunk'), "Mock stream 應有 readchunk 方法")
        self.assertTrue(hasattr(stream_reader_res, 'release'), "Mock stream 應有 release 方法")

        self.mock_session.get.assert_called_once()
        called_url = self.mock_session.get.call_args[0][0]
        self.assertTrue(expected_url_part in called_url)

        if stream_reader_res:
            # 使用 read 方法來讀取，因為 readchunk 只讀一個預設塊
            read_content = await stream_reader_res.read(len(test_content_bytes))
            self.assertEqual(read_content, test_content_bytes, "從 mock stream 讀取的內容不匹配")

            # 確保 EOF 行為正確
            self.assertTrue(stream_reader_res.at_eof(), "讀取所有內容後應為 EOF (at_eof is not async)") # 移除 await
            empty_chunk = await stream_reader_res.readchunk()
            self.assertEqual(empty_chunk, b'', "EOF 後 readchunk 應返回空字節串")

        self.assertFalse(os.path.exists(os.path.join(self.test_output_dir, "A_Core_Trading")), "不應創建任何目錄")


    @patch('aiohttp.ClientSession')
    async def test_stream_data_http_error(self, MockClientSession):
        MockClientSession.return_value = self.mock_session

        test_date_str = "2023-11-02"
        start_dt = datetime.strptime(test_date_str, "%Y-%m-%d")
        end_dt = start_dt
        data_types_to_download = {'futures_trades': True}
        expected_url_part = "Daily_2023_11_02.zip"

        mock_response_error = MagicMock(spec=aiohttp.ClientResponse)
        mock_response_error.status = 404
        mock_response_error.headers = {'Content-Type': 'text/plain'}
        # 404 時，response.content 可能未定義或為空。
        # get_stream_from_url 應基於 status 引發異常，而不是嘗試讀取 content。
        # 因此，這裡不需要為 mock_response_error.content 賦值。

        mock_get_context_manager_error = MagicMock()
        mock_get_context_manager_error.__aenter__.return_value = mock_response_error
        mock_get_context_manager_error.__aexit__.return_value = None
        self.mock_session.get.return_value = mock_get_context_manager_error

        results = await downloader_run.stream_data(start_dt, end_dt, data_types_to_download)

        self.assertEqual(len(results), 1)
        _, _, file_name_res, stream_reader_res, error_res = results[0]

        self.assertTrue(expected_url_part in file_name_res)
        self.assertIsNone(stream_reader_res, "發生錯誤時 StreamReader 應為 None")
        self.assertIsNotNone(error_res, "應有錯誤訊息")
        self.assertIn("HTTP 錯誤狀態：404", error_res)

        self.mock_session.get.assert_called_once()


    @patch('aiohttp.ClientSession')
    async def test_stream_data_html_content_error(self, MockClientSession):
        MockClientSession.return_value = self.mock_session

        test_date_str = "2023-11-03"
        start_dt = datetime.strptime(test_date_str, "%Y-%m-%d")
        end_dt = start_dt
        data_types_to_download = {'put_call_ratio': True}
        expected_url_part = "PCRatio_2023_11_03.zip"

        mock_response_html = MagicMock(spec=aiohttp.ClientResponse)
        mock_response_html.status = 200
        mock_response_html.headers = {'Content-Type': 'text/html; charset=utf-8'}
        # 即使是 HTML 內容，response.content 仍然存在。
        # get_stream_from_url 會檢查 headers 並引發異常。
        # 我們可以為 content 設置一個 mock stream，但 get_stream_from_url 不應該嘗試從中讀取。
        mock_response_html.content = create_mock_stream_reader("<html><body>查無資料</body></html>".encode('utf-8'))

        mock_get_context_manager_html = MagicMock()
        mock_get_context_manager_html.__aenter__.return_value = mock_response_html
        mock_get_context_manager_html.__aexit__.return_value = None
        self.mock_session.get.return_value = mock_get_context_manager_html

        results = await downloader_run.stream_data(start_dt, end_dt, data_types_to_download)

        self.assertEqual(len(results), 1)
        _, _, file_name_res, stream_reader_res, error_res = results[0]

        self.assertTrue(expected_url_part in file_name_res)
        self.assertIsNone(stream_reader_res)
        self.assertIsNotNone(error_res)
        self.assertIn("內容類型為 HTML", error_res)


    @patch('aiohttp.ClientSession')
    async def test_stream_data_client_timeout_error(self, MockClientSession):
        MockClientSession.return_value = self.mock_session

        test_date_str = "2023-11-04"
        start_dt = datetime.strptime(test_date_str, "%Y-%m-%d")
        end_dt = start_dt
        data_types_to_download = {'final_settlement_price': True}
        expected_url_part = "FSP_2023_11_04.zip"

        self.mock_session.get.side_effect = asyncio.TimeoutError("請求超時了")

        results = await downloader_run.stream_data(start_dt, end_dt, data_types_to_download)

        self.assertEqual(len(results), 1)
        _, _, file_name_res, stream_reader_res, error_res = results[0]

        self.assertTrue(expected_url_part in file_name_res)
        self.assertIsNone(stream_reader_res)
        self.assertIsNotNone(error_res)
        self.assertIn("請求超時", error_res)


    async def test_no_data_types_selected(self):
        start_dt = datetime.strptime("2023-01-01", "%Y-%m-%d")
        end_dt = datetime.strptime("2023-01-01", "%Y-%m-%d")
        data_types_to_download: Dict[str, bool] = {}

        results = await downloader_run.stream_data(start_dt, end_dt, data_types_to_download)

        self.assertEqual(len(results), 0, "未選擇數據類型時，結果列表應為空")

if __name__ == "__main__":
    if downloader_run: # 確保模組已載入
      asyncio.run(unittest.main(argv=['first-arg-is-ignored'], exit=False, verbosity=0))
    else:
      print("Downloader module not loaded, tests cannot run.", file=sys.stderr)
      # Fallback for environments where direct asyncio.run(unittest.main) might be problematic
      # or when module loading failed.
      # This part might need adjustment based on the specific test execution environment.
      # For CI/CD or command-line execution, `python -m unittest ...` is standard.
      try:
          unittest.main(argv=['first-arg-is-ignored'], exit=False)
      except SystemExit: # unittest.main can cause SystemExit
          pass
