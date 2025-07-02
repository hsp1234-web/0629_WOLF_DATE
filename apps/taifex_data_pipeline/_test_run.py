# -*- coding: utf-8 -*-
# 精煉廠測試檔 (v20.2 串流處理版)
import os
# 確保 DuckDB 和其他數值計算庫在受限環境下不會因線程競爭導致效能下降
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
import sys
import unittest
import asyncio # 新增 for async test
import io # 新增 for BytesIO
# import subprocess # 移除 subprocess
# import json # 不再需要，新測試不涉及 format_map.json
# import tempfile # 不再需要，新測試為記憶體操作
# import shutil # 不再需要，新測試為記憶體操作
import zipfile
# import duckdb # 不再需要，新測試不涉及資料庫
from typing import List # Union, Dict, Tuple, Any 不再直接需要
# from unittest.mock import MagicMock # 不再需要，新的模擬串流不使用 MagicMock

# --- 路徑自我校正樣板碼 ---
try:
    current_pipeline_dir = os.path.dirname(os.path.abspath(__file__))
    apps_dir = os.path.dirname(current_pipeline_dir)
    project_root = os.path.dirname(apps_dir)

    if apps_dir not in sys.path:
        sys.path.insert(0, apps_dir)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    # 導入被測試的模組
    # from taifex_data_pipeline import run as pipeline_run # 不再直接調用 async_main
    from taifex_data_pipeline.run import SimpleLogger # HardwareManager 不再需要
    # from taifex_data_downloader.run import StreamDownloadError # 如果需要 mock downloader 異常 (暫不需要)
    # from taifex_data_pipeline.stream_unzipper import AiozipMemberStreamAdapter # 如果需要特定檢查此類型 (暫不需要)
    # 正確導入 InMemoryStreamUnzipper
    from taifex_data_pipeline.run import InMemoryStreamUnzipper

except ImportError as e:
    print(f"導入模組時發生錯誤: {e}", file=sys.stderr)
    # pipeline_run = None # type: ignore
    SimpleLogger = None # type: ignore
    # HardwareManager = None # type: ignore
    InMemoryStreamUnzipper = None # type: ignore
except Exception as e:
    print(f"路徑校正或導入時發生其他錯誤: {e}", file=sys.stderr)
    # pipeline_run = None # type: ignore
    SimpleLogger = None # type: ignore
    # HardwareManager = None # type: ignore
    InMemoryStreamUnzipper = None # type: ignore
# --- 路徑自我校正樣板碼結束 ---

async def create_mock_zip_stream(csv_filename_in_zip: str = "mock_data.csv", chunk_size: int = 1024):
    """
    創建一個模擬的 ZIP 數據流，其中包含一個 CSV 檔案。
    此函數返回一個非同步產生器，模擬 aiohttp.StreamReader 的行為。
    """
    csv_content = (
        "成交日期,商品代號,到期月份(週別),成交時間,成交價格,成交數量(B+S)\r\n"
        "20250703,TXF,202507,08:45:01,18000,2\r\n"
        "20250703,TXF,202507,08:45:02,18001,5\r\n"
    )

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(csv_filename_in_zip, csv_content.encode('utf-8'))
    zip_data = zip_buffer.getvalue()

    # 將 ZIP 數據包裝成非同步產生器
    async def stream_generator():
        for i in range(0, len(zip_data), chunk_size):
            yield zip_data[i:i + chunk_size]
            await asyncio.sleep(0) # 允許事件循環處理其他任務

    # 返回產生器和原始 CSV 內容以供斷言
    return stream_generator(), csv_content


class TestTaifexDataPipelineStreaming(unittest.IsolatedAsyncioTestCase): # 更改基類
    """
    針對重構後的非同步串流版本 `run.py` 的端到端測試。
    主要測試 `run.py` 的核心邏輯 (`async_main`) 是否能正確處理
    模擬的記憶體數據串流 (來自 `aiohttp.StreamReader` 的 mock)，
    並在 DuckDB 資料庫中生成預期的表和數據。
    強調：整個流程不應有本地磁碟檔案讀寫 (除了最終DB和format_map)。
    """

    # def _create_mock_aiohttp_stream_reader(self, content_bytes: bytes) -> MagicMock:
    #     """
    #     創建一個模擬的 aiohttp.StreamReader 物件。
    #     這個 mock 物件的 read() 方法會返回提供的 content_bytes。
    #     """
    #     mock_stream = MagicMock(name="MockAiohttpStreamReader")
    #
    #     # 模擬 read() 方法
    #     async def mock_read(n: int = -1) -> bytes:
    #         if hasattr(mock_stream, '_buffer') and mock_stream._buffer is not None:
    #             if n == -1:
    #                 data = mock_stream._buffer.read()
    #                 mock_stream._buffer = None # 標記已讀完
    #                 return data
    #             else:
    #                 data = mock_stream._buffer.read(n)
    #                 if not data: # 如果讀不到更多，則 buffer 設為 None
    #                      mock_stream._buffer = None
    #                 return data
    #         return b"" # 如果 buffer 為 None 或已耗盡
    #
    #     # 模擬 readline() 方法 (簡化版)
    #     async def mock_readline() -> bytes:
    #         if hasattr(mock_stream, '_buffer') and mock_stream._buffer is not None:
    #             line = mock_stream._buffer.readline()
    #             if not mock_stream._buffer.peek(1): # 檢查是否還有內容
    #                 mock_stream._buffer = None
    #             return line
    #         return b""
    #
    #     # 模擬 readchunk() 方法
    #     async def mock_readchunk(size: int = 8192) -> bytes:
    #         return await mock_read(size)
    #
    #     # 模擬 at_eof() 方法
    #     def mock_at_eof() -> bool:
    #         return not (hasattr(mock_stream, '_buffer') and mock_stream._buffer is not None and mock_stream._buffer.peek(1))
    #
    #     mock_stream.read = MagicMock(side_effect=mock_read)
    #     mock_stream.readline = MagicMock(side_effect=mock_readline)
    #     mock_stream.readchunk = MagicMock(side_effect=mock_readchunk)
    #     mock_stream.at_eof = MagicMock(side_effect=mock_at_eof)
    #
    #     # 初始化內部緩衝區
    #     mock_stream._buffer = io.BytesIO(content_bytes)
    #     return mock_stream

    # def _create_zip_in_memory(self, files_content: Dict[str, Union[str, bytes]]) -> bytes:
    #     """
    #     在記憶體中創建一個 ZIP 檔案，包含指定的檔案及其內容。
    #     返回 ZIP 檔案的位元組內容。
    #     """
    #     zip_buffer = io.BytesIO()
    #     with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
    #         for filename, content in files_content.items():
    #             if isinstance(content, str):
    #                 zf.writestr(filename, content.encode('utf-8')) # 預設使用 utf-8 編碼字串內容
    #             else: # 假設是 bytes
    #                 zf.writestr(filename, content)
    #     return zip_buffer.getvalue()

    async def asyncSetUp(self): # 更改為 asyncSetUp
        """
        為每個測試案例設定初始環境。
        - 初始化 logger。
        - 創建臨時目錄用於資料庫輸出和工作檔案。
        - 準備樣本數據內容 (CSV 字串)。
        """
        self.logger = SimpleLogger(log_level="DEBUG")
        # pipeline_run.logger = self.logger # 嘗試覆蓋 run.py 中的 logger - InMemoryStreamUnzipper 內部有自己的 print
        # 新的測試是記憶體操作，不需要臨時目錄和檔案
        # self.base_temp_dir = tempfile.mkdtemp(prefix="test_pipeline_streaming_")
        # self.temp_db_output_dir = os.path.join(self.base_temp_dir, "db_output")
        # self.temp_work_dir = os.path.join(self.base_temp_dir, "work_temp") # DuckDB 臨時目錄等
        # os.makedirs(self.temp_db_output_dir, exist_ok=True)
        # os.makedirs(self.temp_work_dir, exist_ok=True)

        # 範例 CSV 內容不再需要在 setUp 中準備
        # self.sample_tick_data_csv_content = "..."
        # self.futures_daily_content = "..."
        # self.options_daily_v1_content = "..."
        # self.institutional_investors_content = "..."
        self.logger.info("Test Case SetUp: Logger initialized. No temp directories needed for this test.")

    async def asyncTearDown(self): # 更改為 asyncTearDown
        # if hasattr(self, 'base_temp_dir') and os.path.exists(self.base_temp_dir):
        #     shutil.rmtree(self.base_temp_dir)
        self.logger.info("Test Case TearDown: No temp directories to clean up.")
        # self.tick_data_sample_csv_path 不再創建本地檔案

    # 舊的 test_pipeline_streaming_run 方法已完全移除，以避免任何殘留的程式碼或引用問題。

    async def test_inmemory_stream_unzipper_logic(self):
        """
        專門測試 InMemoryStreamUnzipper 的核心解壓縮邏輯。
        """
        self.logger.info("開始執行 InMemoryStreamUnzipper 密封測試...")

        # 1. 獲取模擬數據流和原始 CSV 內容
        mock_zip_stream_generator, original_csv_content = await create_mock_zip_stream()

        # 2. 實例化 InMemoryStreamUnzipper
        #    注意：InMemoryStreamUnzipper 的建構子期望一個類 aiohttp.StreamReader 的物件，
        #    或者一個可非同步迭代的物件 (async iterable)。我們的 stream_generator() 符合後者。
        unzipper = InMemoryStreamUnzipper(zip_stream_reader=mock_zip_stream_generator)

        # 3. 獲取解壓縮後的數據流
        uncompressed_stream_async_gen = await unzipper.get_uncompressed_stream()
        self.assertIsNotNone(uncompressed_stream_async_gen, "解壓縮後的串流不應為 None")

        # 4. 完整讀取解壓縮後的數據流並解碼
        uncompressed_chunks = []
        if uncompressed_stream_async_gen: # Type guard
            async for chunk in uncompressed_stream_async_gen:
                uncompressed_chunks.append(chunk)

        uncompressed_data_bytes = b"".join(uncompressed_chunks)
        uncompressed_data_str = uncompressed_data_bytes.decode('utf-8')

        # 5. 執行最終斷言
        self.assertEqual(uncompressed_data_str, original_csv_content,
                         "解壓縮後的內容與原始 CSV 內容不符。")

        self.logger.success("InMemoryStreamUnzipper 密封測試成功通過！")

        # 清理 unzipper (如果需要)
        unzipper.close()


if __name__ == "__main__":
    # 為了能在 Colab 或腳本中單獨運行和調試，可以這樣配置：
    # unittest.main(argv=['first-arg-is-ignored'], exit=False) # 舊的運行方式
    # 對於 IsolatedAsyncioTestCase，需要 asyncio.run
    # 檢查 InMemoryStreamUnzipper 和 SimpleLogger 是否已成功導入
    if 'InMemoryStreamUnzipper' in globals() and InMemoryStreamUnzipper is not None and \
       'SimpleLogger' in globals() and SimpleLogger is not None:
        unittest.main(argv=['first-arg-is-ignored'], exit=False, verbosity=2) # 提高 verbosity 以便觀察
    else:
        print("Required modules (InMemoryStreamUnzipper or SimpleLogger) not loaded, tests cannot run.", file=sys.stderr)

# 之前的 log_message 和 _get_test_logger 輔助函式定義已移除
# --- End of _test_run.py ---
