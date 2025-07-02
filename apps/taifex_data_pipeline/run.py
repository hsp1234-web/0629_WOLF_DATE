# -*- coding: utf-8 -*-
# 精煉廠主執行檔 (v20.3 整合 InMemoryStreamUnzipper - 最終修訂版)
import os
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
import sys
import json
import hashlib
import warnings
import shutil
import io
import re
import time
import argparse
from datetime import datetime
from typing import Generator, Tuple, Dict, Any, Optional, List, AsyncGenerator, Union
import asyncio
import concurrent.futures

import pandas as pd
import psutil
import pyarrow
import duckdb
import pytz
import zipfile
import csv
import codecs

# --- Start of Integrated Stream Unzipper Logic ---

import asyncio # Already imported, but good to list dependencies
import zipfile # Already imported, but good to list dependencies
import io # Already imported, but good to list dependencies
from typing import AsyncGenerator, Optional # Already imported, but good to list dependencies

class InMemoryStreamUnzipper:
    """
    一個內嵌的、非同步的記憶體中 ZIP 解壓縮器。
    它被設計為直接在主執行腳本中使用，以規避檔案創建問題。
    """

    def __init__(self, zip_stream_reader, chunk_size: int = 8192):
        self._zip_stream_reader = zip_stream_reader
        self._buffer = io.BytesIO()
        self._chunk_size = chunk_size
        self._first_file_name: Optional[str] = None # 用於記錄第一個檔案名，以便 close 時可以提供資訊

    async def _load_zip_to_buffer(self) -> bool:
        """
        將 ZIP 串流數據載入到內部緩衝區。
        返回 True 表示成功載入並有內容，False 表示串流為空或出錯。
        """
        if not self._zip_stream_reader:
            # logger.warning("InMemoryStreamUnzipper: _load_zip_to_buffer: zip_stream_reader 為空。") # 假設 logger 在此範圍不可用
            print("InMemoryStreamUnzipper: _load_zip_to_buffer: zip_stream_reader 為空。", file=sys.stderr)
            return False

        # 檢查 _zip_stream_reader 是否為 AsyncGenerator
        if not hasattr(self._zip_stream_reader, '__aiter__') or not hasattr(self._zip_stream_reader, '__anext__'):
             # logger.error("InMemoryStreamUnzipper: zip_stream_reader 不是一個有效的 AsyncGenerator。")
             print("InMemoryStreamUnzipper: zip_stream_reader 不是一個有效的 AsyncGenerator。", file=sys.stderr)
             return False

        try:
            async for chunk in self._zip_stream_reader:
                if chunk: # 確保 chunk 不是 None 或空
                    self._buffer.write(chunk)
            self._buffer.seek(0)
            if self._buffer.getbuffer().nbytes == 0:
                # logger.warning("InMemoryStreamUnzipper: ZIP 串流讀取完畢，但緩衝區為空。")
                print("InMemoryStreamUnzipper: ZIP 串流讀取完畢，但緩衝區為空。", file=sys.stderr)
                return False
            return True
        except Exception as e:
            # logger.error(f"InMemoryStreamUnzipper: 從串流讀取 ZIP 數據時發生錯誤: {e}")
            print(f"InMemoryStreamUnzipper: 從串流讀取 ZIP 數據時發生錯誤: {e}", file=sys.stderr)
            return False


    async def get_uncompressed_stream(self) -> Optional[AsyncGenerator[bytes, None]]:
        """
        獲取解壓縮後的數據串流。
        如果 ZIP 檔案無效、為空或不包含任何檔案，則返回 None。
        否則，返回一個非同步產生器，用於讀取 ZIP 中第一個檔案的內容。
        """
        if not await self._load_zip_to_buffer():
            # logger.warning("InMemoryStreamUnzipper: get_uncompressed_stream: _load_zip_to_buffer 失敗或緩衝區為空。")
            print("InMemoryStreamUnzipper: get_uncompressed_stream: _load_zip_to_buffer 失敗或緩衝區為空。", file=sys.stderr)
            self.close() # 確保緩衝區被清理
            return None

        try:
            if not zipfile.is_zipfile(self._buffer):
                # logger.warning("InMemoryStreamUnzipper: 提供的串流不是有效的 ZIP 檔案。")
                print("InMemoryStreamUnzipper: 提供的串流不是有效的 ZIP 檔案 (is_zipfile 返回 False)。", file=sys.stderr) # 保留此更明確的日誌
                self.close()
                return None

            self._buffer.seek(0) # is_zipfile 可能移動了指標，重置它
            with zipfile.ZipFile(self._buffer, 'r') as zf:
                namelist = zf.namelist()
                if not namelist:
                    # logger.warning("InMemoryStreamUnzipper: ZIP 檔案為空（不包含任何檔案）。")
                    print("InMemoryStreamUnzipper: ZIP 檔案為空（不包含任何檔案）。", file=sys.stderr)
                    # 不需要 self.close() 因為 ZipFile 的 context manager 會處理 self._buffer
                    # 但由於我們在 finally 中有 close，這裡保持原樣也行
                    return None

                self._first_file_name = namelist[0]
                # logger.info(f"InMemoryStreamUnzipper: 正在解壓縮 ZIP 檔案中的第一個檔案: {self._first_file_name}")
                print(f"InMemoryStreamUnzipper: 正在解壓縮 ZIP 檔案中的第一個檔案: {self._first_file_name}", file=sys.stdout)

                # 注意：zf.open() 返回的 member_stream 是同步的。
                # 我們需要一個包裝器將其轉換為非同步產生器。
                # 這裡直接在 ZipFile context manager 內部定義並返回產生器是關鍵，
                # 以確保 member_stream 在產生器被消耗時仍然有效。

                member_file_bytes = io.BytesIO(zf.read(self._first_file_name))

            # 移出 ZipFile context manager 後再創建產生器
            # member_file_bytes 現在包含了第一個檔案的全部內容在記憶體中

            async def async_generator_wrapper():
                try:
                    while True:
                        chunk = member_file_bytes.read(self._chunk_size)
                        if not chunk:
                            break
                        yield chunk
                        # 在 I/O 密集型操作中加入 await asyncio.sleep(0)
                        # 是一個好習慣，可以讓事件循環有機會執行其他任務。
                        await asyncio.sleep(0)
                finally:
                    if member_file_bytes:
                        member_file_bytes.close()

            return async_generator_wrapper()

        except zipfile.BadZipFile:
            # logger.warning("InMemoryStreamUnzipper: 捕獲到 BadZipFile 錯誤。")
            print("InMemoryStreamUnzipper: 捕獲到 BadZipFile 錯誤。", file=sys.stderr)
            self.close() # 確保緩衝區被清理
            return None
        except Exception as e:
            # logger.error(f"InMemoryStreamUnzipper: get_uncompressed_stream 中發生未預期錯誤: {e}")
            print(f"InMemoryStreamUnzipper: get_uncompressed_stream 中發生未預期錯誤: {e}", file=sys.stderr)
            self.close() # 確保緩衝區被清理
            return None
        # finally 子句不再需要，因為 ZipFile context manager 會處理 self._buffer 的關閉。
        # 如果 _load_zip_to_buffer 失敗，我們已經 close() 了。
        # 如果 is_zipfile 或 namelist 檢查失敗，我們也 close() 了。
        # BadZipFile 也 close() 了。
        # 成功的路徑，ZipFile context manager 關閉了 buffer，然後我們用 member_file_bytes，
        # async_generator_wrapper 的 finally 會關閉 member_file_bytes。
        # 所以，這裡的 finally 可能是不必要的，甚至可能導致重複關閉。
        # 為了安全起見，如果我們的設計是讓 unzipper 實例一次性使用，
        # 可以在外部調用 close。或者確保 close 是幂等的。

    def close(self) -> None:
        """
        關閉並釋放內部緩衝區。
        """
        if self._buffer:
            # logger.debug(f"InMemoryStreamUnzipper: 正在關閉 BytesIO 緩衝區 (用於 {self._first_file_name or '未知 ZIP 檔案'})。")
            print(f"InMemoryStreamUnzipper: 正在關閉 BytesIO 緩衝區 (用於 {self._first_file_name or '未知 ZIP 檔案'})。", file=sys.stdout)
            self._buffer.close()
            self._buffer = None # type: ignore # 設為 None 以防止重複關閉或使用已關閉的緩衝區
        # else:
            # logger.debug("InMemoryStreamUnzipper: close 被調用，但緩衝區已為 None。")
            # print("InMemoryStreamUnzipper: close 被調用，但緩衝區已為 None。", file=sys.stdout)

# --- End of Integrated Stream Unzipper Logic ---

# --- 路徑自我校正樣板碼 (已移除 InMemoryStreamUnzipper 相關導入) ---
try:
    current_dir = os.path.dirname(os.path.abspath(__file__))
    apps_dir = os.path.dirname(current_dir)
    project_root = os.path.dirname(apps_dir)
    if apps_dir not in sys.path: sys.path.insert(0, apps_dir)
    if project_root not in sys.path: sys.path.insert(0, project_root)
    # from apps.taifex_data_pipeline.stream_unzipper import InMemoryStreamUnzipper # 已移除
except Exception as e:
    print(f"路徑校正時發生錯誤: {e}", file=sys.stderr)
# --- 路徑自我校正樣板碼結束 ---

warnings.filterwarnings("ignore", message=".*_PyDriveImportHook.find_spec.*")

class SimpleLogger:
    def __init__(self, tz_str: str = 'Asia/Taipei', log_level: str = "INFO"):
        self.tz = pytz.timezone(tz_str)
        self.log_level_map = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
        self.current_log_level = self.log_level_map.get(log_level.upper(), 20)
    def _get_timestamp(self) -> str: return datetime.now(self.tz).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    def _log(self, message: str, level: str, details: str = ""):
        if self.log_level_map.get(level.upper(), 0) >= self.current_log_level:
            print(f"[{self._get_timestamp()}] [{level.upper()}] {message} {details}")
    def debug(self, m, d=""): self._log(m, "DEBUG", d)
    def info(self, m, d=""): self._log(m, "INFO", d)
    def success(self, m, d=""): self._log(m, "INFO", f"✅ {d}")
    def warning(self, m, d=""): self._log(m, "WARNING", f"⚠️ {d}")
    def error(self, m, d=""): self._log(m, "ERROR", f"❌ {d}")
    def header(self, m): self.info(f"\n{'='*60}\n=== {m.strip()} ===\n{'='*60}")
    def section(self, m): self.info(f"\n--- {m.strip()} ---")
    def hw_log(self, m, p="[HW_MONITOR]"): self.info(m, p)

logger: SimpleLogger = SimpleLogger()

class HardwareManager:
    def __init__(self, user_max_workers: Optional[int] = None, user_memory_limit_gb: Optional[int] = None):
        self.cpu_cores = os.cpu_count() or 2
        self.total_ram_gb = psutil.virtual_memory().total / (1024**3)
        self.max_workers = user_max_workers if user_max_workers is not None else max(1, round(self.cpu_cores * 0.8))
        self.memory_limit_gb = user_memory_limit_gb if user_memory_limit_gb is not None else int(self.total_ram_gb * 0.5)
    def get_status_line(self) -> str:
        cpu_percent = psutil.cpu_percent(); ram = psutil.virtual_memory(); disk = psutil.disk_usage('/')
        return f"CPU: {cpu_percent:.1f}% | RAM: {ram.percent:.1f}% ({ram.used/(1024**3):.2f}/{self.total_ram_gb:.2f} GB) | Disk: {disk.percent:.1f}%"
    def display_initial_dashboard(self):
        logger.header("硬體狀態與執行參數"); logger.info(f"CPU 核心數: {self.cpu_cores}"); logger.info(f"總記憶體: {self.total_ram_gb:.2f} GB")
        logger.info(f"並行處理核心數 (max_workers): {self.max_workers}"); logger.info(f"DuckDB 記憶體預算 (memory_limit): {self.memory_limit_gb} GB"); logger.info(self.get_status_line())
    def log_event_snapshot(self, event_name: str): logger.hw_log(f"[{event_name}] {self.get_status_line()}", "[HW_SNAPSHOT]")

TABLE_DEFINITIONS = {
    'daily_ohlc': "CREATE TABLE IF NOT EXISTS daily_ohlc (id UBIGINT PRIMARY KEY, trading_date DATE, product_id VARCHAR, expiry_month VARCHAR, strike_price DOUBLE, option_type VARCHAR, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, settlement_price DOUBLE, volume UBIGINT, open_interest UBIGINT, trading_session VARCHAR, change DOUBLE, change_percent DOUBLE, source VARCHAR);",
    'tick_data': "CREATE TABLE IF NOT EXISTS tick_data (id UBIGINT PRIMARY KEY, trade_datetime TIMESTAMP, product_id VARCHAR, expiry_month VARCHAR, strike_price DOUBLE, option_type VARCHAR, price DOUBLE, volume UBIGINT, source VARCHAR);",
    'institutional_investors': "CREATE TABLE IF NOT EXISTS institutional_investors (id UBIGINT PRIMARY KEY, data_date DATE, product_name VARCHAR, investor_type VARCHAR, instrument_type VARCHAR, option_type VARCHAR, long_pos_vol BIGINT, long_pos_val_twd_k BIGINT, short_pos_vol BIGINT, short_pos_val_twd_k BIGINT, net_pos_vol BIGINT, net_pos_val_twd_k BIGINT, long_oi_vol BIGINT, long_oi_val_twd_k BIGINT, short_oi_vol BIGINT, short_oi_val_twd_k BIGINT, net_oi_vol BIGINT, net_oi_val_twd_k BIGINT, source VARCHAR);",
    'pcr': "CREATE TABLE IF NOT EXISTS pcr (id UBIGINT PRIMARY KEY, data_date DATE, put_volume UBIGINT, call_volume UBIGINT, pcr_volume DOUBLE, put_oi UBIGINT, call_oi UBIGINT, pcr_oi DOUBLE, source VARCHAR);",
    'fx_rates': "CREATE TABLE IF NOT EXISTS fx_rates (id UBIGINT PRIMARY KEY, data_date DATE, usd_twd DOUBLE, cny_twd DOUBLE, eur_usd DOUBLE, usd_jpy DOUBLE, gbp_usd DOUBLE, aud_usd DOUBLE, usd_hkd DOUBLE, usd_cny DOUBLE, usd_zar DOUBLE, nzd_usd DOUBLE, source VARCHAR);"
}
SEQUENCES = {name: f"CREATE SEQUENCE IF NOT EXISTS seq_{name};" for name in TABLE_DEFINITIONS.keys()}
UNIQUE_INDICES = {
    'daily_ohlc': "CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_ohlc_unique ON daily_ohlc(trading_date, product_id, expiry_month, strike_price, option_type, trading_session);",
    'tick_data': "CREATE UNIQUE INDEX IF NOT EXISTS idx_tick_data_unique ON tick_data(trade_datetime, product_id, expiry_month, strike_price, option_type, price, volume);",
    'institutional_investors': "CREATE UNIQUE INDEX IF NOT EXISTS idx_inst_inv_unique ON institutional_investors(data_date, product_name, investor_type, instrument_type, option_type);",
    'pcr': "CREATE UNIQUE INDEX IF NOT EXISTS idx_pcr_unique ON pcr(data_date);",
    'fx_rates': "CREATE UNIQUE INDEX IF NOT EXISTS idx_fx_rates_unique ON fx_rates(data_date);"
}
MANUAL_COLUMN_NAMES = {
    'futures_daily': ['trading_date', 'product_id', 'expiry_month', 'open', 'high', 'low', 'close', 'change', 'change_percent', 'volume', 'settlement_price', 'open_interest', 'last_best_bid_price', 'last_best_ask_price', 'historical_high', 'historical_low', 'is_suspended', 'trading_session', 'spread_volume'],
    'options_daily_v1': ['trading_date', 'product_id', 'expiry_month', 'strike_price', 'option_type', 'open', 'high', 'low', 'close', 'volume', 'settlement_price', 'open_interest', 'last_best_bid_price', 'last_best_ask_price', 'historical_high', 'historical_low', 'is_suspended', 'trading_session'],
    'options_daily_v2': ['trading_date', 'product_id', 'expiry_month', 'strike_price', 'option_type', 'open', 'high', 'low', 'close', 'volume', 'settlement_price', 'open_interest', 'last_best_bid_price', 'last_best_ask_price', 'historical_high', 'historical_low', 'is_suspended', 'trading_session', 'change', 'change_percent']
}
FORMAT_MAP_FILENAME = "format_map.json"
RECIPE_SAMPLE_SIZE_BYTES = 16384

class AsyncBytesGeneratorReader: # 定義移到 run.py
    def __init__(self, async_byte_generator: AsyncGenerator[bytes, None], descriptor: str = "AsyncBytesGenReader"):
        self._generator = async_byte_generator
        self._buffer = bytearray()
        self._eof = False
        self._descriptor = descriptor
        logger.debug(f"[{self._descriptor}] AsyncBytesGeneratorReader initialized.")
    async def _fill_buffer_if_needed(self, needed_bytes: int = 0) -> None:
        if self._eof: return
        if not self._buffer or (needed_bytes > 0 and len(self._buffer) < needed_bytes):
            try:
                chunk = await self._generator.__anext__()
                if chunk: self._buffer.extend(chunk)
                else: self._eof = True
            except StopAsyncIteration: self._eof = True
            except Exception as e:
                logger.error(f"[{self._descriptor}] _fill_buffer_if_needed: Error reading from generator: {e}")
                self._eof = True
    async def read(self, n: int = -1) -> bytes:
        if n == 0: return b""
        if self._eof and not self._buffer: return b""
        if n == -1:
            temp_buffer_list = [bytes(self._buffer)]
            self._buffer.clear()
            try:
                async for chunk in self._generator:
                    temp_buffer_list.append(chunk)
            except Exception as e:
                logger.error(f"[{self._descriptor}] Error consuming generator in read(-1): {e}")
            finally: self._eof = True
            return b"".join(temp_buffer_list)
        else:
            while len(self._buffer) < n and not self._eof:
                await self._fill_buffer_if_needed(n)
                if self._eof and len(self._buffer) < n: break
            data_to_return = bytes(self._buffer[:n])
            self._buffer = self._buffer[n:]
            return data_to_return
    async def readchunk(self, size: int = 8192) -> bytes:
        if self._eof and not self._buffer: return b""
        if len(self._buffer) >= size:
            data_to_return = bytes(self._buffer[:size])
            self._buffer = self._buffer[size:]
            return data_to_return
        temp_chunk_list = []
        if self._buffer:
            temp_chunk_list.append(bytes(self._buffer))
            self._buffer.clear()
        current_len = sum(len(c) for c in temp_chunk_list)
        while current_len < size and not self._eof:
            try:
                chunk = await self._generator.__anext__()
                if chunk:
                    temp_chunk_list.append(chunk)
                    current_len += len(chunk)
                else: self._eof = True; break
            except StopAsyncIteration: self._eof = True; break
            except Exception as e: logger.error(f"[{self._descriptor}] readchunk: Error reading from generator: {e}"); self._eof = True; break
        final_data = b"".join(temp_chunk_list)
        if len(final_data) > size:
            self._buffer.extend(final_data[size:])
            return final_data[:size]
        return final_data
    async def readline(self) -> bytes:
        if self._eof and not self._buffer: return b""
        line_buffer = bytearray()
        while True:
            newline_pos = self._buffer.find(b'\n')
            if newline_pos != -1:
                line_buffer.extend(self._buffer[:newline_pos + 1])
                self._buffer = self._buffer[newline_pos + 1:]
                return bytes(line_buffer)
            else:
                line_buffer.extend(self._buffer)
                self._buffer.clear()
                if self._eof: return bytes(line_buffer) if line_buffer else b""
                try:
                    chunk = await self._generator.__anext__()
                    if chunk: self._buffer.extend(chunk)
                    else: self._eof = True; return bytes(line_buffer) if line_buffer else b""
                except StopAsyncIteration: self._eof = True; return bytes(line_buffer) if line_buffer else b""
                except Exception as e: logger.error(f"[{self._descriptor}] readline: Error reading from generator: {e}"); self._eof = True; return bytes(line_buffer) if line_buffer else b""
    def at_eof(self) -> bool: return self._eof and not self._buffer
    async def release(self):
        if not self._eof:
            try:
                async for _ in self._generator: pass
            except Exception: pass
            self._eof = True
        self._buffer.clear()
        logger.debug(f"AsyncBytesGeneratorReader for '{self._descriptor}' released.")

async def determine_parsing_recipe(
    stream_reader: Union[AsyncBytesGeneratorReader, Any],
    descriptor: str,
    sample_size: int = RECIPE_SAMPLE_SIZE_BYTES
) -> Tuple[Optional[Dict[str, Any]], bytes]:
    if not hasattr(stream_reader, 'read') or not callable(stream_reader.read):
        logger.error(f"[{descriptor}] 傳遞給 determine_parsing_recipe 的串流物件沒有 'read' 方法。")
        return None, b""
    logger.debug(f"[{descriptor}] 正在從串流讀取最多 {sample_size} 位元組用於配方判斷...")
    try:
        sample_bytes = await stream_reader.read(sample_size)
    except Exception as e:
        logger.error(f"[{descriptor}] 從串流讀取樣本數據時出錯: {e}")
        return None, b""
    if not sample_bytes:
        logger.warning(f"[{descriptor}] 從串流讀取的樣本數據為空。")
        return None, sample_bytes
    if descriptor.lower().endswith('.ods'):
        logger.info(f"[{descriptor}] 描述符以 .ods 結尾，標記為 excel_ods。注意：ODS 的串流配方判斷可能不準確。")
        return {"parser": "excel_ods", "args": {}, "pipeline": "unknown"}, sample_bytes
    sample_lines, detected_encoding = [], 'ms950'
    try:
        try: sample_text = sample_bytes.decode('ms950')
        except UnicodeDecodeError:
            try: detected_encoding = 'utf-8-sig'; sample_text = sample_bytes.decode(detected_encoding)
            except UnicodeDecodeError: detected_encoding = 'utf-8'; sample_text = sample_bytes.decode(detected_encoding)
        sample_lines = sample_text.splitlines()[:20]
    except UnicodeDecodeError:
        logger.warning(f"檔案 {descriptor} 樣本數據解碼失敗 (嘗試了 ms950, utf-8-sig, utf-8)。")
        return {"parser": "unknown_encoding", "args": {}, "pipeline": "unknown"}, sample_bytes
    except Exception as e:
        logger.warning(f"讀取 {descriptor} 樣本行出錯: {e}")
        return None, sample_bytes
    if not sample_lines:
        logger.warning(f"[{descriptor}] 樣本數據解碼後沒有有效行。")
        return None, sample_bytes
    header_line_raw = sample_lines[0].strip()
    first_data_line_raw = next((line.strip() for line in sample_lines[1:] if line.strip()), "")
    base_args = {"encoding": detected_encoding, "skipinitialspace": True, "thousands": ',', "dtype": "str", "on_bad_lines": "warn"}
    if "成交日期" in header_line_raw and "成交時間" in header_line_raw and "---" in first_data_line_raw:
        header_cols = [col.strip() for col in re.split(r'\s{2,}', header_line_raw)]
        skip_rows_count = next((i for i, line in enumerate(sample_lines) if '---' in line), 0) + 1
        return {"parser": "fwf", "args": {**base_args, "skiprows": skip_rows_count, "names": header_cols}, "pipeline": "tick_data"}, sample_bytes
    try:
        header_row_index = 0; found_header = False
        for i, line_text in enumerate(sample_lines):
            if any(k in line_text for k in ['交易日期', '商品', '身份別', '日期', '美元／新台幣', '買賣權成交量比率', '契約', '成交價格']):
                header_row_index = i; found_header = True; break
        if found_header:
            if header_row_index < len(sample_lines):
                temp_header_line = sample_lines[header_row_index]
                header_reader = csv.reader(io.StringIO(temp_header_line), skipinitialspace=base_args.get("skipinitialspace", True))
                try: df_cols = next(header_reader)
                except StopIteration:
                    logger.warning(f"[{descriptor}] 無法從樣本中解析動態CSV表頭行: '{temp_header_line}'")
                    df_cols = []
            else:
                logger.warning(f"[{descriptor}] 動態CSV表頭索引 ({header_row_index}) 超出樣本範圍 ({len(sample_lines)} 行)。")
                df_cols = []
            cols_set = {str(c).strip().replace(' ', '_').replace('(', '').replace(')', '') for c in df_cols}
            dyn_args = {**base_args, "header": header_row_index}
            if {'身份別', '商品名稱'}.issubset(cols_set): return {"parser": "csv_dynamic_header", "args": dyn_args, "pipeline": "institutional_investors"}, sample_bytes
            if {'美元／新台幣', '日期'}.issubset(cols_set): return {"parser": "csv_dynamic_header", "args": dyn_args, "pipeline": "fx_rates"}, sample_bytes
            if {'買賣權成交量比率', '日期'}.issubset(cols_set): return {"parser": "csv_dynamic_header", "args": dyn_args, "pipeline": "pcr"}, sample_bytes
            if {'交易日期', '契約', '收盤價'}.issubset(cols_set) and '成交時間' not in cols_set: return {"parser": "csv_dynamic_header", "args": dyn_args, "pipeline": "daily_ohlc"}, sample_bytes
            if {'成交日期', '商品代號', '成交價格'}.issubset(cols_set) and '成交時間' in cols_set: return {"parser": "csv_dynamic_header", "args": dyn_args, "pipeline": "tick_data"}, sample_bytes
    except Exception as e_dyn: logger.debug(f"動態CSV判斷 ({descriptor}) 失敗: {e_dyn}"); pass
    if header_line_raw and first_data_line_raw:
        h_parts = len(header_line_raw.split(','))
        key_found = None
        if h_parts == len(MANUAL_COLUMN_NAMES['options_daily_v2']): key_found = 'options_daily_v2'
        elif h_parts == len(MANUAL_COLUMN_NAMES['options_daily_v1']): key_found = 'options_daily_v1'
        if key_found:
            first_field_of_first_line = header_line_raw.split(',')[0].strip()
            if re.match(r"^\d{8}$", first_field_of_first_line) or \
               re.match(r"^\d{4}/\d{2}/\d{2}$", first_field_of_first_line) or \
               re.match(r"^\d{4}-\d{2}-\d{2}$", first_field_of_first_line):
                logger.info(f"檔案 {descriptor} 符合 {key_found} (手動欄位，無表頭) 特徵。")
                return {"parser": "csv_manual_cols", "args": {**base_args, "names_key": key_found, "header": None, "skiprows": 0}, "pipeline": "daily_ohlc"}, sample_bytes
    if header_line_raw:
        potential_data_cols_count = len(header_line_raw.split(','))
        if potential_data_cols_count == len(MANUAL_COLUMN_NAMES['futures_daily']):
            first_field = header_line_raw.split(',')[0].strip()
            if re.match(r"^\d{8}$", first_field) or re.match(r"^\d{4}/\d{2}/\d{2}$", first_field):
                logger.info(f"檔案 {descriptor} 符合 futures_daily (無表頭) 特徵，嘗試使用 csv_manual_cols。")
                manual_csv_args = {**base_args, "names_key": 'futures_daily', "header": None, "skiprows": 0}
                return {"parser": "csv_manual_cols", "args": manual_csv_args, "pipeline": "daily_ohlc"}, sample_bytes
    logger.warning(f"未能為 {descriptor} 確定解析配方。樣本首行預覽: {header_line_raw}")
    return {"parser": "unknown", "args": {"encoding": detected_encoding}, "pipeline": "unknown"}, sample_bytes

def parse_with_recipe(content_bytes: bytes, recipe: Dict[str, Any], descriptor: str) -> Optional[pd.DataFrame]:
    logger.warning(f"[{descriptor}] 舊的 parse_with_recipe 被調用，這可能表示流程未完全遷移到串流解析。")
    parser_type, args = recipe.get("parser"), recipe.get("args", {}).copy()
    if parser_type in ["unknown", "unknown_encoding"]: logger.warning(f"跳過 {descriptor} (配方: {parser_type})"); return None
    stream = io.BytesIO(content_bytes)
    try:
        if parser_type == "excel_ods": return pd.read_excel(stream, engine='odf', header=None, dtype=str)
        if parser_type == "fwf": return pd.read_fwf(stream, **args)
        if parser_type in ["csv", "csv_dynamic_header"]: return pd.read_csv(stream, **args)
        if parser_type == "csv_manual_cols":
            names_key = args.pop("names_key", None)
            if not names_key or names_key not in MANUAL_COLUMN_NAMES:
                 logger.error(f"[{descriptor}] csv_manual_cols 解析器缺少有效的 names_key: '{names_key}'。")
                 return None
            names = MANUAL_COLUMN_NAMES[names_key]
            return pd.read_csv(stream, names=names, usecols=range(len(names)), **args)
    except Exception as e: logger.error(f"解析 {descriptor} (配方 {parser_type}) 失敗: {e}"); return None
    logger.error(f"未知解析器 '{parser_type}' ({descriptor})"); return None

async def _iterate_text_lines(
    stream_reader: Union[AsyncBytesGeneratorReader, Any],
    consumed_sample_bytes: bytes,
    encoding: str,
    descriptor: str,
    chunk_size: int = 8192
) -> AsyncGenerator[str, None]:
    decoder = codecs.getincrementaldecoder(encoding)(errors='replace')
    buffer = ""

    if consumed_sample_bytes:
        try:
            buffer += decoder.decode(consumed_sample_bytes, final=False)
        except UnicodeDecodeError as e:
            logger.warning(f"[{descriptor}] 解碼 consumed_sample_bytes 時出錯: {e}.")
            try:
                buffer += consumed_sample_bytes.decode(encoding, errors='replace')
            except Exception as e_replace:
                 logger.error(f"[{descriptor}] consumed_sample_bytes 無法用 '{encoding}' (errors='replace') 解碼: {e_replace}，將被忽略。")

    while '\n' in buffer:
        line, _, buffer = buffer.partition('\n')
        yield line.rstrip('\r')

    read_method_to_use = None
    if hasattr(stream_reader, 'readchunk') and callable(stream_reader.readchunk):
        read_method_to_use = stream_reader.readchunk
    elif hasattr(stream_reader, 'read') and callable(stream_reader.read): # type: ignore
        read_method_to_use = stream_reader.read # type: ignore

    if not read_method_to_use:
        logger.warning(f"[{descriptor}] _iterate_text_lines: stream_reader 沒有有效的 readchunk 或 read 方法。")
        if buffer: yield buffer.rstrip('\r')
        return

    while True:
        try:
            chunk = await read_method_to_use(chunk_size)
            if not chunk:
                break
            buffer += decoder.decode(chunk, final=False)
            while '\n' in buffer:
                line, _, buffer = buffer.partition('\n')
                yield line.rstrip('\r')
        except Exception as e:
            logger.error(f"[{descriptor}] 從串流讀取或解碼數據塊時出錯 (_iterate_text_lines): {e}")
            try:
                final_chunk_from_decoder_on_error = decoder.decode(b'', final=True)
                if final_chunk_from_decoder_on_error:
                    buffer += final_chunk_from_decoder_on_error
            except Exception as e_final_decode:
                logger.error(f"[{descriptor}] 清理解碼器時發生額外錯誤: {e_final_decode}")
            break

    final_chunk_from_decoder = decoder.decode(b'', final=True)
    if final_chunk_from_decoder:
        buffer += final_chunk_from_decoder

    if buffer:
        while '\n' in buffer:
            line, _, buffer = buffer.partition('\n')
            yield line.rstrip('\r')
        if buffer:
             yield buffer.rstrip('\r')

async def async_generate_rows_from_csv(
    stream_reader: Union[AsyncBytesGeneratorReader, Any],
    consumed_sample_bytes: bytes,
    recipe_args: Dict[str, Any],
    descriptor: str,
    manual_names: Optional[List[str]] = None
) -> AsyncGenerator[Dict[str, Any], None]:
    encoding = recipe_args.get("encoding", "utf-8")
    skip_initial_space = recipe_args.get("skipinitialspace", True)
    header_row_index = recipe_args.get("header", 0) if not manual_names else None # type: ignore
    skip_rows = recipe_args.get("skiprows", 0)

    line_iterator = _iterate_text_lines(stream_reader, consumed_sample_bytes, encoding, descriptor)
    current_line_num_for_log = 0

    try:
        if manual_names:
            for _ in range(skip_rows): # type: ignore
                try:
                    await line_iterator.__anext__()
                except StopAsyncIteration:
                    logger.warning(f"[{descriptor}] 在跳過 manual_cols 的前 {skip_rows} 行時檔案提前結束。")
                    return

            async for line_text in line_iterator:
                current_line_num_for_log += 1
                if not line_text.strip(): continue
                try:
                    row_values = next(csv.reader([line_text], skipinitialspace=skip_initial_space))
                except csv.Error as e_csv_line:
                    logger.warning(f"[{descriptor}] CSV manual_cols 解析行 '{line_text[:100]}...' (數據行號 {current_line_num_for_log}) 時出錯: {e_csv_line}")
                    continue

                if len(row_values) < len(manual_names):
                    row_values.extend([None] * (len(manual_names) - len(row_values)))
                elif len(row_values) > len(manual_names):
                    row_values = row_values[:len(manual_names)]
                yield dict(zip(manual_names, row_values))
        else:
            header_list_cleaned = None
            for i in range(header_row_index): # type: ignore
                try:
                    await line_iterator.__anext__()
                except StopAsyncIteration:
                    logger.warning(f"[{descriptor}] 在尋找 CSV 表頭時檔案提前結束 (目標表頭行索引: {header_row_index})。")
                    return

            try:
                header_line_text = await line_iterator.__anext__()
                header_list_raw = next(csv.reader([header_line_text], skipinitialspace=skip_initial_space))
                header_list_cleaned = [str(col).strip() for col in header_list_raw]
            except StopAsyncIteration:
                logger.warning(f"[{descriptor}] 無法讀取 CSV 表頭行 (在跳過 {header_row_index} 行之後)。")
                return
            except csv.Error as e_csv_header:
                logger.error(f"[{descriptor}] 解析 CSV 表頭行 '{header_line_text[:100]}...' 時出錯: {e_csv_header}") # type: ignore
                return

            async for line_text in line_iterator:
                current_line_num_for_log += 1
                if not line_text.strip(): continue
                try:
                    row_values = next(csv.reader([line_text], skipinitialspace=skip_initial_space))
                except csv.Error as e_csv_line:
                    logger.warning(f"[{descriptor}] CSV dynamic_header 解析行 '{line_text[:100]}...' (數據行號 {current_line_num_for_log}) 時出錯: {e_csv_line}")
                    continue

                if not any(field and field.strip() for field in row_values): continue

                if header_list_cleaned:
                    if len(row_values) < len(header_list_cleaned):
                        row_values.extend([None] * (len(header_list_cleaned) - len(row_values)))
                    elif len(row_values) > len(header_list_cleaned):
                        row_values = row_values[:len(header_list_cleaned)]
                    yield dict(zip(header_list_cleaned, row_values))
                else:
                    logger.warning(f"[{descriptor}] CSV dynamic_header 表頭未解析，無法處理數據行。")
                    break
    except Exception as e:
        logger.error(f"[{descriptor}] 在 async_generate_rows_from_csv (數據行號 {current_line_num_for_log} 附近) 中發生未知錯誤: {e}")

async def async_generate_rows_from_fwf(
    stream_reader: Union[AsyncBytesGeneratorReader, Any],
    consumed_sample_bytes: bytes,
    recipe_args: Dict[str, Any],
    descriptor: str
) -> AsyncGenerator[Dict[str, Any], None]:
    encoding = recipe_args.get("encoding", "utf-8")
    skip_rows = recipe_args.get("skiprows", 0)
    names = recipe_args.get("names")

    if not names:
        logger.error(f"[{descriptor}] FWF 解析需要 'names' (欄位名列表) 在 recipe_args 中。")
        return

    line_iterator = _iterate_text_lines(stream_reader, consumed_sample_bytes, encoding, descriptor)
    current_line_num_for_log = 0

    try:
        for _ in range(skip_rows): # type: ignore
            try:
                await line_iterator.__anext__()
            except StopAsyncIteration:
                logger.warning(f"[{descriptor}] 在跳過 FWF 的前 {skip_rows} 行時檔案提前結束。")
                return

        async for line_text in line_iterator:
            current_line_num_for_log += 1
            line_to_parse = line_text.rstrip('\n\r')
            if not line_to_parse.strip(): continue

            row_values = re.split(r'\s{2,}', line_to_parse.strip())
            if len(row_values) < len(names):
                row_values.extend([None] * (len(names) - len(row_values)))
            elif len(row_values) > len(names):
                row_values = row_values[:len(names)]
            yield dict(zip(names, row_values))

    except Exception as e:
        logger.error(f"[{descriptor}] 在 async_generate_rows_from_fwf (數據行號 {current_line_num_for_log} 附近) 中發生未知錯誤: {e}")

async def parse_content_to_arrow(
    stream_reader: Union[AsyncBytesGeneratorReader, Any],
    consumed_sample_bytes: bytes,
    recipe: Dict[str, Any],
    descriptor: str,
    batch_size: int = 10000
) -> Optional[pyarrow.Table]:
    parser_type = recipe.get("parser")
    args = recipe.get("args", {}).copy()
    pipeline_name = recipe.get("pipeline", "unknown")

    if parser_type in ["unknown", "unknown_encoding"]:
        logger.warning(f"跳過 {descriptor} (配方: {parser_type})")
        return None

    if parser_type == "excel_ods":
        logger.info(f"[{descriptor}] ODS 檔案類型，將嘗試完整讀取串流內容。")
        full_content_bytes_list = [consumed_sample_bytes]
        if hasattr(stream_reader, 'read') and callable(stream_reader.read): # type: ignore
            while True:
                chunk = await stream_reader.read(RECIPE_SAMPLE_SIZE_BYTES) # type: ignore
                if not chunk: break
                full_content_bytes_list.append(chunk)
        else:
            logger.warning(f"[{descriptor}] ODS 處理：stream_reader 沒有有效的 read 方法，僅使用 consumed_sample_bytes。")

        full_content_bytes = b"".join(full_content_bytes_list)

        if not full_content_bytes:
            logger.warning(f"[{descriptor}] ODS 檔案串流讀取後內容為空。")
            return None

        try:
            temp_df = pd.read_excel(io.BytesIO(full_content_bytes), engine='odf', header=None, dtype=str)
            if temp_df is None or temp_df.empty:
                logger.warning(f"[{descriptor}] ODS 解析步驟未能從內容生成初始 DataFrame。")
                return None

            pipeline_func = PIPELINE_MAP.get(pipeline_name)
            df_processed = temp_df if not pipeline_func else pipeline_func(temp_df.copy(), descriptor)

            if df_processed is None or df_processed.empty:
                logger.warning(f"[{descriptor}] 管線 {pipeline_name} 未返回 ODS DataFrame 或為空。")
                return None
            if 'id' in df_processed.columns: df_processed = df_processed.drop(columns=['id'])
            arrow_table = pyarrow.Table.from_pandas(df_processed, preserve_index=False)
            logger.success(f"[{descriptor}] (ODS) 成功將 DataFrame 轉換為 Arrow Table, 行數: {len(arrow_table)}")
            return arrow_table
        except Exception as e:
            logger.error(f"[{descriptor}] (ODS) DataFrame 到 Arrow Table 轉換失敗: {e}")
            return None

    row_iterator: Optional[AsyncGenerator[Dict[str, Any], None]] = None
    if parser_type in ["csv_dynamic_header", "csv_manual_cols", "csv"]:
        manual_names_key = args.pop("names_key", None) if parser_type == "csv_manual_cols" else None
        manual_names_list = MANUAL_COLUMN_NAMES.get(manual_names_key) if manual_names_key else None
        row_iterator = async_generate_rows_from_csv(stream_reader, consumed_sample_bytes, args, descriptor, manual_names=manual_names_list)
    elif parser_type == "fwf":
        row_iterator = async_generate_rows_from_fwf(stream_reader, consumed_sample_bytes, args, descriptor)

    if not row_iterator:
        logger.error(f"[{descriptor}] 未能為解析器類型 '{parser_type}' 創建行迭代器。")
        return None

    processed_rows_for_arrow = []
    row_processor = PIPELINE_ROW_PROCESSORS.get(pipeline_name)

    if not row_processor:
        logger.warning(f"[{descriptor}] 未找到針對管線 '{pipeline_name}' 的特定行處理器。將嘗試直接轉換原始行。")
        async for raw_row_dict in row_iterator:
            if raw_row_dict: processed_rows_for_arrow.append(raw_row_dict)
    else:
        if pipeline_name == "daily_ohlc":
            ohlc_map = {'交易日期':'trading_date','契約':'product_id','商品代號':'product_id','到期月份_週別':'expiry_month','到期月份／週別':'expiry_month','履約價':'strike_price','買賣權':'option_type','開盤價':'open','最高價':'high','最低價':'low','收盤價':'close','成交量':'volume','結算價':'settlement_price','未沖銷契約數':'open_interest','交易時段':'trading_session','漲跌價':'change','漲跌percent':'change_percent'}
            ohlc_req_cols = ['trading_date','product_id','close']
            async for raw_row_dict in row_iterator:
                cleaned_row = _clean_and_prepare_row(raw_row_dict, ohlc_req_cols, ohlc_map, descriptor)
                processed_row = row_processor(cleaned_row, descriptor)
                if processed_row: processed_rows_for_arrow.append(processed_row)
        else:
            async for raw_row_dict in row_iterator:
                processed_row = row_processor(raw_row_dict, descriptor)
                if processed_row: processed_rows_for_arrow.append(processed_row)

    if not processed_rows_for_arrow:
        logger.warning(f"[{descriptor}] {pipeline_name} 串流處理後沒有有效數據行可供轉換為 Arrow。")
        return None

    try:
        arrow_table = pyarrow.Table.from_pylist(processed_rows_for_arrow)
        logger.success(f"[{descriptor}] ({pipeline_name}串流) 成功將處理後的行直接轉換為 Arrow Table, 行數: {len(arrow_table)}")
        return arrow_table
    except Exception as e:
        logger.error(f"[{descriptor}] ({pipeline_name}串流) 從 pylist 到 Arrow Table 轉換失敗: {e}")
        try:
            logger.info(f"[{descriptor}] 嘗試使用 Pandas DataFrame 作為中介進行 Arrow 轉換回退...")
            fallback_df = pd.DataFrame(processed_rows_for_arrow)
            if 'id' in fallback_df.columns: fallback_df = fallback_df.drop(columns=['id'])
            arrow_table = pyarrow.Table.from_pandas(fallback_df, preserve_index=False)
            logger.info(f"[{descriptor}] ({pipeline_name}串流-回退) Pandas 中介轉換成功。")
            return arrow_table
        except Exception as e_fallback:
            logger.error(f"[{descriptor}] ({pipeline_name}串流-回退) Pandas 中介轉換也失敗: {e_fallback}")
            return None

# --- process_file_content, async_main, main 函數定義不變，故省略以減少 diff 大小 ---
# ... (這些函數的定義與上一個 overwrite_file_with_block 中的版本相同)
# 僅確保 async_main 中使用 InMemoryStreamUnzipper 和 AsyncBytesGeneratorReader 的邏輯正確

async def process_file_content(
    descriptor: str,
    stream_reader: Union[AsyncBytesGeneratorReader, Any],
    format_map: Dict[str, Any],
    db_conn: duckdb.DuckDBPyConnection,
    hw_mgr: HardwareManager
):
    logger.info(f"開始處理串流: {descriptor}")
    recipe_from_map = format_map.get(descriptor)
    recipe: Optional[Dict[str, Any]] = None
    consumed_sample_bytes: bytes = b""
    map_updated_locally = False

    if recipe_from_map:
        recipe = recipe_from_map
        logger.info(f"為 {descriptor[:50]} 從 format_map 找到配方: {recipe.get('pipeline') if recipe else '無'}")
        consumed_sample_bytes = b""
    else:
        determined_recipe_tuple = await determine_parsing_recipe(stream_reader, descriptor)
        if determined_recipe_tuple:
            recipe, consumed_sample_bytes = determined_recipe_tuple
            if recipe:
                format_map[descriptor] = recipe
                map_updated_locally = True
                logger.info(f"學習到新配方 for {descriptor[:50]} -> {recipe.get('pipeline')}")
            else:
                format_map[descriptor] = {"parser": "unknown", "pipeline": "unknown", "args": {}}
                map_updated_locally = True
                logger.warning(f"未能學習到配方 for {descriptor[:50]} (determine_parsing_recipe 返回 None 配方)")
        else:
            format_map[descriptor] = {"parser": "unknown", "pipeline": "unknown", "args": {}}
            map_updated_locally = True
            logger.warning(f"未能學習到配方 for {descriptor[:50]} (determine_parsing_recipe 本身返回 None)")
            return {"status": "error_learning_recipe", "descriptor": descriptor, "map_updated": map_updated_locally}

    if not recipe or recipe.get("pipeline") == "unknown" or recipe.get("parser") in ["unknown", "unknown_encoding"]:
        logger.info(f"因未知/無效配方跳過 {descriptor[:50]}")
        return {"status": "skipped_invalid_recipe", "descriptor": descriptor, "map_updated": map_updated_locally}

    arrow_table = await parse_content_to_arrow(
        stream_reader,
        consumed_sample_bytes,
        recipe,
        descriptor
    )

    if arrow_table is None or arrow_table.num_rows == 0:
        logger.warning(f"[{descriptor}] 未能從內容生成 Arrow Table 或 Table 為空。")
        return {"status": "skipped_no_arrow_data", "descriptor": descriptor, "map_updated": map_updated_locally}

    table_name = recipe.get("pipeline", "unknown_pipeline")
    if table_name not in TABLE_DEFINITIONS:
        logger.warning(f"Arrow Table 的目標資料表 '{table_name}' 未在 TABLE_DEFINITIONS 中定義，跳過載入。")
        return {"status": "skipped_unknown_table", "descriptor": descriptor, "map_updated": map_updated_locally}

    try:
        temp_view_name = f"arrow_view_{hashlib.sha256(descriptor.encode()).hexdigest()[:8]}"
        db_conn.register(temp_view_name, arrow_table)

        idx_sql = UNIQUE_INDICES.get(table_name)
        uq_cols_str = ""
        if idx_sql:
            match = re.search(r'\((.*?)\)', idx_sql)
            uq_cols_str = match.group(1) if match else ""

        target_cols_info = db_conn.execute(f"DESCRIBE {table_name};").fetchall()
        target_cols = {row[0] for row in target_cols_info if row[0] != 'id'}
        arrow_cols = set(arrow_table.schema.names)
        insert_cols_list = list(target_cols.intersection(arrow_cols))

        if not insert_cols_list:
            logger.warning(f"[{descriptor}] Arrow Table 與目標表 {table_name} 沒有共同的可插入欄位。")
            db_conn.unregister(temp_view_name)
            return {"status": "skipped_no_common_columns", "descriptor": descriptor, "map_updated": map_updated_locally}

        insert_cols_str = ", ".join(f'"{c}"' for c in insert_cols_list)
        select_cols_str = insert_cols_str
        sql_insert = f"INSERT INTO {table_name} (id, {insert_cols_str}) SELECT nextval('seq_{table_name}'), {select_cols_str} FROM {temp_view_name}"
        if uq_cols_str: sql_insert += f" ON CONFLICT ({uq_cols_str}) DO NOTHING"

        db_conn.execute(sql_insert)
        logger.success(f"[{descriptor}] 成功將 Arrow Table ({arrow_table.num_rows} 行) 載入到 DuckDB 表 '{table_name}'。")
        db_conn.unregister(temp_view_name)
        if hw_mgr: hw_mgr.log_event_snapshot(f"Processed_stream: {descriptor[:30]}")

        return {"status": "success", "rows_in_arrow": arrow_table.num_rows, "table": table_name, "descriptor": descriptor, "map_updated": map_updated_locally}

    except Exception as e:
        logger.error(f"[{descriptor}] 處理/載入 Arrow Table 到 DuckDB 表 '{table_name}' 時失敗: {e}")
        try: db_conn.unregister(temp_view_name)
        except: pass
        return {"status": "error_loading_to_db", "descriptor": descriptor, "error_msg": str(e), "map_updated": map_updated_locally}

async def async_main(
    input_streams: List[Tuple[str, Union[Any, AsyncBytesGeneratorReader]]],
    db_output_dir_arg: str,
    db_name_arg: str,
    temp_dir_arg: Optional[str],
    max_workers_arg: Optional[int],
    memory_limit_gb_arg: Optional[int],
    log_level_arg: str
):
    global logger
    logger = SimpleLogger(log_level=log_level_arg)
    logger.header("TAIFEX Pipeline (Async Full Stream v20.3) Starting")

    db_out_dir = os.path.abspath(db_output_dir_arg)
    db_fpath = os.path.join(db_out_dir, db_name_arg)
    fmt_map_fpath = os.path.join(db_out_dir, FORMAT_MAP_FILENAME)
    tmp_dir_root = os.path.abspath(temp_dir_arg) if temp_dir_arg else os.path.join(db_out_dir, "temp_pipeline_async")
    duckdb_tmp_p = os.path.join(tmp_dir_root, "duckdb_temp")
    for p in [db_out_dir, tmp_dir_root, duckdb_tmp_p]: os.makedirs(p, exist_ok=True)

    hw_mgr = HardwareManager(max_workers_arg, memory_limit_gb_arg)
    hw_mgr.display_initial_dashboard()
    t_start = time.time()

    format_map: Dict[str, Any] = {}
    if os.path.exists(fmt_map_fpath):
        try:
            with open(fmt_map_fpath, 'r', encoding='utf-8') as f: format_map = json.load(f)
            logger.info(f"已載入 format map: {len(format_map)} 個條目。")
        except Exception as e: logger.warning(f"載入 format map 失敗: {e}")
    else: logger.info("未找到現有 format map，將創建新的。")

    db_conn: Optional[duckdb.DuckDBPyConnection] = None
    try:
        db_conn = duckdb.connect(database=db_fpath, read_only=False)
        db_conn.execute(f"SET memory_limit='{hw_mgr.memory_limit_gb}GB';")
        db_conn.execute(f"SET threads={hw_mgr.max_workers};")
        db_conn.execute(f"SET temp_directory='{duckdb_tmp_p}';")
        for sql in SEQUENCES.values(): db_conn.execute(sql)
        for sql in TABLE_DEFINITIONS.values(): db_conn.execute(sql)
        for idx_name, idx_sql in UNIQUE_INDICES.items():
            try: db_conn.execute(idx_sql)
            except Exception as e_idx: logger.warning(f"創建唯一索引 {idx_name} 失敗: {e_idx}")
        logger.info("DuckDB 資料庫結構已初始化。")
    except Exception as e:
        logger.error(f"DuckDB 初始化失敗: {e}")
        if db_conn: db_conn.close()
        return {"status": "error", "message": f"DuckDB 初始化失敗: {e}"}

    total_files_processed = 0
    total_rows_in_arrow = 0
    overall_map_updated = False
    errors_encountered = 0
    tasks = []

    for initial_descriptor, initial_stream_reader in input_streams:
        if initial_descriptor.lower().endswith(".zip"):
            logger.info(f"檢測到 ZIP 串流: {initial_descriptor}，將使用 InMemoryStreamUnzipper 處理。")
            unzipper = InMemoryStreamUnzipper(initial_stream_reader)
            try:
                # InMemoryStreamUnzipper (Directive v20.3) 的 get_uncompressed_stream
                # 處理第一個檔案並返回 AsyncGenerator[bytes, None]。
                # 我們需要為這個流構造一個描述符。
                member_descriptor = f"{initial_descriptor} -> member_0_from_unzipper"
                logger.info(f"準備處理來自 ZIP 的第一個成員串流: {member_descriptor}")

                member_byte_agen = unzipper.get_uncompressed_stream()
                adapted_member_stream = AsyncBytesGeneratorReader(member_byte_agen, member_descriptor)

                task = asyncio.create_task(
                    process_file_content(member_descriptor, adapted_member_stream, format_map, db_conn, hw_mgr)
                )
                tasks.append(task)
            except Exception as e_unzip:
                logger.error(f"處理 ZIP 串流 {initial_descriptor} 時解壓縮或任務創建失敗: {e_unzip}")
                errors_encountered += 1
            finally:
                unzipper.close() # 手動調用 close，因為沒有使用 async with
        else:
            logger.info(f"準備處理直接串流: {initial_descriptor}")
            task = asyncio.create_task(
                process_file_content(initial_descriptor, initial_stream_reader, format_map, db_conn, hw_mgr) # type: ignore
            )
            tasks.append(task)

    results = await asyncio.gather(*tasks, return_exceptions=True)

    for res in results:
        if isinstance(res, Exception):
            logger.error(f"任務執行時發生未捕獲異常: {res}")
            errors_encountered += 1
        elif res:
            total_files_processed +=1
            if res.get("map_updated"): overall_map_updated = True
            if res.get("status") == "success":
                total_rows_in_arrow += res.get("rows_in_arrow", 0)
            elif res.get("status", "").startswith("error_"):
                errors_encountered += 1
                logger.error(f"處理檔案/串流 {res.get('descriptor')} 時發生錯誤: {res.get('error_msg', '未知錯誤')}")
            elif res.get("status", "").startswith("skipped_"):
                 logger.info(f"串流 {res.get('descriptor')} 被跳過: {res.get('status')}")

    logger.header("資料處理階段摘要")
    logger.success(f"總共處理的串流 (包括ZIP成員) 數量: {total_files_processed}")
    logger.success(f"從 Arrow 表嘗試載入的總行數: {total_rows_in_arrow:,}")
    logger.info(f"錯誤發生次數: {errors_encountered}")

    if overall_map_updated:
        try:
            with open(fmt_map_fpath, 'w', encoding='utf-8') as f: json.dump(format_map, f, indent=4, ensure_ascii=False)
            logger.info(f"Format map 已儲存至 {fmt_map_fpath}")
        except Exception as e: logger.error(f"儲存 format map 失敗: {e}")

    if db_conn: db_conn.close()
    logger.header(f"Pipeline finished. Total time: {time.time() - t_start:.2f}s. DB: {db_fpath}")

    if errors_encountered > 0:
        logger.error("管線執行期間發生錯誤。")
        return {"status": "error", "message": "管線執行期間發生一個或多個錯誤。", "processed": total_files_processed, "loaded_rows": total_rows_in_arrow, "errors": errors_encountered }

    return {"status": "success", "processed": total_files_processed, "loaded_rows": total_rows_in_arrow, "db_path": db_fpath}

def main():
    parser = argparse.ArgumentParser(description="TAIFEX Pipeline (Async Full Stream v20.3)")
    parser.add_argument("--db-output-dir", required=True)
    parser.add_argument("--db-name", default="taifex_full_stream_analytics.duckdb")
    parser.add_argument("--temp-dir", default=None)
    parser.add_argument("--max-workers",type=int,default=None)
    parser.add_argument("--memory-limit-gb",type=int,default=None)
    parser.add_argument("--log-level",default="INFO")
    args = parser.parse_args()

    logger.info("TAIFEX Pipeline (Async Full Stream v20.3) 腳本已啟動。")
    logger.info("注意：此版本設計為被其他模組調用並傳入數據串流。")
    logger.info("若要直接運行進行測試，需在 main() 中提供模擬的 input_streams。")

    if len(sys.argv) <= 1 or not args.db_output_dir :
        parser.print_help()
        sys.exit(1)

if __name__ == "__main__":
    print("TAIFEX Pipeline (Async Full Stream v20.3) - 主執行入口。")
    print("此版本主要設計為庫使用，直接執行僅用於基本演示或需要模擬輸入串流。")
    pass
