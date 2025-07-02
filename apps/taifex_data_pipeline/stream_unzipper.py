# -*- coding: utf-8 -*-
"""
模組名稱: stream_unzipper
職責: 提供一個非同步的記憶體中 ZIP 解壓縮器。
"""

import asyncio
import zipfile
import io
from typing import AsyncGenerator, Optional

class InMemoryStreamUnzipper:
    """
    一個非同步的記憶體中 ZIP 解壓縮器。

    本類別能接收一個代表 ZIP 壓縮檔的非同步串流讀取器 (如 aiohttp.StreamReader)，
    並在不將任何內容寫入磁碟的情況下，提供一個解壓縮後內容的非同步生成器。
    它被設計為只處理壓縮檔中的第一個檔案。
    """

    def __init__(self, zip_stream_reader, chunk_size: int = 8192):
        """
        初始化解壓縮器。

        :param zip_stream_reader: 一個非同步串流讀取器，其內容應為一個完整的 ZIP 檔案。
        :param chunk_size: 內部讀取和產出數據時的塊大小（位元組）。
        """
        self._zip_stream_reader = zip_stream_reader
        self._buffer = io.BytesIO()
        self._chunk_size = chunk_size
        self._zip_file_loaded = False # Added to track if buffer is loaded

    async def _load_zip_to_buffer(self) -> None:
        """
        以非同步方式將整個 ZIP 串流讀取到記憶體緩衝區 (io.BytesIO)。

        這是必要的步驟，因為標準的 zipfile 模組需要對檔案進行 seek 操作以讀取
        位於檔案末尾的中央目錄記錄 (Central Directory Record)，
        因此無法處理純粹的前向數據流。
        """
        # Ensure this method is idempotent or safe to call multiple times
        if self._zip_file_loaded:
            self._buffer.seek(0) # Reset pointer if called again on already loaded buffer
            return

        async for chunk in self._zip_stream_reader: # Assumes zip_stream_reader is an async iterator
            self._buffer.write(chunk)
        self._buffer.seek(0)
        self._zip_file_loaded = True # Mark as loaded

    async def get_uncompressed_stream(self) -> Optional[AsyncGenerator[bytes, None]]:
        """
        獲取解壓縮後的數據流。

        此方法是一個非同步生成器，如果成功，它將返回另一個可從中讀取數據的非同步生成器。

        如果 ZIP 檔案為空或損壞，此方法將返回 None。

        :return: 一個非同步生成器用於產出解壓數據，或在失敗時返回 None。
        """
        if not self._zip_file_loaded: # Ensure buffer is loaded before use
            await self._load_zip_to_buffer()

        # It's crucial to reset the buffer's position before ZipFile reads it
        self._buffer.seek(0)

        try:
            if not zipfile.is_zipfile(self._buffer):
                self._buffer.seek(0) # Reset for safety, though it might be consumed by is_zipfile
                return None

            self._buffer.seek(0) # is_zipfile might move the pointer
            with zipfile.ZipFile(self._buffer, 'r') as zf:
                namelist = zf.namelist()
                if not namelist:
                    return None

                first_file_name = ""
                for name in namelist:
                    # More robust check for actual files vs directories/metadata
                    member_info = zf.getinfo(name)
                    if not member_info.is_dir() and \
                       "__MACOSX" not in member_info.filename and \
                       not member_info.filename.startswith('.'):
                        first_file_name = name
                        break

                if not first_file_name:
                    return None

                # This inner async generator is what will be returned
                async def async_generator_wrapper():
                    # This part needs to operate on a ZipFile object that is still open.
                    # The `zf` from the outer `with` block will be closed when `get_uncompressed_stream` returns.
                    # This means the generator would operate on a closed ZipFile object.
                    # To fix this, the ZipFile object must be managed within the generator,
                    # or the opened member stream must be passed to the generator.

                    # Re-opening the buffer for the specific member to ensure context is valid
                    # This is less efficient but safer for generator lifecycle.
                    # self._buffer must be seek(0) before this new ZipFile instance is created.
                    current_buffer_position = self._buffer.tell() # Save current position
                    self._buffer.seek(0) # Reset for the new ZipFile instance

                    try:
                        # Create a new ZipFile instance specifically for this generator's lifetime
                        # This ensures that 'zf_internal' is valid as long as the generator is being consumed.
                        with zipfile.ZipFile(self._buffer, 'r') as zf_internal:
                            # We need to find first_file_name again using zf_internal if it's a new instance
                            # Or, pass first_file_name to this wrapper. For now, assume first_file_name is correct.
                            with zf_internal.open(first_file_name, 'r') as member_stream:
                                while True:
                                    chunk = member_stream.read(self._chunk_size)
                                    if not chunk:
                                        break
                                    yield chunk
                                    await asyncio.sleep(0)
                    finally:
                        # Restore buffer position if it was changed by this generator's ZipFile instance
                        # This is important if self._buffer is to be reused by other operations
                        # on the same InMemoryStreamUnzipper instance.
                        # However, if _load_zip_to_buffer always happens and resets, this might be redundant.
                        # For safety, if we are creating a new ZipFile instance from self._buffer,
                        # we should ensure its state is managed.
                        # Since ZipFile(BytesIO) doesn't close the BytesIO, we don't need to reopen self._buffer.
                        # The seek(0) at the start of get_uncompressed_stream or _load_zip_to_buffer handles reuse.
                        pass # zf_internal is closed by its 'with' statement.

                return async_generator_wrapper()

        except zipfile.BadZipFile:
            return None
        # The 'finally' block that was in Directive v20.3.1 to close self._buffer
        # is removed from here. The closing of self._buffer should be handled
        # by the close() method of this class, or its __aexit__ if used as context manager.
        # This allows the buffer to be potentially reused if get_uncompressed_stream is called
        # multiple times on the same instance (though current logic only processes first file).
