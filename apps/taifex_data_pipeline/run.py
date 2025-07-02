# -*- coding: utf-8 -*-
# 精煉廠主執行檔 (v16.0 Hotfix 後的批次掃描版本 - 修正 institutional_investors)
import os
# 確保 DuckDB 和其他數值計算庫在受限環境下不會因線程競爭導致效能下降
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
from typing import Generator, Tuple, Dict, Any, Optional, List, AsyncGenerator # AsyncGenerator 新增
import asyncio # 新增
import concurrent.futures # 保留 ProcessPoolExecutor 以便逐步遷移，最終可能移除

import pandas as pd
import psutil
import pyarrow
import duckdb
import pytz
import zipfile # 確保 zipfile 已導入

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

logger = SimpleLogger()

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

async def discover_and_stream_files(root_path: str) -> AsyncGenerator[Tuple[str, bytes], None]:
    """
    非同步地發現檔案，並以串流方式處理 ZIP 檔案內容。
    產出 (描述符, 檔案位元組內容)。
    與原始 discover_files_recursively 的 descriptor 產出邏輯盡可能保持一致。
    """
    if not os.path.exists(root_path):
        logger.warning(f"輸入路徑不存在: {root_path}")
        return

    # items_to_scan 儲存 (描述符前綴, 實際路徑)
    # 描述符前綴的規則：
    # - 對於 root_path 本身（如果是檔案），前綴為空字串。
    # - 對於 root_path 目錄下的頂層檔案/目錄，前綴為空字串。
    # - 對於子目錄中的檔案/目錄，前綴是從 root_path 到該子目錄的相對路徑名稱。
    items_to_scan = []
    if os.path.isfile(root_path):
        # 如果 root_path 是檔案，描述符就是其檔名，所以前綴是空，路徑是它自己
        items_to_scan.append(("", root_path))
    elif os.path.isdir(root_path):
        # 如果 root_path 是目錄，其下項目的描述符就是它們自己的名字，所以前綴也是空
        for name in sorted(os.listdir(root_path)):
            items_to_scan.append(("", os.path.join(root_path, name)))

    processed_paths = set() # 用於避免重複處理同一路徑（主要針對目錄結構循環，雖然罕見）

    while items_to_scan:
        descriptor_base, current_item_path = items_to_scan.pop(0) # 使用 current_item_path 代替 current_path 以免混淆
        if current_item_path in processed_paths:
            continue
        processed_paths.add(current_item_path)

        try:
            current_item_name = os.path.basename(current_item_path)
            # 構造當前項目的描述符，如果 descriptor_base 為空，則直接用項目名，否則用 base/名
            current_descriptor = os.path.join(descriptor_base, current_item_name) if descriptor_base else current_item_name

            if os.path.isdir(current_item_path):
                new_items = []
                for name_in_dir in sorted(os.listdir(current_item_path)):
                    # 遍歷目錄時，新的 descriptor_base 就是當前目錄的 current_descriptor
                    new_items.append((current_descriptor, os.path.join(current_item_path, name_in_dir)))
                items_to_scan = new_items + items_to_scan # 深度優先，將新項目加到隊首
                continue

            # 至此，current_item_path 是一個檔案
            # For non-async file reading, we can wrap it, or for initial non-network reads, direct is fine.
            # However, to make it truly async for future extensions (e.g. aiohttp source), use run_in_executor
            loop = asyncio.get_running_loop()
            # 使用 current_item_path 而不是 current_path
            with open(current_item_path, 'rb') as f_content:
                 content_bytes = await loop.run_in_executor(None, f_content.read)

            # current_descriptor 已在 try 塊開始時根據 descriptor_base 和 current_item_name 正確構造
            # final_descriptor 應為 current_descriptor
            final_descriptor_for_file = current_descriptor

            if current_item_path.lower().endswith('.zip'):
                try:
                    with zipfile.ZipFile(io.BytesIO(content_bytes), 'r') as zf:
                        for member_info in sorted(zf.infolist(), key=lambda mi: mi.filename):
                            if member_info.is_dir() or '__MACOSX' in member_info.filename:
                                continue
                            member_content_bytes = await loop.run_in_executor(None, zf.read, member_info.filename)
                            # ZIP成員的描述符是 "原始檔案描述符 ->成員路徑"
                            yield f"{final_descriptor_for_file} -> {member_info.filename}", member_content_bytes
                except zipfile.BadZipFile:
                    logger.warning(f"檔案 '{current_item_path}' (描述: {final_descriptor_for_file}) 不是有效的 ZIP 檔案，將其視為普通二進制檔案。")
                    yield final_descriptor_for_file, content_bytes
                except Exception as e_zip:
                     logger.warning(f"處理 ZIP 檔案 '{current_item_path}' (描述: {final_descriptor_for_file}) 時發生錯誤: {e_zip}")
            else:
                yield final_descriptor_for_file, content_bytes
        except Exception as e:
            # 此處的 current_descriptor 可能在 try 塊失敗前未完全形成，或者 current_item_path 更可靠
            # descriptor_base 和 current_item_path 是在 while 迴圈中定義的，在此處應該可見
            failed_item_name = os.path.basename(current_item_path)
            context_descriptor = os.path.join(descriptor_base, failed_item_name) if descriptor_base else failed_item_name
            logger.warning(f"讀取/處理 '{context_descriptor}' (路徑: {current_item_path}) 失敗: {e}")


def determine_parsing_recipe(content_bytes: bytes, descriptor: str) -> Optional[Dict[str, Any]]:
    if not content_bytes: return None
    if descriptor.lower().endswith('.ods'): return {"parser": "excel_ods", "args": {}, "pipeline": "unknown"}
    sample_lines, detected_encoding = [], 'ms950'
    try:
        try: sample_text = content_bytes.decode('ms950')
        except UnicodeDecodeError:
            try: detected_encoding = 'utf-8-sig'; sample_text = content_bytes.decode(detected_encoding)
            except UnicodeDecodeError: detected_encoding = 'utf-8'; sample_text = content_bytes.decode(detected_encoding)
        sample_lines = sample_text.splitlines()[:20]
    except UnicodeDecodeError: logger.warning(f"檔案 {descriptor} 解碼失敗 (ms950, utf-8, utf-8-sig)。"); return {"parser": "unknown_encoding", "args": {}, "pipeline": "unknown"}
    except Exception as e: logger.warning(f"讀取 {descriptor} 樣本行出錯: {e}"); return None
    if not sample_lines: return None
    header_line_raw = sample_lines[0].strip()
    first_data_line_raw = next((line.strip() for line in sample_lines[1:] if line.strip()), "")
    base_args = {"encoding": detected_encoding, "skipinitialspace": True, "thousands": ',', "dtype": "str", "on_bad_lines": "warn"}

    if "成交日期" in header_line_raw and "成交時間" in header_line_raw and "---" in first_data_line_raw: # Tick Data FWF
        header_cols = [col.strip() for col in re.split(r'\s{2,}', header_line_raw)]
        skip_rows_count = next((i for i, line in enumerate(sample_lines) if '---' in line), 0) + 1
        return {"parser": "fwf", "args": {**base_args, "skiprows": skip_rows_count, "names": header_cols}, "pipeline": "tick_data"}
    try: # Dynamic Header CSVs
        header_row_index = 0; found_header = False
        for i, line_text in enumerate(sample_lines):
            if any(k in line_text for k in ['交易日期', '商品', '身份別', '日期', '美元／新台幣', '買賣權成交量比率', '契約', '成交價格']):
                header_row_index = i; found_header = True; break
        if found_header:
            df_cols = pd.read_csv(io.BytesIO(content_bytes), header=header_row_index, nrows=0, **base_args).columns
            cols_set = {str(c).strip().replace(' ', '_').replace('(', '').replace(')', '') for c in df_cols}
            dyn_args = {**base_args, "header": header_row_index}
            if {'身份別', '商品名稱'}.issubset(cols_set): return {"parser": "csv_dynamic_header", "args": dyn_args, "pipeline": "institutional_investors"}
            if {'美元／新台幣', '日期'}.issubset(cols_set): return {"parser": "csv_dynamic_header", "args": dyn_args, "pipeline": "fx_rates"}
            if {'買賣權成交量比率', '日期'}.issubset(cols_set): return {"parser": "csv_dynamic_header", "args": dyn_args, "pipeline": "pcr"}
            if {'交易日期', '契約', '收盤價'}.issubset(cols_set) and '成交時間' not in cols_set: return {"parser": "csv_dynamic_header", "args": dyn_args, "pipeline": "daily_ohlc"}
            if {'成交日期', '商品代號', '成交價格'}.issubset(cols_set) and '成交時間' in cols_set: return {"parser": "csv_dynamic_header", "args": dyn_args, "pipeline": "tick_data"}
    except Exception as e_dyn: logger.debug(f"動態CSV判斷 ({descriptor}) 失敗: {e_dyn}"); pass

    if header_line_raw and first_data_line_raw: # Manual Cols CSVs
        h_parts = len(header_line_raw.split(',')); d_parts = len(first_data_line_raw.split(','))
        if d_parts > h_parts and h_parts > 3:
            key = None
            if '漲跌%' in header_line_raw or ('履約價' in header_line_raw and len(h_parts) > 15): key = 'options_daily_v2'
            elif '履約價' in header_line_raw: key = 'options_daily_v1'
            if key: return {"parser": "csv_manual_cols", "args": {**base_args, "names_key": key, "header": None, "skiprows": 1}, "pipeline": "daily_ohlc"}

    # Hotfix: 加入對無表頭 futures_daily 的判斷
    if header_line_raw:
        potential_data_cols_count = len(header_line_raw.split(','))
        if potential_data_cols_count == len(MANUAL_COLUMN_NAMES['futures_daily']):
            first_field = header_line_raw.split(',')[0].strip()
            if re.match(r"^\d{8}$", first_field) or re.match(r"^\d{4}/\d{2}/\d{2}$", first_field):
                logger.info(f"檔案 {descriptor} 符合 futures_daily (無表頭) 特徵，嘗試使用 csv_manual_cols。")
                manual_csv_args = {**base_args, "names_key": 'futures_daily', "header": None, "skiprows": 0}
                return {"parser": "csv_manual_cols", "args": manual_csv_args, "pipeline": "daily_ohlc"}

    logger.warning(f"未能為 {descriptor} 確定解析配方。預覽: {sample_lines[:1]}")
    return {"parser": "unknown", "args": {"encoding": detected_encoding}, "pipeline": "unknown"}

def parse_with_recipe(content_bytes: bytes, recipe: Dict[str, Any], descriptor: str) -> Optional[pd.DataFrame]:
    parser_type, args = recipe.get("parser"), recipe.get("args", {}).copy()
    if parser_type in ["unknown", "unknown_encoding"]: logger.warning(f"跳過 {descriptor} (配方: {parser_type})"); return None
    stream = io.BytesIO(content_bytes)
    try:
        if parser_type == "excel_ods": return pd.read_excel(stream, engine='odf', header=None, dtype=str)
        if parser_type == "fwf": return pd.read_fwf(stream, **args)
        if parser_type in ["csv", "csv_dynamic_header"]: return pd.read_csv(stream, **args)
        if parser_type == "csv_manual_cols":
            names = MANUAL_COLUMN_NAMES[args.pop("names_key")]
            return pd.read_csv(stream, names=names, usecols=range(len(names)), **args)
    except Exception as e: logger.error(f"解析 {descriptor} (配方 {parser_type}) 失敗: {e}"); return None
    logger.error(f"未知解析器 '{parser_type}' ({descriptor})"); return None

def _clean_and_prepare_df(df: pd.DataFrame, required_cols: List[str], rename_map: Dict[str, str]) -> pd.DataFrame:
    df.columns = [str(col).strip().lower().replace(' ', '_').replace('(', '').replace(')', '').replace('/', '_').replace('%', 'percent').replace('+', 'plus') for col in df.columns]
    df = df.rename(columns=lambda c: rename_map.get(c, c))
    for col in required_cols:
        if col not in df.columns: df[col] = None
    return df

def _clean_and_prepare_row(row_dict: Dict[str, Any], required_cols: List[str], rename_map: Dict[str, str], descriptor: str) -> Dict[str, Any]:
    """
    清理單個原始數據行字典的鍵名，並根據提供的映射重命名鍵。
    同時確保所有在 `required_cols` 中指定的必要欄位都存在於結果字典中，
    如果不存在，則以 None 值填充。此函數是 `_clean_and_prepare_df` 的單行版本。

    Args:
        row_dict: 從 CSV 或 FWF 解析器得到的原始數據行字典。
        required_cols: 一個字串列表，包含結果字典中必須存在的欄位名。
        rename_map: 一個字典，用於將清理後的原始鍵名映射到最終的目標欄位名。
        descriptor: 檔案描述符，用於可能的日誌記錄（目前未使用）。

    Returns:
        一個新的字典，其中鍵名已清理和重命名，且包含了所有必要欄位。
    """
    # 清理欄位名 (在字典的鍵上操作)
    cleaned_row = {}
    for key, value in row_dict.items():
        new_key = str(key).strip().lower().replace(' ', '_').replace('(', '').replace(')', '').replace('/', '_').replace('%', 'percent').replace('+', 'plus')
        cleaned_row[new_key] = value

    # 重命名欄位
    renamed_row = {}
    for key, value in cleaned_row.items():
        renamed_row[rename_map.get(key, key)] = value

    # 確保必要欄位存在
    for col in required_cols:
        if col not in renamed_row:
            renamed_row[col] = None
    return renamed_row

NUMERIC_COLUMNS_DAILY_OHLC = [
    'open', 'high', 'low', 'close', 'volume', 'settlement_price',
    'open_interest', 'strike_price', 'change', 'change_percent',
    'last_best_bid_price', 'last_best_ask_price',
    'historical_high', 'historical_low', 'spread_volume'
]

def process_daily_ohlc_row(row: Dict[str, Any], source_descriptor: str) -> Optional[Dict[str, Any]]:
    """
    處理單行 `daily_ohlc` (每日行情) 類型的數據。
    此函數假設輸入的 `row` 字典已經過初步的欄位名清理和重命名 (例如由 `_clean_and_prepare_row` 處理)。
    它負責特定於 `daily_ohlc` 管線的數據轉換和驗證邏輯，包括：
    - 將 'trading_date' 欄位轉換為 'YYYY-MM-DD' 格式的字串。
    - 將 'option_type' (買賣權) 從中文（買權/賣權）或字母（C/P）映射到標準的 'C' 或 'P'。
    - 標準化 'trading_session' (交易時段) 的值，並提供預設值 'Regular'。
    - 將定義在 `NUMERIC_COLUMNS_DAILY_OHLC` 中的多個數值型欄位從字串轉換為浮點數，
      處理過程中會移除千分位逗號和代表NA的'-'。轉換失敗的欄位值會被設為 None。
    - 添加 'source' 欄位，值為傳入的 `source_descriptor`。
    - 驗證核心欄位 ('trading_date', 'product_id', 'close') 是否都存在且不為 None，
      如果任一核心欄位缺失，則返回 None (相當於舊流程中的 `dropna`)。

    Args:
        row: 一個字典，代表待處理的單行 `daily_ohlc` 數據。
        source_descriptor: 原始檔案的描述符，用於添加到 'source' 欄位。

    Returns:
        一個字典，包含處理和轉換後的 `daily_ohlc` 行數據；如果行數據因核心欄位缺失
        而被視為無效，則返回 `None`。
    """
    try:
        # 日期轉換
        raw_date = row.get('trading_date')
        if raw_date:
            try:
                # Pandas to_datetime 支援多種格式，這裡需要一個更穩健的日期解析或假設特定格式
                # 假設日期是 YYYY/MM/DD 或 YYYY-MM-DD 或 YYYYMMDD
                # 為了簡化，我們先用一個基本轉換，實際可能需要更複雜的解析
                dt_obj = pd.to_datetime(str(raw_date), errors='coerce')
                row['trading_date'] = dt_obj.strftime('%Y-%m-%d') if pd.notna(dt_obj) else None
            except Exception as e_date:
                # logger.debug(f"[{source_descriptor}] trading_date '{raw_date}' 轉換失敗: {e_date}")
                row['trading_date'] = None
        else:
            row['trading_date'] = None

        # 買賣權轉換
        if 'option_type' in row and row['option_type'] is not None:
            row['option_type'] = str(row['option_type']).strip().upper() # 先轉大寫比較保險
            row['option_type'] = {'買權':'C', '賣權':'P', 'C':'C', 'P':'P'}.get(row['option_type'])

        # 交易時段轉換
        session = row.get('trading_session')
        if session is not None:
            session_str = str(session).strip()
            row['trading_session'] = {'盤後':'AfterHours', '一般':'Regular', '0':'Regular', '1':'AfterHours'}.get(session_str, 'Regular') # 默認為 Regular
        else:
            row['trading_session'] = 'Regular' # 預設值

        # 數值欄位轉換
        for col in NUMERIC_COLUMNS_DAILY_OHLC:
            if col in row and row[col] is not None:
                val_str = str(row[col]).replace(',', '').replace('-', '') # 移除千分位和負號（如果是代表NA的'-'）
                if not val_str: # 如果處理後是空字串
                    row[col] = None
                else:
                    try:
                        row[col] = float(val_str) # 嘗試轉為 float
                    except ValueError:
                        # logger.debug(f"[{source_descriptor}] 欄位 {col} 值 '{row[col]}' 無法轉換為 float。")
                        row[col] = None # 轉換失敗則設為 None
            # else: # 如果欄位不存在或值為None，則保持原樣或確保為None
                # if col in row and row[col] is None: pass # 已經是 None
                # elif col not in row: row[col] = None # 欄位不存在則設為 None

        row['source'] = source_descriptor

        # 檢查核心欄位是否存在 (dropna 邏輯)
        if not all(row.get(key) is not None for key in ['trading_date', 'product_id', 'close']):
            # logger.debug(f"[{source_descriptor}] 行因缺少 trading_date, product_id 或 close 而被跳過: { {k: row.get(k) for k in ['trading_date', 'product_id', 'close']} }")
            return None

        return row
    except Exception as e:
        logger.error(f"[{source_descriptor}] 處理 daily_ohlc 行時出錯: {e}, 行數據: {row}")
        return None

def pipeline_daily_ohlc(df: pd.DataFrame, source: str) -> pd.DataFrame:
    map_ = {'交易日期':'trading_date','契約':'product_id','商品代號':'product_id','到期月份_週別':'expiry_month','到期月份／週別':'expiry_month','履約價':'strike_price','買賣權':'option_type','開盤價':'open','最高價':'high','最低價':'low','收盤價':'close','成交量':'volume','結算價':'settlement_price','未沖銷契約數':'open_interest','交易時段':'trading_session','漲跌價':'change','漲跌percent':'change_percent'}
    df = _clean_and_prepare_df(df, ['trading_date','product_id','close'], map_)

    # --- 以下邏輯將被 process_daily_ohlc_row 取代或改寫 ---
    # df['trading_date'] = pd.to_datetime(df['trading_date'], errors='coerce').dt.strftime('%Y-%m-%d')
    # if 'option_type' in df.columns: df['option_type'] = df['option_type'].astype(str).str.strip().map({'買權':'C','賣權':'P','C':'C','P':'P'})
    # df['trading_session'] = df.get('trading_session', pd.Series(index=df.index, dtype='str')).fillna('Regular').astype(str).str.strip().replace({'盤後':'AfterHours','一般':'Regular','0':'Regular','1':'AfterHours'})

    # 擴展數值欄位列表，確保所有潛在的數值欄位都被包含和正確處理
    # 原始列表：['open','high','low','close','volume','settlement_price','open_interest','strike_price','change','change_percent']
    # 'last_best_bid_price', 'last_best_ask_price', 'historical_high', 'historical_low' 也可能是數值
    # 'spread_volume' 也是一個可能的數值欄位
    num_cols = [
        'open', 'high', 'low', 'close', 'volume', 'settlement_price',
        'open_interest', 'strike_price', 'change', 'change_percent',
        'last_best_bid_price', 'last_best_ask_price',
        'historical_high', 'historical_low', 'spread_volume'
    ]

    for col in num_cols:
        if col in df.columns:
            # 先將原始值中的 '-' 替換為空字串或 NaN，pd.to_numeric 可以處理空字串為 NaN
            # 並移除千分位符號 ','
            # astype(str) 確保在替換前是字串類型，避免對非字串類型如純數字或布林值進行 .str 操作
            df[col] = df[col].astype(str).str.replace(',', '', regex=False).str.replace('-', '', regex=False)
            df[col] = pd.to_numeric(df[col], errors='coerce')
        # else:
            # 如果欄位不存在，可以選擇性地創建它並賦值 NaN，但 _clean_and_prepare_df 可能已處理
            # df[col] = pd.NA # 或者 np.nan

    df['source'] = source
    # 保持原有的 dropna 邏輯，確保核心識別欄位存在
    return df.dropna(subset=['trading_date','product_id','close'])

NUMERIC_COLUMNS_TICK_DATA = ['strike_price', 'price', 'volume']

def _clean_raw_tick_row(raw_row_dict: Dict[str, Any]) -> Dict[str, Any]:
    """
    對從 CSV/FWF 解析器得到的原始 `tick_data` 行字典的鍵進行初步清理。
    將鍵轉換為小寫，替換空格為底線，並移除括號等特殊字符，
    這模擬了舊版 `_clean_and_prepare_df` 函數中對 DataFrame 欄位名的通用清理部分。

    Args:
        raw_row_dict: 包含原始鍵值對的單行數據字典。

    Returns:
        一個新的字典，其鍵名已經過初步清理。
    """
    cleaned_row = {}
    for key, value in raw_row_dict.items():
        new_key = str(key).strip().lower().replace(' ', '_').replace('(', '').replace(')', '').replace('/', '_').replace('%', 'percent').replace('+', 'plus')
        cleaned_row[new_key] = value
    return cleaned_row

def process_tick_data_row(raw_row_dict: Dict[str, Any], source_descriptor: str) -> Optional[Dict[str, Any]]:
    """
    處理單行 `tick_data` (逐筆交易) 類型的數據。

    此函數負責將原始的、經過初步鍵名清理的 `tick_data` 行字典，轉換為結構化的、
    符合資料庫模式的字典。主要步驟包括：
    1. 應用一個特定的欄位名映射 (`map_`)，將清理後的原始鍵名（例如 '成交日期', '商品代號'）
       轉換為最終的目標欄位名（例如 'trade_date', 'product_id'）。
    2. 合併 'trade_date' 和 'trade_time' 欄位，並將結果格式化為 'YYYY-MM-DD HH:MM:SS.ffffff'
       格式的 'trade_datetime' 字串。會處理時間字串不足6位的情況（補零）。
    3. 將 'option_type' (買賣權) 從中文或字母映射到標準的 'C' 或 'P'。
    4. 如果 'volume' 欄位不存在但 'volume_with_side' (如 "10(B+S)") 存在，
       則從後者中提取數值部分作為 'volume'。
    5. 將定義在 `NUMERIC_COLUMNS_TICK_DATA` 中的數值型欄位 ('strike_price', 'price', 'volume')
       從字串轉換為相應的數值類型（通常是浮點數，成交量為整數）。
       處理過程中會移除千分位逗號。轉換失敗的欄位值會被設為 None。
    6. 添加 'source' 欄位。
    7. 驗證核心欄位 ('trade_datetime', 'product_id', 'price', 'volume') 是否都存在且不為 None，
       若任一核心欄位缺失，則返回 None。

    Args:
        raw_row_dict: 從 CSV 或 FWF 解析器得到的原始數據行字典。
                      注意：此函數期望 `raw_row_dict` 的鍵已經過 `_clean_raw_tick_row` 的初步清理。
        source_descriptor: 原始檔案的描述符，用於添加到 'source' 欄位。

    Returns:
        一個字典，包含處理和轉換後的 `tick_data` 行數據；如果行數據因核心欄位缺失
        而被視為無效，則返回 `None`。
    """
    row = _clean_raw_tick_row(raw_row_dict) # 先對原始字典的 key 做一次通用清理

    # 欄位重命名 (來自 pipeline_tick_data 的 map_)
    map_ = {
        '成交日期': 'trade_date', # 原始 CSV 表頭 -> 清理後 key
        '商品代號': 'product_id',
        '到期月份週別': 'expiry_month', # 清理後 key: 到期月份週別
        '履約價': 'strike_price',
        '買賣權': 'option_type',
        '成交時間': 'trade_time',
        '成交價格': 'price',
        '成交數量_買賣別_': 'volume_with_side',
        '成交數量_b_or_s_': 'volume_with_side', # 另一種可能的原始表頭清理結果
        '成交數量bpluss': 'volume', # 清理後 key: 成交數量bpluss (B+S)
        '成交數量': 'volume' # 原始表頭 "成交數量"
    }
    # 應用重命名，注意這裡的 key 是 _clean_raw_tick_row 清理後的 key
    # 例如，原始 "成交日期" -> 清理後 "成交日期" (假設中文不變) -> 映射到 "trade_date"
    # 原始 "到期月份(週別)" -> 清理後 "到期月份週別" -> 映射到 "expiry_month"

    # 執行重命名邏輯 (需要小心處理，因為鍵可能已經是目標名稱了)
    # 一個簡單的方法是創建一個新字典，只挑選和重命名我們關心的欄位
    processed_row = {}
    # 首先處理直接映射的欄位
    for original_csv_header, target_key in map_.items():
        cleaned_original_header = str(original_csv_header).strip().lower().replace(' ', '_').replace('(', '').replace(')', '').replace('/', '_').replace('%', 'percent').replace('+', 'plus')
        if cleaned_original_header in row:
            processed_row[target_key] = row[cleaned_original_header]

    # 確保所有 map_ 中的 target_key 都有機會從 row 中獲取 (如果 key 在 row 中但不在 map_ 的 source 中)
    for key_in_row in row:
        if key_in_row not in processed_row: # 如果還沒被 map_ 處理過
             # 如果 key_in_row 本身就是一個目標 key，或者不需要重命名，則直接複製
            if key_in_row in map_.values() or key_in_row in ['product_id', 'expiry_month', 'strike_price', 'option_type', 'price', 'volume']: # 以及其他潛在的目標欄位
                 processed_row[key_in_row] = row[key_in_row]


    # 日期時間合併與轉換
    trade_date_val = processed_row.get('trade_date')
    trade_time_val = processed_row.get('trade_time')

    current_trade_datetime = None # 先設為 None

    if trade_date_val and trade_time_val: # 確保兩者都有值且不為空字串
        trade_date_str = str(trade_date_val).strip()
        trade_time_str = str(trade_time_val).strip()

        if len(trade_time_str) == 5:
            trade_time_str = "0" + trade_time_str

        if len(trade_time_str) == 6: # 只處理長度為6的時間 (HHMMSS)
            combined_dt_str = f"{trade_date_str} {trade_time_str}"
            try:
                dt_obj = pd.to_datetime(combined_dt_str, errors='coerce')
                if pd.notna(dt_obj):
                    current_trade_datetime = dt_obj.strftime('%Y-%m-%d %H:%M:%S.%f')
            except Exception:
                pass # current_trade_datetime 保持 None

    processed_row['trade_datetime'] = current_trade_datetime

    # Option Type 轉換
    opt_type_val = processed_row.get('option_type') # 從 processed_row 獲取，這裡的值是原始CSV中的值，例如 "買權"
    if opt_type_val is not None:
        opt_type_str_cleaned = str(opt_type_val).strip() # 不再使用 .upper()，因為我們的 map key 是中文
        option_type_mapping = {'買權': 'C', '賣權': 'P', 'C': 'C', 'P': 'P'} # map 的 key 用實際的詞
        processed_row['option_type'] = option_type_mapping.get(opt_type_str_cleaned)
    # 如果 opt_type_val 是 None，或者不在 mapping 中，processed_row['option_type'] 會是 None (如果之前是 None) 或被設為 None

    # Volume 提取 (如果存在 volume_with_side)
    if 'volume' not in processed_row and processed_row.get('volume_with_side'):
        vol_match = re.search(r'(\d+)', str(processed_row['volume_with_side']))
        if vol_match:
            processed_row['volume'] = vol_match.group(1)
        else:
            processed_row['volume'] = None # 或者保持 volume_with_side 的值？通常是需要數值 volume

    # 數值欄位轉換
    for col in NUMERIC_COLUMNS_TICK_DATA:
        if col in processed_row and processed_row[col] is not None:
            val_str = str(processed_row[col]).replace(',', '').replace('-', '')
            if not val_str:
                processed_row[col] = None
            else:
                try:
                    # price 和 strike_price 可能是 float, volume 應該是 int
                    if col == 'volume':
                        processed_row[col] = int(float(val_str)) # 先轉 float 再轉 int 以處理 "1.0" 這種情況
                    else:
                        processed_row[col] = float(val_str)
                except ValueError:
                    processed_row[col] = None
        # else: # 欄位不存在或為 None
            # if col in processed_row and processed_row[col] is None: pass
            # elif col not in processed_row: processed_row[col] = None


    processed_row['source'] = source_descriptor

    # 核心欄位檢查 (dropna)
    # 'trade_datetime','product_id','price','volume'
    if not all(processed_row.get(key) is not None for key in ['trade_datetime', 'product_id', 'price', 'volume']):
        # logger.debug(f"[{source_descriptor}] Tick data 行因核心欄位缺失而被跳過: { {k: processed_row.get(k) for k in ['trade_datetime', 'product_id', 'price', 'volume']} }")
        return None

    # 確保返回的字典只包含最終需要的欄位，並且順序一致（如果需要固定 schema）
    # 這裡可以定義一個 final_tick_columns 列表
    # final_tick_columns = ['trade_datetime', 'product_id', 'expiry_month', 'strike_price', 'option_type', 'price', 'volume', 'source']
    # return {k: processed_row.get(k) for k in final_tick_columns}
    # 暫時返回所有已處理的欄位，讓 pyarrow.Table.from_pylist 推斷 schema
    return processed_row

# 對於 institutional_investors 的數值欄位列表
NUMERIC_COLUMNS_INST_INVEST = [
    'long_pos_vol', 'long_pos_val_twd_k', 'short_pos_vol', 'short_pos_val_twd_k',
    'net_pos_vol', 'net_pos_val_twd_k', 'long_oi_vol', 'long_oi_val_twd_k',
    'short_oi_vol', 'short_oi_val_twd_k', 'net_oi_vol', 'net_oi_val_twd_k'
]

# institutional_investors 的欄位映射，從清理後的 DataFrame 欄位名到最終 Arrow 表欄位名
# 注意：這裡的源頭是 _clean_and_prepare_df 處理 DataFrame 後的欄位名
# 在單行處理中，我們需要模擬這個過程或直接使用原始 CSV 表頭（清理後）到最終名的映射
INST_INV_COL_MAP_FROM_CLEANED_DF = {
    '多方交易口數':'long_pos_vol',
    '多方交易契約金額_千元_':'long_pos_val_twd_k',
    '空方交易口數':'short_pos_vol',
    '空方交易契約金額_千元_':'short_pos_val_twd_k',
    '多空交易淨口數':'net_pos_vol',
    '多空交易淨額_千元_':'net_pos_val_twd_k',
    '未平倉多方口數':'long_oi_vol',
    '未平倉多方契約金額_千元_':'long_oi_val_twd_k',
    '未平倉空方口數':'short_oi_vol',
    '未平倉空方契約金額_千元_':'short_oi_val_twd_k',
    '未平倉淨口數':'net_oi_vol',
    '未平倉淨額_千元_':'net_oi_val_twd_k'
}


def process_institutional_investors_row(raw_row_dict: Dict[str, Any], source_descriptor: str) -> Optional[Dict[str, Any]]:
    """
    處理單行 `institutional_investors` (三大法人) 類型的數據。

    此函數將原始的 CSV 行字典轉換為結構化的字典，用於後續生成 Arrow Table。主要步驟包括：
    1. 初步清理原始字典的鍵名（小寫化、去特殊字符等）。
    2. 應用特定的欄位重命名，例如將 '身份別' 轉為 'investor_type'，'商品名稱' 轉為 'product_name'，
       以及將 '日期' 或 '交易日' 統一轉為 'data_date'。
    3. 將 'data_date' 欄位轉換為 'YYYY-MM-DD' 格式的字串。
    4. 根據是否存在 '買賣權' 欄位及其內容（'買權'/'賣權'），判斷 'instrument_type' (商品類型)
       是 'Option' 還是 'Future'，並相應設定 'option_type' ('C'/'P' 或 None)。
    5. 處理大量的數值欄位（定義於 `INST_INV_COL_MAP_FROM_CLEANED_DF` 和 `NUMERIC_COLUMNS_INST_INVEST`）：
       - 這些欄位通常代表交易口數、契約金額、未平倉口數和契約金額等。
       - 從原始字串值中移除千分位逗号。
       - 將清理後的字串轉換為整數。如果原始值為 None、空字串或轉換失敗，則該欄位值設為 0。
       - 確保所有預期的數值欄位都存在於結果字典中，若不存在則以 0 填充。
    6. 添加 'source' 欄位。
    7. 驗證核心欄位 ('data_date', 'product_name', 'investor_type') 是否都存在且不為 None，
       若任一核心欄位缺失，則返回 None。

    Args:
        raw_row_dict: 從 CSV 解析器得到的原始數據行字典。
        source_descriptor: 原始檔案的描述符，用於添加到 'source' 欄位。

    Returns:
        一個字典，包含處理和轉換後的 `institutional_investors` 行數據；
        如果行數據因核心欄位缺失而被視為無效，則返回 `None`。
    """
    # 步驟1: 清理原始 CSV 行的鍵名 (模仿 _clean_and_prepare_df 的 general clean)
    # 原始 pipeline_institutional_investors 使用了 _clean_and_prepare_df，其 map_ 如下：
    # map_ = {'身份別':'investor_type','商品名稱':'product_name'}
    # required_cols = ['investor_type','product_name']

    temp_cleaned_row = {}
    for k, v in raw_row_dict.items():
        new_key = str(k).strip().lower().replace(' ', '_').replace('(', '').replace(')', '').replace('/', '_').replace('%', 'percent').replace('+', 'plus')
        temp_cleaned_row[new_key] = v

    # 步驟2: 應用特定重命名 (身份別 -> investor_type, 商品名稱 -> product_name)
    row = {}
    specific_rename_map = {'身份別':'investor_type', '商品名稱':'product_name', '日期':'data_date', '交易日':'data_date'} # '日期' 和 '交易日' 都映射到 data_date

    for key, value in temp_cleaned_row.items():
        # 處理特定重命名，例如 "身份別" -> "investor_type"
        # 注意：temp_cleaned_row 的鍵已經是清理過的，例如 "身份別" 保持 "身份別" （如果中文不變）
        # 或者 "商品名稱" 保持 "商品名稱"
        # 我們需要檢查原始的 CSV 表頭（未清理的）是否在 specific_rename_map 中
        # 這裡的邏輯比較 tricky，因為 _clean_and_prepare_df 是先清理所有列名，再重命名
        # 為了簡化，我們假設 temp_cleaned_row 中的鍵可以直接用於 INST_INV_COL_MAP_FROM_CLEANED_DF
        # 而 specific_rename_map 的鍵是針對清理後的鍵名

        # 修正：specific_rename_map 的鍵應該是清理後的鍵名
        cleaned_specific_keys_map = {
            str(k).strip().lower().replace(' ', '_').replace('(', '').replace(')', '').replace('/', '_').replace('%', 'percent').replace('+', 'plus'): v
            for k,v in specific_rename_map.items()
        }

        final_key = cleaned_specific_keys_map.get(key, key) # 如果在特定重命名中，使用它的值，否則用原key
        row[final_key] = value

    # 確保核心的重命名欄位存在
    if 'investor_type' not in row: row['investor_type'] = temp_cleaned_row.get('身份別') # 從原始清理名嘗試獲取
    if 'product_name' not in row: row['product_name'] = temp_cleaned_row.get('商品名稱')
    if 'data_date' not in row: row['data_date'] = temp_cleaned_row.get('日期', temp_cleaned_row.get('交易日'))


    # 步驟3: data_date 轉換
    raw_date = row.get('data_date')
    if raw_date:
        try:
            dt_obj = pd.to_datetime(str(raw_date), errors='coerce')
            row['data_date'] = dt_obj.strftime('%Y-%m-%d') if pd.notna(dt_obj) else None
        except Exception:
            row['data_date'] = None
    else:
        row['data_date'] = None

    # 步驟4: instrument_type 和 option_type 判斷
    # 原始邏輯: df['instrument_type'] = df.apply(lambda r: 'Option' if '買賣權' in r and pd.notna(r['買賣權']) and str(r['買賣權']).strip() in ['買權','賣權'] else 'Future', axis=1)
    # df['option_type'] = df.get('買賣權', pd.Series(index=df.index)).astype(str).str.strip().map({'買權':'C','賣權':'P'})

    # '買賣權' 欄位在 temp_cleaned_row 中可能是 '買賣權' (如果CSV表頭是這樣且清理後不變)
    raw_option_type_val = temp_cleaned_row.get('買賣權') # 使用清理但未重命名前的鍵

    if raw_option_type_val and str(raw_option_type_val).strip() in ['買權', '賣權']:
        row['instrument_type'] = 'Option'
        row['option_type'] = {'買權':'C', '賣權':'P'}.get(str(raw_option_type_val).strip())
    else:
        row['instrument_type'] = 'Future'
        row['option_type'] = None # 期貨沒有買賣權類型

    # 步驟5: 數值欄位轉換 (使用 INST_INV_COL_MAP_FROM_CLEANED_DF)
    # 這裡的鍵是DataFrame清理後的鍵名，值是最終的Arrow表欄位名
    for df_col_name, final_arrow_col_name in INST_INV_COL_MAP_FROM_CLEANED_DF.items():
        # 我們需要從 row 中找到對應 df_col_name 的值
        # row 的鍵目前是 final_arrow_col_name 或清理後的原始名
        # 假設 temp_cleaned_row 中的鍵是我們需要的 df_col_name (在它被 specific_rename 之前)
        raw_val = temp_cleaned_row.get(df_col_name) # 例如，temp_cleaned_row['多方交易口數']

        if raw_val is not None:
            val_str = str(raw_val).replace(',', '')
            if not val_str: # 空字串
                row[final_arrow_col_name] = 0 # 數值欄位NA通常填0
            else:
                try:
                    row[final_arrow_col_name] = int(float(val_str)) # 先轉 float 避免 "1.0" 之類，再轉 int
                except ValueError:
                    # logger.debug(f"[{source_descriptor}] II欄位 {final_arrow_col_name} 值 '{raw_val}' 轉換為 int 失敗。")
                    row[final_arrow_col_name] = 0 # 轉換失敗填0
        else:
            row[final_arrow_col_name] = 0 # 不存在或為None則填0

    # 確保所有 NUMERIC_COLUMNS_INST_INVEST 都存在於 row 中，如果不存在則補0
    for num_col in NUMERIC_COLUMNS_INST_INVEST:
        if num_col not in row:
            row[num_col] = 0

    row['source'] = source_descriptor

    # 步驟6: 核心欄位檢查 (dropna)
    if not all(row.get(key) is not None for key in ['data_date', 'product_name', 'investor_type']):
        # logger.debug(f"[{source_descriptor}] II行因核心欄位缺失而被跳過: { {k: row.get(k) for k in ['data_date', 'product_name', 'investor_type']} }")
        return None

    return row


def pipeline_tick_data(df: pd.DataFrame, source: str) -> pd.DataFrame:
    logger.debug(f"[{source}] Entering pipeline_tick_data. RAW df.head():\n{df.head().to_string()}")
    logger.debug(f"[{source}] RAW df.columns: {df.columns.tolist()}")
    logger.debug(f"[{source}] RAW df.dtypes:\n{df.dtypes}")

    # Hotfix applied for "成交數量(B+S)"
    map_ = {
        '成交日期': 'trade_date',
        '商品代號': 'product_id',
        '到期月份週別': 'expiry_month',  # 修正鍵名以匹配 _clean_and_prepare_df 的輸出
        '履約價': 'strike_price',
        '買賣權': 'option_type',
        '成交時間': 'trade_time',
        '成交價格': 'price',
        '成交數量_買賣別_': 'volume_with_side', # 清理後會變 '成交數量_買賣別_'
        '成交數量_b_or_s_': 'volume_with_side', # 清理後會變 '成交數量_b_or_s_'
        '成交數量bpluss': 'volume',      # 修正鍵名以匹配 _clean_and_prepare_df 的輸出 for (B+S)
        '成交數量': 'volume'
    }
    # '到期月份_週別' 經過 _clean_and_prepare_df 會變成 '到期月份_週別_' (如果 lower() 不影響中文)
    # 但日誌顯示是 '到期月份週別'，這意味著 '(' 和 ')' 之間的 '週別' 也被處理了，或者 lower() 的影響
    # _clean_and_prepare_df: str(col).strip().lower().replace(' ', '_').replace('(', '').replace(')', '')
    # '到期月份(週別)' -> '到期月份週別' (小寫化 '週別' 如果適用)
    # 測試樣本的列名是 '到期月份(週別)'
    # 經過 _clean_and_prepare_df 清理:
    # str('到期月份(週別)').strip().lower().replace('(', '').replace(')', '')
    # '到期月份(週別)'.lower() -> '到期月份(週別)' (假設中文不變)
    # '到期月份(週別)'.replace('(', '') -> '到期月份週別)'
    # '到期月份週別)'.replace(')', '') -> '到期月份週別'
    # 所以 map_ 中的鍵 '到期月份週別' 是正確的。

    df = _clean_and_prepare_df(df, ['trade_date','trade_time','price','volume', 'product_id', 'expiry_month', 'strike_price', 'option_type'], map_)

    logger.debug(f"[{source}] df.head() AFTER _clean_and_prepare_df:\n{df.head().to_string()}")
    logger.debug(f"[{source}] df.columns AFTER _clean_and_prepare_df: {df.columns.tolist()}")
    logger.debug(f"[{source}] df.dtypes AFTER _clean_and_prepare_df:\n{df.dtypes}")

    if 'trade_date' in df.columns and 'trade_time' in df.columns:
        logger.debug(f"[{source}] df[['trade_date', 'trade_time']].head() BEFORE datetime conversion:\n{df[['trade_date', 'trade_time']].head().to_string()}")
    else:
        logger.warning(f"[{source}] 'trade_date' or 'trade_time' missing AFTER _clean_and_prepare_df.")

    # Check if essential date/time columns exist and are not all NaN before attempting conversion
    if 'trade_date' in df.columns and 'trade_time' in df.columns:
        # Ensure columns are not all None/NaN which would make them object type or float if mixed
        is_trade_date_valid = not df['trade_date'].isnull().all()
        is_trade_time_valid = not df['trade_time'].isnull().all()

        if not is_trade_date_valid or not is_trade_time_valid:
            logger.warning(f"[{source}] 'trade_date' or 'trade_time' column is all NaN or missing before datetime conversion. Setting 'trade_datetime' to NaT.")
            df['trade_datetime'] = pd.NaT
        else:
            # Attempt conversion
            df['trade_date'] = df['trade_date'].astype(str).str.strip() # Add strip here
            df['trade_date'] = df['trade_date'].astype(str).str.strip()
            df['trade_time'] = df['trade_time'].astype(str).str.strip()
            combined_datetime_str = df['trade_date'] + ' ' + df['trade_time']
            # Let Pandas infer format, as it seems more robust for this case.
            datetime_series = pd.to_datetime(combined_datetime_str, errors='coerce')

            # Attempt with microseconds if primary fails (though sample data doesn't have microseconds)
            # This logic might be overly complex if data is consistently without microseconds
            # For now, let's assume the primary format is usually without microseconds for tick time.
            # If specific files have microseconds, the recipe determination should ideally handle it.
            # dt_format_ms = '%Y%m%d %H:%M:%S.%f'
            # datetime_series_ms = pd.to_datetime(combined_datetime_str, format=dt_format_ms, errors='coerce')

            # Use the series that has more valid dates (less NaTs)
            # if datetime_series_ms.count() > datetime_series.count():
            #    datetime_series = datetime_series_ms

            df['trade_datetime'] = datetime_series

            # Apply strftime only if there are non-NaT dates
            if df['trade_datetime'].notnull().any():
                try:
                    df['trade_datetime_str'] = df['trade_datetime'].dt.strftime('%Y-%m-%d %H:%M:%S.%f')
                     # Replace original trade_datetime with its string representation if successful
                    df['trade_datetime'] = df['trade_datetime_str']
                    df.drop(columns=['trade_datetime_str'], inplace=True, errors='ignore') # clean up temp column
                except AttributeError as e: # Handles cases like all NaT which might not have .dt accessor as expected
                    logger.warning(f"[{source}] Could not apply strftime to 'trade_datetime', possibly all NaT or mixed types not allowing .dt: {e}")
                    # Ensure 'trade_datetime' is object type and NaTs are pd.NaT for dropna
                    df['trade_datetime'] = df['trade_datetime'].astype('object').replace(pd.NaT, None) # dropna handles None
            else: # All values were NaT after to_datetime
                df['trade_datetime'] = pd.NaT # Ensure it's pd.NaT
    else:
        logger.warning(f"[{source}] 'trade_date' or 'trade_time' not in DataFrame columns. 'trade_datetime' will be NaT.")
        df['trade_datetime'] = pd.NaT

    logger.debug(f"[{source}] df['trade_datetime'].head() after conversion and strftime attempt:\n{df['trade_datetime'].head()}")

    if 'option_type' in df.columns: df['option_type'] = df['option_type'].astype(str).str.strip().map({'C':'C','P':'P','買':'C','賣':'P'})
    if 'volume' not in df.columns and 'volume_with_side' in df.columns:
        # 確保提取的 volume 是字串，以便後續清理
        df['volume'] = df['volume_with_side'].astype(str).str.extract(r'(\d+)').iloc[:,0].astype(str)
        # 如果提取結果是 <NA> (pandas StringArray NA), 轉換為 np.nan 字串以便 to_numeric 正確處理為 NaN
        # pd.to_numeric('nan', errors='coerce') -> np.nan
        # pd.to_numeric('<NA>', errors='coerce') -> np.nan (Pandas 1.0+ 應該可以正確處理)
        # 為保險起見，明確處理可能的 pandas NA 字串表示
        df.loc[df['volume'].str.lower() == '<na>', 'volume'] = 'nan'


    num_cols = ['strike_price','price','volume']
    for col in num_cols:
        if col in df.columns:
            logger.debug(f"[{source}] Processing numeric column: {col}")
            original_series_head = df[col].head().to_list()
            df[col] = df[col].astype(str).str.replace(',', '', regex=False).str.replace('-', '', regex=False)
            cleaned_series_head = df[col].head().to_list()
            df[col] = pd.to_numeric(df[col], errors='coerce')
            coerced_series_head = df[col].head().to_list()
            logger.debug(f"[{source}] Col {col} | Original: {original_series_head} | Cleaned: {cleaned_series_head} | Coerced: {coerced_series_head}")
        else:
            logger.warning(f"[{source}] Numeric column {col} not found in DataFrame.")

    logger.debug(f"[{source}] df.head() after numeric conversion:\n{df.head().to_string()}")
    logger.debug(f"[{source}] df.dtypes after numeric conversion:\n{df.dtypes}")

    df_before_dropna = df.copy()
    # Ensure df_processed is a copy to avoid SettingWithCopyWarning
    df_processed = df.dropna(subset=['trade_datetime','product_id','price','volume']).copy()

    if len(df_processed) == 0 and len(df_before_dropna) > 0:
        logger.warning(f"[{source}] All rows were dropped by dropna. Price or Volume likely all NaN after coercion.")
        logger.warning(f"[{source}] Price column before dropna (first 5):\n{df_before_dropna['price'].head().to_string()}")
        logger.warning(f"[{source}] Volume column before dropna (first 5):\n{df_before_dropna['volume'].head().to_string()}")
        logger.warning(f"[{source}] Product ID column before dropna (first 5):\n{df_before_dropna['product_id'].head().to_string()}")
        logger.warning(f"[{source}] Trade Datetime column before dropna (first 5):\n{df_before_dropna['trade_datetime'].head().to_string()}")

    if not df_processed.empty: # Only add source if df_processed is not empty
        df_processed['source'] = source # Now this should be safe

    return df_processed

def pipeline_institutional_investors(df: pd.DataFrame, source: str) -> pd.DataFrame:
    map_ = {'身份別':'investor_type','商品名稱':'product_name'}
    df = _clean_and_prepare_df(df, ['investor_type','product_name'], map_)
    date_col = next((c for c in df.columns if c in ['日期','交易日','data_date']), None)
    if not date_col: logger.error(f"II: 日期欄位未找到 ({source})"); return pd.DataFrame()
    df = df.rename(columns={date_col:'data_date'})
    df['data_date'] = pd.to_datetime(df['data_date'], errors='coerce').dt.strftime('%Y-%m-%d')
    df['instrument_type'] = df.apply(lambda r: 'Option' if '買賣權' in r and pd.notna(r['買賣權']) and str(r['買賣權']).strip() in ['買權','賣權'] else 'Future', axis=1)
    df['option_type'] = df.get('買賣權', pd.Series(index=df.index)).astype(str).str.strip().map({'買權':'C','賣權':'P'})
    col_map = {'多方交易口數':'long_pos_vol','多方交易契約金額_千元_':'long_pos_val_twd_k','空方交易口數':'short_pos_vol','空方交易契約金額_千元_':'short_pos_val_twd_k','多空交易淨口數':'net_pos_vol','多空交易淨額_千元_':'net_pos_val_twd_k','未平倉多方口數':'long_oi_vol','未平倉多方契約金額_千元_':'long_oi_val_twd_k','未平倉空方口數':'short_oi_vol','未平倉空方契約金額_千元_':'short_oi_val_twd_k','未平倉淨口數':'net_oi_vol','未平倉淨額_千元_':'net_oi_val_twd_k'}
    df_clean = df.rename(columns=lambda c: col_map.get(c,c)) # Use df_clean for consistency

    all_numeric_cols = list(col_map.values()) # Iterate over target English names from col_map
    for col in all_numeric_cols:
        if col not in df_clean.columns:
            df_clean[col] = 0

        df_clean[col] = pd.to_numeric(
            df_clean[col].astype(str).str.replace(',', ''),
            errors='coerce'
        ).fillna(0).astype(int)

    df_clean['source'] = source; return df_clean.dropna(subset=['data_date','product_name','investor_type'])

NUMERIC_COLUMNS_FX_RATES = ['usd_twd', 'cny_twd', 'eur_usd', 'usd_jpy', 'gbp_usd', 'aud_usd', 'usd_hkd', 'usd_cny', 'usd_zar', 'nzd_usd']

def process_fx_rates_row(raw_row_dict: Dict[str, Any], source_descriptor: str) -> Optional[Dict[str, Any]]:
    """
    處理單行 `fx_rates` (外匯匯率) 類型的數據。

    此函數將原始的 CSV 行字典轉換為結構化的字典。主要步驟包括：
    1. 初步清理原始字典的鍵名。
    2. 應用一個欄位名映射 (`fx_rename_map_from_cleaned`)，將清理後的原始鍵名
       (例如 '美元_新台幣') 轉換為最終的目標欄位名 (例如 'usd_twd')。
    3. 將 'data_date' 欄位轉換為 'YYYY-MM-DD' 格式的字串。
    4. 將定義在 `NUMERIC_COLUMNS_FX_RATES` 中的多個匯率數值型欄位從字串轉換為浮點數。
       處理過程中可能會移除逗號（儘管匯率通常不含）。轉換失敗或原始值為None則設為 None。
       確保所有預期的數值欄位都存在於結果字典中，若不存在則以 None 填充。
    5. 添加 'source' 欄位。
    6. 驗證核心欄位 'data_date' 是否存在且不為 None，若缺失則返回 None。

    Args:
        raw_row_dict: 從 CSV 解析器得到的原始數據行字典。
        source_descriptor: 原始檔案的描述符，用於添加到 'source' 欄位。

    Returns:
        一個字典，包含處理和轉換後的 `fx_rates` 行數據；如果行數據因 'data_date' 缺失
        而被視為無效，則返回 `None`。
    """
    # map_ = {'日期':'data_date','美元／新台幣':'usd_twd', ...}
    # required_cols = ['data_date','usd_twd']

    temp_cleaned_row = {}
    for k, v in raw_row_dict.items():
        new_key = str(k).strip().lower().replace(' ', '_').replace('(', '').replace(')', '').replace('/', '_').replace('%', 'percent').replace('+', 'plus')
        temp_cleaned_row[new_key] = v

    row = {}
    # 簡化重命名：假設清理後的 key 如果在 NUMERIC_COLUMNS_FX_RATES 或 'data_date' 中，則直接使用
    # 或者，更精確地，使用一個從清理後的原始 CSV 表頭到目標欄位名的映射
    fx_rename_map_from_cleaned = {
        '日期': 'data_date', '美元_新台幣': 'usd_twd', '人民幣_新台幣': 'cny_twd',
        '歐元_美元': 'eur_usd', '美元_日圓': 'usd_jpy', '英鎊_美元': 'gbp_usd',
        '澳幣_美元': 'aud_usd', '美元_港幣': 'usd_hkd', '美元_人民幣': 'usd_cny',
        '美元_南非幣': 'usd_zar', '紐幣_美元': 'nzd_usd'
    }
    for cleaned_key, value in temp_cleaned_row.items():
        final_key = fx_rename_map_from_cleaned.get(cleaned_key, cleaned_key) # 如果不在map中，可能是一些未知欄位
        row[final_key] = value

    # data_date 轉換
    raw_date = row.get('data_date')
    if raw_date:
        try:
            dt_obj = pd.to_datetime(str(raw_date), errors='coerce')
            row['data_date'] = dt_obj.strftime('%Y-%m-%d') if pd.notna(dt_obj) else None
        except Exception: row['data_date'] = None
    else: row['data_date'] = None

    # 數值欄位轉換
    for col in NUMERIC_COLUMNS_FX_RATES:
        if col in row and row[col] is not None:
            val_str = str(row[col]).replace(',', '') # 通常匯率沒有千分位，但以防萬一
            if not val_str: row[col] = None
            else:
                try: row[col] = float(val_str)
                except ValueError: row[col] = None
        elif col not in row : # 確保所有數值欄位都存在
            row[col] = None


    row['source'] = source_descriptor
    if not row.get('data_date'): # 原本是 dropna(subset=['data_date','usd_twd'])，但 usd_twd 可能不存在
        return None
    return row

NUMERIC_COLUMNS_PCR = ['put_volume', 'call_volume', 'pcr_volume', 'put_oi', 'call_oi', 'pcr_oi']

def process_pcr_row(raw_row_dict: Dict[str, Any], source_descriptor: str) -> Optional[Dict[str, Any]]:
    """
    處理單行 `pcr` (Put/Call Ratio) 類型的數據。

    此函數將原始的 CSV 行字典轉換為結構化的字典。主要步驟包括：
    1. 初步清理原始字典的鍵名。
    2. 應用一個欄位名映射 (`pcr_rename_map_from_cleaned`)，將清理後的原始鍵名
       (例如 '買賣權成交量比率percent') 轉換為最終的目標欄位名 (例如 'pcr_volume')。
    3. 將 'data_date' 欄位轉換為 'YYYY-MM-DD' 格式的字串。
    4. 將定義在 `NUMERIC_COLUMNS_PCR` 中的多個數值型欄位（成交量、未平倉量、比率）
       從字串轉換為浮點數。處理過程中會移除 '%' 和千分位逗號。
       轉換失敗或原始值為None則設為 None。
       確保所有預期的數值欄位都存在於結果字典中，若不存在則以 None 填充。
    5. 添加 'source' 欄位。
    6. 驗證核心欄位 'data_date' 是否存在且不為 None，若缺失則返回 None。

    Args:
        raw_row_dict: 從 CSV 解析器得到的原始數據行字典。
        source_descriptor: 原始檔案的描述符，用於添加到 'source' 欄位。

    Returns:
        一個字典，包含處理和轉換後的 `pcr` 行數據；如果行數據因 'data_date' 缺失
        而被視為無效，則返回 `None`。
    """
    # map_ = {'日期':'data_date','賣權成交量':'put_volume', ...}
    # required_cols = ['data_date']

    temp_cleaned_row = {}
    for k, v in raw_row_dict.items():
        new_key = str(k).strip().lower().replace(' ', '_').replace('(', '').replace(')', '').replace('/', '_').replace('%', 'percent').replace('+', 'plus')
        temp_cleaned_row[new_key] = v

    row = {}
    pcr_rename_map_from_cleaned = {
        '日期': 'data_date', '賣權成交量': 'put_volume', '買權成交量': 'call_volume',
        '買賣權成交量比率percent': 'pcr_volume', '賣權未平倉量': 'put_oi',
        '買權未平倉量': 'call_oi', '買賣權未平倉量比率percent': 'pcr_oi'
    }
    for cleaned_key, value in temp_cleaned_row.items():
        final_key = pcr_rename_map_from_cleaned.get(cleaned_key, cleaned_key)
        row[final_key] = value

    raw_date = row.get('data_date')
    if raw_date:
        try:
            dt_obj = pd.to_datetime(str(raw_date), errors='coerce')
            row['data_date'] = dt_obj.strftime('%Y-%m-%d') if pd.notna(dt_obj) else None
        except Exception: row['data_date'] = None
    else: row['data_date'] = None

    for col in NUMERIC_COLUMNS_PCR:
        if col in row and row[col] is not None:
            val_str = str(row[col]).replace('%','').replace(',','')
            if not val_str: row[col] = None
            else:
                try: row[col] = float(val_str) # PCR 比率可能是 float
                except ValueError: row[col] = None
        elif col not in row: # 確保所有數值欄位都存在
             row[col] = None


    row['source'] = source_descriptor
    if not row.get('data_date'):
        return None
    return row

# 更新 PIPELINE_MAP 以便舊的 DataFrame 流程可以選擇性地使用新的行處理器 (如果需要)
# 但主要目標是讓 parse_content_to_arrow 直接調用新的行處理器
PIPELINE_ROW_PROCESSORS = {
    "daily_ohlc": process_daily_ohlc_row,
    "institutional_investors": process_institutional_investors_row,
    "tick_data": process_tick_data_row,
    "fx_rates": process_fx_rates_row,
    "pcr": process_pcr_row,
}

# 重新定義 pipeline_fx_rates 和 pipeline_pcr 以供舊的 PIPELINE_MAP 使用
# 這些函數現在將使用它們對應的單行處理器
def pipeline_fx_rates(df: pd.DataFrame, source: str) -> pd.DataFrame:
    """舊的 DataFrame 版本，內部調用行處理器。"""
    processed_rows = []
    for _, raw_row_series in df.iterrows():
        raw_row_dict = raw_row_series.to_dict()
        # 注意：process_fx_rates_row 期望的 raw_row_dict 是來自 CSV 解析器的原始字典，
        # 其鍵名可能與 DataFrame 的欄位名不同（DataFrame 的欄位名可能已經被 _clean_and_prepare_df 清理過）。
        # 為了安全，我們假設傳入的 df 的欄位名是原始的或與 process_fx_rates_row 期望的一致。
        # 如果 df 的欄位名已經是清理過的，那 process_fx_rates_row 內部的初步清理可能需要調整。
        # 這裡我們假設 df 的欄位是 process_fx_rates_row 可以處理的。
        processed_row = process_fx_rates_row(raw_row_dict, source)
        if processed_row:
            processed_rows.append(processed_row)
    if not processed_rows:
        return pd.DataFrame() # 返回空的 DataFrame
    return pd.DataFrame(processed_rows)

def pipeline_pcr(df: pd.DataFrame, source: str) -> pd.DataFrame:
    """舊的 DataFrame 版本，內部調用行處理器。"""
    processed_rows = []
    for _, raw_row_series in df.iterrows():
        raw_row_dict = raw_row_series.to_dict()
        processed_row = process_pcr_row(raw_row_dict, source)
        if processed_row:
            processed_rows.append(processed_row)
    if not processed_rows:
        return pd.DataFrame()
    return pd.DataFrame(processed_rows)

PIPELINE_MAP = {"daily_ohlc":pipeline_daily_ohlc,"institutional_investors":pipeline_institutional_investors,"tick_data":pipeline_tick_data,"fx_rates":pipeline_fx_rates,"pcr":pipeline_pcr,"unknown":lambda df,src: df}

def worker_process_file(args: Tuple[str, bytes, Dict, str, Any]) -> Dict[str, Any]:
    # 臨時加入，用於測試錯誤傳遞機制
    # raise Exception("Simulated worker error to test exit code")
    desc, bytes_data, recipe, staging_path, hw_mgr = args
    try:
        # 測試強化錯誤傳遞機制的第二種情況：讓一個 worker process 返回錯誤狀態
        # if "futures_daily_sample.csv" in desc: # 特定針對一個檔案使其失敗
        #     logger.warning(f"開發者注入錯誤：模擬 {desc} 處理失敗以測試主程序錯誤出口。")
        #     return {'status':'error','descriptor':desc,'error_msg':"模擬的 worker 錯誤"}

        df = parse_with_recipe(bytes_data, recipe, desc)

        # Reverted: Removed the detailed TDD logging block from worker_process_file as the issue was in pipeline_tick_data
        # Original TDD logging was:
        # # --- More detailed DEBUG LOGGING for TDD ---
        # logger.debug(f"[{desc}] Value of df after parse_with_recipe: type={type(df)}")
        # if df is None:
        #     logger.warning(f"[{desc}] parse_with_recipe returned None.")
        # else:
        #     try:
        #         is_empty = df.empty
        #         logger.debug(f"[{desc}] df.empty property: {is_empty}")
        #         if not is_empty:
        #             logger.debug(f"[{desc}] DataFrame AFTER parse_with_recipe. Columns: {df.columns.tolist()}")
        #             logger.debug(f"[{desc}] DataFrame AFTER parse_with_recipe. Head:\n{df.head().to_string()}")
        #             logger.debug(f"[{desc}] DataFrame AFTER parse_with_recipe. dtypes:\n{df.dtypes}")
        #         else:
        #             logger.warning(f"[{desc}] parse_with_recipe returned an empty DataFrame (df.empty is True). Columns: {df.columns.tolist()}")
        #     except Exception as e_debug:
        #         logger.error(f"[{desc}] Error accessing df properties after parse_with_recipe: {e_debug}")
        # # --- END DEBUG LOGGING ---

        if df is None or df.empty:
            # logger.warning(f"[{desc}] Condition (df is None or df.empty) is TRUE. Returning skipped_parse_empty.") # Keep this log for clarity
            return {'status':'skipped_parse_empty','descriptor':desc}

        p_name = recipe.get("pipeline","unknown"); p_func = PIPELINE_MAP.get(p_name)
        if not p_func: return {'status':'skipped_no_pipeline','descriptor':desc}
        df = p_func(df, desc)
        if df is None or df.empty: return {'status':'skipped_clean_empty','descriptor':desc}
        f_hash = hashlib.sha256(desc.encode()).hexdigest()[:16]
        out_path = os.path.join(staging_path, f"{p_name}_{f_hash}.parquet")
        df.to_parquet(out_path, index=False); return {'status':'success','rows':len(df),'pipeline':p_name,'file':out_path,'descriptor':desc}
    except Exception as e: logger.error(f"Worker error ({desc}): {e}"); return {'status':'error','descriptor':desc,'error_msg':str(e)}
    finally:
        if hw_mgr: hw_mgr.log_event_snapshot(f"Processed: {desc[:30]}") # hw_mgr will be part of context or passed differently

# def run_parsing_stage(...) -> ... : This function will be replaced by async logic within main

# def run_duckdb_loading_stage(...) -> int : This function's logic will be integrated into process_file_content or similar async handler

async def parse_content_to_arrow(content_bytes: bytes, recipe: Dict[str, Any], descriptor: str, batch_size: int = 10000) -> Optional[pyarrow.Table]:
    """
    懶加載解析內容，轉換為 Arrow Table。
    取代 parse_with_recipe 和部分 pipeline_* 函數的功能。
    注意：此函數目前為示意，具體的逐行解析和 Arrow 轉換邏輯需要詳細實現。
    """
    parser_type = recipe.get("parser")
    args = recipe.get("args", {}).copy()
    pipeline_name = recipe.get("pipeline", "unknown")

    if parser_type in ["unknown", "unknown_encoding"]:
        logger.warning(f"跳過 {descriptor} (配方: {parser_type})")
        return None

    # TODO: 實際的懶加載解析邏輯
    # 示例：假設我們有一個 async def generate_rows(content_bytes, parser_type, args) -> AsyncGenerator[Dict[str, Any], None]:
    # async for row_dict in generate_rows(content_bytes, parser_type, args):
    #     # 逐行處理 row_dict
    #     # 應用 pipeline_* 中的轉換邏輯 (型別轉換、欄位清理)
    #     # 收集到批次
    #     # 轉換批次為 Arrow Table
    #     pass
    logger.info(f"[{descriptor}] 示意：應在此處解析內容並轉換為 Arrow Table (解析器: {parser_type}, 管線: {pipeline_name})")

    # **** 串流解析與 Arrow 轉換核心邏輯 ****

    data_rows = [] # 用於收集一個批次的行數據
    schema = None # Arrow Schema 將在處理第一批數據時推斷或預定義

    # 獲取 pipeline 函數，它將被用於處理單行數據
    # 注意：這需要對現有的 pipeline_* 函數進行調整，
    # 使其能夠接受單個字典並返回處理後的字典，或者接受一個小批次的字典列表。
    # 為了簡化，我們先假設 pipeline_func 可以處理一個字典列表，並返回一個字典列表。
    # 或者，更理想的是，pipeline_func 處理單行，然後我們在這裡批處理。
    # 我們將首先嘗試讓 pipeline_func 處理 DataFrame，然後從 DataFrame 獲取行。
    # 這仍然不是最優的，但比整個檔案一次性處理要好。

    # 真正的串流處理需要重寫 pipeline_* 函數。
    # 暫時，我們將在這裡模擬逐行處理，但仍然依賴於舊的 pipeline 函數處理批次。

    # 1. 懶加載解析原始數據行 (這裡仍使用 Pandas DataFrame 作為中介，待優化)
    #    理想情況下，這裡應該有一個 generate_raw_rows 的非同步生成器
    temp_df = None
    if parser_type == "excel_ods": # ODS 保持原樣，因為它不是主要的串流目標
        temp_df = pd.read_excel(io.BytesIO(content_bytes), engine='odf', header=None, dtype=str)
    elif parser_type == "fwf":
        temp_df = pd.read_fwf(io.BytesIO(content_bytes), **args)
    elif parser_type in ["csv", "csv_dynamic_header"]:
        temp_df = pd.read_csv(io.BytesIO(content_bytes), **args)
    elif parser_type == "csv_manual_cols":
        names_key = args.pop("names_key", None)
        if not names_key:
            logger.error(f"[{descriptor}] csv_manual_cols 解析器缺少 names_key。")
            return None
        names = MANUAL_COLUMN_NAMES[names_key]
        temp_df = pd.read_csv(io.BytesIO(content_bytes), names=names, usecols=range(len(names)), **args)
    else:
        logger.error(f"[{descriptor}] 未知的解析器類型: {parser_type}")
        return None

    if temp_df is None or temp_df.empty:
        logger.warning(f"[{descriptor}] 串流解析步驟未能從內容生成初始 DataFrame。")
        return None

    # 2. 應用管線轉換 (目前仍對整個臨時 DataFrame 操作)
    #    理想情況下，pipeline_func 應作用於單行或小批次行。
    pipeline_func = PIPELINE_MAP.get(pipeline_name)
    if not pipeline_func:
        logger.warning(f"[{descriptor}] 找不到管線函數 {pipeline_name}。")
        # 如果沒有特定的管線函數，我們可能需要決定是直接從 temp_df 轉換，還是報錯。
        # 假設此時 temp_df 就是我們要轉換的數據。
        df_processed = temp_df
    else:
        try:
            df_processed = pipeline_func(temp_df.copy(), descriptor) # 傳遞副本以防意外修改
        except Exception as e:
            logger.error(f"[{descriptor}] 在管線 {pipeline_name} 中處理 DataFrame 時出錯: {e}")
            return None

    if df_processed is None or df_processed.empty:
        logger.warning(f"[{descriptor}] 管線 {pipeline_name} 未返回 DataFrame 或為空。")
        return None

    # 3. 從處理後的 DataFrame 轉換為 Arrow Table
    #    這是最終的 Arrow 轉換步驟。'id' 欄位應在此之前被移除或不存在。
    try:
        if 'id' in df_processed.columns:
            df_processed = df_processed.drop(columns=['id'])

        # 確保欄位名字符合 Arrow 的要求（例如，沒有奇怪的字符）
        # Pandas to Arrow 通常會處理好這個，但可以加上額外的清理步驟
        # df_processed.columns = [pyarrow.compat.canonicalize_name(c) for c in df_processed.columns]

        arrow_table = pyarrow.Table.from_pandas(df_processed, preserve_index=False)
        logger.success(f"[{descriptor}] 成功將處理後的 DataFrame 轉換為 Arrow Table, 行數: {len(arrow_table)}, 欄位: {arrow_table.schema.names}")
        return arrow_table
    except Exception as e:
        logger.error(f"[{descriptor}] DataFrame 到 Arrow Table 轉換失敗: {e}")
        return None
import csv # 確保導入

async def async_generate_rows_from_csv(content_bytes: bytes, recipe_args: Dict[str, Any], descriptor: str, manual_names: Optional[List[str]] = None) -> AsyncGenerator[Dict[str, Any], None]:
    """
    非同步逐行解析 CSV 內容。
    - content_bytes: CSV 檔案的位元組內容。
    - recipe_args: 解析參數，如 encoding, header (行號), skipinitialspace。
    - descriptor: 檔案描述符，用於日誌。
    - manual_names: 如果是手動指定欄位名 (如 csv_manual_cols)，則提供此列表。
    """
    encoding = recipe_args.get("encoding", "utf-8") # 從 recipe_args 獲取編碼
    skip_initial_space = recipe_args.get("skipinitialspace", True)

    try:
        # 將 bytes 解碼為 text stream
        # io.TextIOWrapper 可以在 BytesIO 上提供解碼和換行符處理
        text_stream = io.TextIOWrapper(io.BytesIO(content_bytes), encoding=encoding, newline='')

        header_row_index = recipe_args.get("header", 0) if not manual_names else None # csv_manual_cols 通常 header=None, skiprows=1
        skip_rows = recipe_args.get("skiprows", 0) # for csv_manual_cols

        if manual_names:
            # 處理 manual_cols 的情況
            # 跳過指定的行數
            for _ in range(skip_rows):
                try:
                    next(text_stream)
                except StopIteration:
                    logger.warning(f"[{descriptor}] 在跳過 manual_cols 的行時檔案提前結束。")
                    return

            reader = csv.reader(text_stream, skipinitialspace=skip_initial_space)
            for row_values in reader:
                if len(row_values) < len(manual_names):
                    # logger.debug(f"[{descriptor}] 行的欄位數 ({len(row_values)}) 少於預期 ({len(manual_names)})，將用 None 填充。行: {row_values[:5]}")
                    row_values.extend([None] * (len(manual_names) - len(row_values)))
                elif len(row_values) > len(manual_names):
                    # logger.debug(f"[{descriptor}] 行的欄位數 ({len(row_values)}) 多於預期 ({len(manual_names)})，將截斷。行: {row_values[:5]}")
                    row_values = row_values[:len(manual_names)]
                yield dict(zip(manual_names, row_values))
        else:
            # 處理 dynamic_header 的情況
            # 首先，跳到表頭行
            current_line_num = 0
            for _ in range(header_row_index):
                try:
                    next(text_stream)
                    current_line_num +=1
                except StopIteration:
                    logger.warning(f"[{descriptor}] 在尋找表頭時檔案提前結束 (目標行: {header_row_index})。")
                    return

            # 使用 csv.DictReader
            # DictReader 會將第一行（在跳過 header_row_index 之後）作為欄位名
            # 我們需要確保欄位名被正確清理
            # csv.DictReader 的 fieldnames 參數可以預先指定，但如果為 None，它會使用第一行。

            # 為了獲取與 Pandas read_csv(header=...) 一致的行為，
            # 我們讀取表頭行，然後將剩餘的流傳遞給 DictReader。
            # 或者，更簡單地，我們可以讀取所有行，然後手動處理。

            # 簡易方法：使用 csv.reader 讀取表頭，然後再讀取數據行
            temp_reader_for_header = csv.reader(text_stream, skipinitialspace=skip_initial_space)
            try:
                header_list = next(temp_reader_for_header)
                current_line_num +=1
            except StopIteration:
                logger.warning(f"[{descriptor}] 無法讀取表頭行 (在行 {header_row_index} 之後)。")
                return

            # 清理表頭名 (模仿 Pandas 的行為，或至少是 _clean_and_prepare_df 的行為)
            # cleaned_header = [str(col).strip().lower().replace(' ', '_').replace('(', '').replace(')', '').replace('/', '_').replace('%', 'percent').replace('+', 'plus') for col in header_list]
            # 暫時使用原始表頭，清理步驟在後續的管線轉換中進行
            cleaned_header = [str(col).strip() for col in header_list]


            for row_values in temp_reader_for_header: # 繼續從同一個 reader 讀取
                if not any(field and field.strip() for field in row_values): # 跳過完全空行
                    continue
                if len(row_values) < len(cleaned_header):
                    row_values.extend([None] * (len(cleaned_header) - len(row_values)))
                elif len(row_values) > len(cleaned_header):
                     row_values = row_values[:len(cleaned_header)]
                yield dict(zip(cleaned_header, row_values))

    except UnicodeDecodeError as e_decode:
        logger.error(f"[{descriptor}] 使用編碼 '{encoding}' 解碼失敗: {e_decode}")
        # 可以嘗試備用編碼
    except csv.Error as e_csv:
        logger.error(f"[{descriptor}] CSV 解析錯誤: {e_csv}")
    except Exception as e:
        logger.error(f"[{descriptor}] 在 async_generate_rows_from_csv 中發生未知錯誤: {e}")

async def async_generate_rows_from_fwf(content_bytes: bytes, recipe_args: Dict[str, Any], descriptor: str) -> AsyncGenerator[Dict[str, Any], None]:
    """
    非同步逐行解析 FWF (固定寬度格式) 檔案內容。

    此函數假設 FWF 檔案中的欄位是由兩個或多個空格分隔的，
    這與 `determine_parsing_recipe` 函數中解析 FWF 表頭的方式一致。
    它會跳過指定的表頭行和分隔線，然後逐行讀取數據，
    並根據提供的欄位名列表將每行分割後的數據轉換為字典。

    Args:
        content_bytes: FWF 檔案的原始位元組內容。
        recipe_args: 解析配方參數字典，必須包含:
            'encoding': 檔案編碼 (預設 'utf-8')。
            'skiprows': 需要跳過的初始行數 (例如表頭和分隔線)。
            'names': 欄位名稱的列表。
        descriptor: 檔案的描述符，主要用於日誌記錄。

    Yields:
        一個字典，代表 FWF 檔案中的一行數據，其中鍵是欄位名，值是對應的數據字串。
        如果行中的欄位數與預期不符，會嘗試填充 None 或截斷。

    Raises:
        無直接拋出異常，但會在日誌中記錄解碼或解析錯誤。
    """
    encoding = recipe_args.get("encoding", "utf-8")
    skip_rows = recipe_args.get("skiprows", 0)
    names = recipe_args.get("names")

    if not names:
        logger.error(f"[{descriptor}] FWF 解析需要 'names' (欄位名列表) 在 recipe_args 中。")
        return

    try:
        text_stream = io.TextIOWrapper(io.BytesIO(content_bytes), encoding=encoding, newline='')

        # 跳過指定的行數 (表頭和分隔線)
        for i in range(skip_rows):
            try:
                line_content = next(text_stream)
                # logger.debug(f"[{descriptor}] FWF 跳過行 {i+1}: {line_content[:100]}") # Log first 100 chars
            except StopIteration:
                logger.warning(f"[{descriptor}] 在跳過 FWF 的行 ({skip_rows}行) 時檔案提前結束。")
                return

        # 逐行讀取數據
        for line_num, line in enumerate(text_stream):
            line = line.rstrip('\n\r') # 移除換行符
            if not line.strip(): # 跳過空行
                # logger.debug(f"[{descriptor}] FWF 跳過空數據行 {line_num + skip_rows + 1}")
                continue

            # 假設欄位是用至少兩個空格分隔的 (與表頭解析邏輯類似)
            row_values = re.split(r'\s{2,}', line.strip())

            if len(row_values) < len(names):
                # logger.debug(f"[{descriptor}] FWF 行 {line_num + skip_rows + 1} 欄位數 ({len(row_values)}) 少於預期 ({len(names)})。行: '{line[:70]}...' 值: {row_values}")
                row_values.extend([None] * (len(names) - len(row_values)))
            elif len(row_values) > len(names):
                # logger.debug(f"[{descriptor}] FWF 行 {line_num + skip_rows + 1} 欄位數 ({len(row_values)}) 多於預期 ({len(names)})，將截斷。行: '{line[:70]}...' 值: {row_values}")
                row_values = row_values[:len(names)]

            yield dict(zip(names, row_values))

    except UnicodeDecodeError as e_decode:
        logger.error(f"[{descriptor}] FWF 使用編碼 '{encoding}' 解碼失敗: {e_decode}")
    except Exception as e:
        logger.error(f"[{descriptor}] 在 async_generate_rows_from_fwf 中發生未知錯誤: {e}")

async def parse_content_to_arrow(content_bytes: bytes, recipe: Dict[str, Any], descriptor: str, batch_size: int = 10000) -> Optional[pyarrow.Table]:
    """
    根據解析配方 (recipe)，將原始檔案位元組內容 (content_bytes) 非同步轉換為 Apache Arrow 表 (pyarrow.Table)。
    此函數是資料處理流程中的核心轉換步驟。

    主要處理邏輯：
    1. 根據 `parser_type` (來自配方) 選擇合適的原始行數據生成策略：
        - 對於 CSV 格式 (如 'csv_dynamic_header', 'csv_manual_cols', 'csv'):
            調用 `async_generate_rows_from_csv` 逐行解析 CSV 內容。
        - 對於 FWF 格式 ('fwf'):
            調用 `async_generate_rows_from_fwf` 逐行解析固定寬度格式內容。
        - 對於 Excel ODS 格式 ('excel_ods'):
            由於 ODS 為二進制格式且串流解析複雜，暫時維持舊有方式，
            即使用 Pandas 一次性讀取整個檔案到 DataFrame。

    2. 應用特定管線的單行處理邏輯：
        - 如果檔案對應的 `pipeline_name` (來自配方) 存在於 `PIPELINE_ROW_PROCESSORS` 字典中
          (例如 'daily_ohlc', 'tick_data', 'institutional_investors', 'fx_rates', 'pcr')：
            a. 從 `PIPELINE_ROW_PROCESSORS` 獲取對應的單行處理函數 (如 `process_daily_ohlc_row`)。
            b. 對於從 CSV 或 FWF 生成器得到的每一原始數據行（字典），調用此單行處理函數。
               該函數負責數據清洗、類型轉換、業務邏輯驗證等。
            c. 收集所有成功處理後的行。
            d. 使用 `pyarrow.Table.from_pylist()` 直接從這些處理後的 Python 字典列表高效創建 Arrow Table。
               若 `from_pylist` 失敗，會嘗試通過 Pandas DataFrame 作為中介進行回退轉換，以便診斷。

    3. 對於沒有特定單行處理器的 CSV 管線，或未知的 CSV 管線：
        - 暫時回退到基於批次 Pandas DataFrame 的處理方式：
            a. 收集一定數量 (`batch_size`) 的原始行。
            b. 將這些原始行轉換為一個小的 Pandas DataFrame。
            c. 應用在 `PIPELINE_MAP` 中定義的舊有 DataFrame 整體處理函數。
            d. 收集所有處理後的 DataFrame 批次。
            e. 使用 `pd.concat()` 合併這些 DataFrame 批次。
            f. 最後從合併後的 DataFrame 轉換為 Arrow Table。
        - 這是一個過渡性方案，長遠目標是為所有管線實現基於單行處理的串流模式。

    Args:
        content_bytes: 原始檔案內容的位元組串。
        recipe: 解析配方字典，包含 'parser' (解析器類型), 'args' (解析參數),
                和 'pipeline' (管線名稱) 等關鍵信息。
        descriptor: 檔案的描述符，用於日誌記錄和追蹤。
        batch_size: 主要用於上述第 3 點中回退方案的批次大小控制。

    Returns:
        一個 `pyarrow.Table` 物件，包含轉換後的結構化數據；如果過程中發生嚴重錯誤
        或沒有有效數據可供轉換，則返回 `None`。
    """
    parser_type = recipe.get("parser")
    args = recipe.get("args", {}).copy() # .copy() 很重要，因為後續可能會修改 args (如 pop)
    pipeline_name = recipe.get("pipeline", "unknown")

    if parser_type in ["unknown", "unknown_encoding"]:
        logger.warning(f"跳過 {descriptor} (配方: {parser_type})")
        return None

    processed_rows_batch_dfs = [] # 更改: 收集處理後的 DataFrame 批次

    # 針對 CSV 類型的串流處理
    if parser_type in ["csv_dynamic_header", "csv_manual_cols", "csv"]:
        manual_names_key = args.pop("names_key", None) if parser_type == "csv_manual_cols" else None
        manual_names_list = MANUAL_COLUMN_NAMES.get(manual_names_key) if manual_names_key else None

        # ---- daily_ohlc 的特定串流處理路徑 ----
        if pipeline_name == "daily_ohlc":
            ohlc_map = {'交易日期':'trading_date','契約':'product_id','商品代號':'product_id','到期月份_週別':'expiry_month','到期月份／週別':'expiry_month','履約價':'strike_price','買賣權':'option_type','開盤價':'open','最高價':'high','最低價':'low','收盤價':'close','成交量':'volume','結算價':'settlement_price','未沖銷契約數':'open_interest','交易時段':'trading_session','漲跌價':'change','漲跌percent':'change_percent'}
            ohlc_req_cols = ['trading_date','product_id','close']

            processed_ohlc_rows = [] # Renamed to avoid conflict
            async for raw_row_dict in async_generate_rows_from_csv(content_bytes, args, descriptor, manual_names=manual_names_list):
                cleaned_row = _clean_and_prepare_row(raw_row_dict, ohlc_req_cols, ohlc_map, descriptor)
                processed_row = process_daily_ohlc_row(cleaned_row, descriptor)
                if processed_row:
                    processed_ohlc_rows.append(processed_row)

            if not processed_ohlc_rows:
                logger.warning(f"[{descriptor}] daily_ohlc 串流處理後沒有有效數據行。")
                return None
            try:
                arrow_table = pyarrow.Table.from_pylist(processed_ohlc_rows)
                logger.success(f"[{descriptor}] (daily_ohlc串流) 成功將處理後的行直接轉換為 Arrow Table, 行數: {len(arrow_table)}, 欄位: {arrow_table.schema.names}")
                return arrow_table
            except Exception as e:
                logger.error(f"[{descriptor}] (daily_ohlc串流) 從 pylist 到 Arrow Table 轉換失敗: {e}")
                try: # Fallback for daily_ohlc
                    fallback_df = pd.DataFrame(processed_ohlc_rows)
                    if 'id' in fallback_df.columns: fallback_df = fallback_df.drop(columns=['id'])
                    arrow_table = pyarrow.Table.from_pandas(fallback_df, preserve_index=False)
                    logger.info(f"[{descriptor}] (daily_ohlc串流-回退) Pandas 中介轉換成功。")
                    return arrow_table
                except Exception as e_fallback:
                    logger.error(f"[{descriptor}] (daily_ohlc串流-回退) Pandas 中介轉換也失敗: {e_fallback}")
                    return None

        # ---- tick_data 的特定串流處理路徑 ----
        elif pipeline_name == "tick_data":
            processed_tick_rows = []
            async for raw_row_dict in async_generate_rows_from_csv(content_bytes, args, descriptor, manual_names=manual_names_list):
                # process_tick_data_row 內部處理欄位名清理和特定重命名
                processed_row = process_tick_data_row(raw_row_dict, descriptor)
                if processed_row:
                    processed_tick_rows.append(processed_row)

            if not processed_tick_rows:
                logger.warning(f"[{descriptor}] tick_data 串流處理後沒有有效數據行。")
                return None
            try:
                # 為 tick_data 定義一個 schema 可能更穩健，因為其欄位相對固定
                # tick_data_schema = pyarrow.schema([
                #     ('trade_datetime', pyarrow.timestamp('us')), # 或 'ms'，取決於strftime的精度
                #     ('product_id', pyarrow.string()), ('expiry_month', pyarrow.string()),
                #     ('strike_price', pyarrow.float64()), ('option_type', pyarrow.string()),
                #     ('price', pyarrow.float64()), ('volume', pyarrow.int64()),
                #     ('source', pyarrow.string())
                # ])
                # 讓 pyarrow 推斷 schema
                arrow_table = pyarrow.Table.from_pylist(processed_tick_rows) # schema=tick_data_schema
                logger.success(f"[{descriptor}] (tick_data串流) 成功將處理後的行直接轉換為 Arrow Table, 行數: {len(arrow_table)}, 欄位: {arrow_table.schema.names}")
                return arrow_table
            except Exception as e:
                logger.error(f"[{descriptor}] (tick_data串流) 從 pylist 到 Arrow Table 轉換失敗: {e}")
                try: # Fallback for tick_data
                    fallback_df = pd.DataFrame(processed_tick_rows)
                    if 'id' in fallback_df.columns: fallback_df = fallback_df.drop(columns=['id'])
                    arrow_table = pyarrow.Table.from_pandas(fallback_df, preserve_index=False)
                    logger.info(f"[{descriptor}] (tick_data串流-回退) Pandas 中介轉換成功。")
                    return arrow_table
                except Exception as e_fallback:
                    logger.error(f"[{descriptor}] (tick_data串流-回退) Pandas 中介轉換也失敗: {e_fallback}")
                    return None

        # ---- institutional_investors 的特定串流處理路徑 ----
        elif pipeline_name == "institutional_investors":
            processed_inst_inv_rows = []
            async for raw_row_dict in async_generate_rows_from_csv(content_bytes, args, descriptor, manual_names=manual_names_list):
                processed_row = process_institutional_investors_row(raw_row_dict, descriptor)
                if processed_row:
                    processed_inst_inv_rows.append(processed_row)

            if not processed_inst_inv_rows:
                logger.warning(f"[{descriptor}] institutional_investors 串流處理後沒有有效數據行。")
                return None
            try:
                # institutional_investors 的 schema 包含多個 int64 和 string/date
                # 讓 pyarrow 推斷，或定義 schema
                arrow_table = pyarrow.Table.from_pylist(processed_inst_inv_rows)
                logger.success(f"[{descriptor}] (institutional_investors串流) 成功將處理後的行直接轉換為 Arrow Table, 行數: {len(arrow_table)}, 欄位: {arrow_table.schema.names}")
                return arrow_table
            except Exception as e:
                logger.error(f"[{descriptor}] (institutional_investors串流) 從 pylist 到 Arrow Table 轉換失敗: {e}")
                try: # Fallback
                    fallback_df = pd.DataFrame(processed_inst_inv_rows)
                    if 'id' in fallback_df.columns: fallback_df = fallback_df.drop(columns=['id'])
                    arrow_table = pyarrow.Table.from_pandas(fallback_df, preserve_index=False)
                    logger.info(f"[{descriptor}] (institutional_investors串流-回退) Pandas 中介轉換成功。")
                    return arrow_table
                except Exception as e_fallback:
                    logger.error(f"[{descriptor}] (institutional_investors串流-回退) Pandas 中介轉換也失敗: {e_fallback}")
                    return None

        # ---- 通用 CSV 串流處理 (使用 PIPELINE_ROW_PROCESSORS) ----
        elif pipeline_name in PIPELINE_ROW_PROCESSORS:
            row_processor = PIPELINE_ROW_PROCESSORS[pipeline_name]
            processed_rows = []
            async for raw_row_dict in async_generate_rows_from_csv(content_bytes, args, descriptor, manual_names=manual_names_list):
                # 注意：對於 fx_rates 和 pcr，它們的 process_<name>_row 函數內部已包含初步的鍵名清理。
                # 如果需要更通用的 _clean_and_prepare_row，則可以在此處調用。
                # 目前假設 process_<name>_row 函數能處理來自 async_generate_rows_from_csv 的原始字典。
                processed_row = row_processor(raw_row_dict, descriptor)
                if processed_row:
                    processed_rows.append(processed_row)

            if not processed_rows:
                logger.warning(f"[{descriptor}] {pipeline_name} 串流處理後沒有有效數據行。")
                return None
            try:
                arrow_table = pyarrow.Table.from_pylist(processed_rows)
                logger.success(f"[{descriptor}] ({pipeline_name}串流) 成功將處理後的行直接轉換為 Arrow Table, 行數: {len(arrow_table)}, 欄位: {arrow_table.schema.names}")
                return arrow_table
            except Exception as e:
                logger.error(f"[{descriptor}] ({pipeline_name}串流) 從 pylist 到 Arrow Table 轉換失敗: {e}")
                try: # Fallback
                    fallback_df = pd.DataFrame(processed_rows)
                    if 'id' in fallback_df.columns: fallback_df = fallback_df.drop(columns=['id'])
                    arrow_table = pyarrow.Table.from_pandas(fallback_df, preserve_index=False)
                    logger.info(f"[{descriptor}] ({pipeline_name}串流-回退) Pandas 中介轉換成功。")
                    return arrow_table
                except Exception as e_inner: # Inner except for fallback
                    logger.error(f"[{descriptor}] ({pipeline_name}串流-回退) Pandas 中介轉換也失敗: {e_inner}")
                    return None # Return None for the entire function if fallback also fails
        else:
            # ---- 對於其他未特殊處理的 CSV 類型（即不在 PIPELINE_ROW_PROCESSORS 中的，或 pipeline_name 未知），保留舊的批次 DataFrame 處理方式 ----
            logger.info(f"[{descriptor}] CSV管線 '{pipeline_name}' (未在串流處理器中明確定義或未知) 暫時使用基於批次DataFrame的處理。")
            temp_raw_rows_for_batch_other = []
            processed_rows_batch_dfs_other = []
            async for raw_row_dict in async_generate_rows_from_csv(content_bytes, args, descriptor, manual_names=manual_names_list):
                temp_raw_rows_for_batch_other.append(raw_row_dict)
                if len(temp_raw_rows_for_batch_other) >= batch_size:
                    if not temp_raw_rows_for_batch_other: continue
                    current_batch_df = pd.DataFrame(temp_raw_rows_for_batch_other)
                    temp_raw_rows_for_batch_other = []

                    pipeline_df_func = PIPELINE_MAP.get(pipeline_name) # 舊的DataFrame處理管線
                    df_processed_batch = None
                    if not pipeline_df_func: df_processed_batch = current_batch_df
                    else:
                        try: df_processed_batch = pipeline_df_func(current_batch_df.copy(), descriptor)
                        except Exception as e:
                            logger.error(f"[{descriptor}] DATAFRAME管線 {pipeline_name} 處理批次時出錯: {e}")
                            continue
                    if df_processed_batch is not None and not df_processed_batch.empty:
                        if 'id' in df_processed_batch.columns: df_processed_batch = df_processed_batch.drop(columns=['id'])
                        processed_rows_batch_dfs_other.append(df_processed_batch)

            if temp_raw_rows_for_batch_other:
                current_batch_df = pd.DataFrame(temp_raw_rows_for_batch_other)
                pipeline_df_func = PIPELINE_MAP.get(pipeline_name)
                df_processed_batch = None
                if not pipeline_df_func: df_processed_batch = current_batch_df
                else:
                    try: df_processed_batch = pipeline_df_func(current_batch_df.copy(), descriptor)
                    except Exception as e: logger.error(f"[{descriptor}] DATAFRAME管線 {pipeline_name} 處理最後批次時出錯: {e}")
                if df_processed_batch is not None and not df_processed_batch.empty:
                    if 'id' in df_processed_batch.columns: df_processed_batch = df_processed_batch.drop(columns=['id'])
                    processed_rows_batch_dfs_other.append(df_processed_batch)

            if not processed_rows_batch_dfs_other:
                logger.warning(f"[{descriptor}] 其他CSV類型串流處理後沒有可轉換為 Arrow 的數據 (管線: {pipeline_name})。")
                return None

            final_df_to_convert = pd.concat(processed_rows_batch_dfs_other, ignore_index=True)
            if final_df_to_convert.empty:
                logger.warning(f"[{descriptor}] 合併所有其他CSV批次後 DataFrame 為空 (管線: {pipeline_name})。")
                return None
            try:
                arrow_table = pyarrow.Table.from_pandas(final_df_to_convert, preserve_index=False)
                logger.success(f"[{descriptor}] (CSV串流半成品-{pipeline_name}) 成功將處理後的 DataFrame 轉換為 Arrow Table, 行數: {len(arrow_table)}, 欄位: {arrow_table.schema.names}")
                return arrow_table
            except Exception as e:
                logger.error(f"[{descriptor}] (CSV串流半成品-{pipeline_name}) DataFrame 到 Arrow Table 轉換失敗: {e}")
                return None

    elif parser_type == "fwf" or parser_type == "excel_ods":
        logger.info(f"[{descriptor}] Parser type {parser_type} 暫時使用舊的 Pandas 完整解析流程。")
        temp_df = None
        if parser_type == "excel_ods":
            temp_df = pd.read_excel(io.BytesIO(content_bytes), engine='odf', header=None, dtype=str)
        elif parser_type == "fwf":
            temp_df = pd.read_fwf(io.BytesIO(content_bytes), **args)

        if temp_df is None or temp_df.empty:
            logger.warning(f"[{descriptor}] {parser_type} 解析步驟未能從內容生成初始 DataFrame。")
            return None

        pipeline_func = PIPELINE_MAP.get(pipeline_name)
        df_processed = None
        if not pipeline_func: df_processed = temp_df
        else:
            try: df_processed = pipeline_func(temp_df.copy(), descriptor)
            except Exception as e:
                logger.error(f"[{descriptor}] 在管線 {pipeline_name} 中處理 {parser_type} DataFrame 時出錯: {e}")
                return None

        if df_processed is None or df_processed.empty:
            logger.warning(f"[{descriptor}] 管線 {pipeline_name} 未返回 {parser_type} DataFrame 或為空。")
            return None
        try:
            if 'id' in df_processed.columns: df_processed = df_processed.drop(columns=['id'])
            arrow_table = pyarrow.Table.from_pandas(df_processed, preserve_index=False)
            logger.success(f"[{descriptor}] ({parser_type}舊流程) 成功將處理後的 DataFrame 轉換為 Arrow Table, 行數: {len(arrow_table)}, 欄位: {arrow_table.schema.names}")
            return arrow_table
        except Exception as e:
            logger.error(f"[{descriptor}] ({parser_type}舊流程) DataFrame 到 Arrow Table 轉換失敗: {e}")
            return None
    else:
        logger.error(f"[{descriptor}] 未知的或尚未支持串流的解析器類型: {parser_type}")
        return None

async def process_file_content(
    descriptor: str,
    content_bytes: bytes,
    format_map: Dict[str, Any], # format_map 將作為共享狀態傳入
    db_conn: duckdb.DuckDBPyConnection, # DuckDB 連接也將傳入
    hw_mgr: HardwareManager # 暫時保留，用於日誌或資源限制
):
    """
    非同步處理單個檔案的完整流程。

    此函數協調以下步驟：
    1. 根據檔案內容的哈希值從 `format_map` (格式配方映射表) 中查找現有的解析配方。
    2. 如果找不到配方，則調用 `determine_parsing_recipe` (舊的同步函數，待評估是否可非同步化或替換)
       來嘗試學習新的配方，並更新共享的 `format_map`。
    3. 如果配方有效（即非未知類型），則調用 `parse_content_to_arrow` 將檔案內容轉換為 Arrow Table。
    4. 如果成功生成 Arrow Table 且該表不為空：
        a. 根據配方中的管線名稱確定目標 DuckDB 資料表。
        b. 檢查目標資料表是否已在 `TABLE_DEFINITIONS` 中定義。
        c. 將 Arrow Table 註冊為 DuckDB 中的一個臨時視圖。
        d. 動態構建 SQL INSERT 語句，將數據從臨時視圖插入到目標資料表。
           - 插入時會查詢目標表和 Arrow Table 的欄位，只插入兩者共有的欄位 (id 欄位由序列生成，不在此列)。
           - 根據 `UNIQUE_INDICES` 中的定義，為 INSERT 語句添加 `ON CONFLICT DO NOTHING` 子句，
             以處理潛在的唯一性衝突，確保數據不重複插入。
        e. 執行 SQL 插入操作。
        f. 清理（取消註冊）臨時視圖。
    5. 記錄處理結果，包括成功、跳過或錯誤狀態，以及處理的行數等信息。
    6. 如果處理過程中更新了 `format_map`，則返回標記以便主流程後續保存。

    Args:
        descriptor: 檔案的描述符，用於日誌和追蹤。
        content_bytes: 檔案的原始位元組內容。
        format_map: 共享的格式配方字典，用於讀取和更新解析配方。
        db_conn: 已初始化的 DuckDB 連接對象。
        hw_mgr: 硬體管理器實例，主要用於日誌記錄硬體快照。

    Returns:
        一個字典，包含處理結果的狀態信息，例如：
        `{'status': 'success', 'rows_in_arrow': ..., 'table': ..., 'descriptor': ..., 'map_updated': ...}`
        或錯誤/跳過狀態及相關信息。
    """
    logger.info(f"開始處理: {descriptor}")
    c_hash = hashlib.sha256(content_bytes).hexdigest()
    recipe = format_map.get(c_hash)
    map_updated_locally = False # 跟蹤此檔案是否導致 format_map 更新

    if not recipe:
        recipe = determine_parsing_recipe(content_bytes, descriptor) # 舊的同步函數
        if recipe:
            format_map[c_hash] = recipe # 更新共享的 format_map
            map_updated_locally = True
            logger.info(f"學習到新配方 for {descriptor[:50]} -> {recipe.get('pipeline')}")
        else:
            # 即使學習失敗，也記錄下來避免重複嘗試（可選策略）
            format_map[c_hash] = {"parser": "unknown", "pipeline": "unknown", "args": {}}
            map_updated_locally = True
            logger.warning(f"未能學習到配方 for {descriptor[:50]}")
            return {"status": "error_learning_recipe", "descriptor": descriptor, "map_updated": map_updated_locally}

    if not recipe or recipe.get("pipeline") == "unknown" or recipe.get("parser") in ["unknown", "unknown_encoding"]:
        logger.info(f"因未知/無效配方跳過 {descriptor[:50]}")
        return {"status": "skipped_invalid_recipe", "descriptor": descriptor, "map_updated": map_updated_locally}

    # 核心處理：解析內容到位元組串流 -> Arrow Table
    arrow_table = await parse_content_to_arrow(content_bytes, recipe, descriptor)

    if arrow_table is None or arrow_table.num_rows == 0:
        logger.warning(f"[{descriptor}] 未能從內容生成 Arrow Table 或 Table 為空。")
        return {"status": "skipped_no_arrow_data", "descriptor": descriptor, "map_updated": map_updated_locally}

    # 載入 Arrow Table 到 DuckDB
    table_name = recipe.get("pipeline", "unknown_pipeline")
    if table_name not in TABLE_DEFINITIONS:
        logger.warning(f"Arrow Table 的目標資料表 '{table_name}' 未在 TABLE_DEFINITIONS 中定義，跳過載入。")
        return {"status": "skipped_unknown_table", "descriptor": descriptor, "map_updated": map_updated_locally}

    try:
        # DuckDB 的 register/insert 操作是同步的，但在 asyncio 中執行它們
        # 可以通過 run_in_executor 避免阻塞事件迴圈，或者 DuckDB Python API 本身可能已優化。
        # DuckDB Python API 在 GIL 釋放方面做得很好，對於 CPU 密集型操作（如轉換和插入），
        # 直接調用通常是可接受的，除非有大量並行 I/O 綁定任務。

        # 創建一個臨時的唯一視圖名稱給這個 Arrow Table
        temp_view_name = f"arrow_view_{hashlib.sha256(descriptor.encode()).hexdigest()[:8]}"
        db_conn.register(temp_view_name, arrow_table)

        # 準備插入語句，處理唯一性約束
        idx_sql = UNIQUE_INDICES.get(table_name)
        uq_cols_str = ""
        if idx_sql:
            match = re.search(r'\((.*?)\)', idx_sql)
            uq_cols_str = match.group(1) if match else ""

        # 獲取目標表和 Arrow 表的欄位，只插入共有的欄位 (id 除外)
        target_cols_info = db_conn.execute(f"DESCRIBE {table_name};").fetchall()
        target_cols = {row[0] for row in target_cols_info if row[0] != 'id'}

        arrow_cols = set(arrow_table.schema.names)

        insert_cols_list = list(target_cols.intersection(arrow_cols))
        if not insert_cols_list:
            logger.warning(f"[{descriptor}] Arrow Table 與目標表 {table_name} 沒有共同的可插入欄位。")
            db_conn.unregister(temp_view_name)
            return {"status": "skipped_no_common_columns", "descriptor": descriptor, "map_updated": map_updated_locally}

        insert_cols_str = ", ".join(f'"{c}"' for c in insert_cols_list)
        select_cols_str = insert_cols_str # 假設 Arrow 表中的欄位名與資料庫中的一致

        # 构建插入语句
        # 我們需要處理 ON CONFLICT 的情況。DuckDB 支持 `INSERT INTO ... ON CONFLICT DO NOTHING`
        # `id` 欄位由序列生成。
        sql_insert = f"INSERT INTO {table_name} (id, {insert_cols_str}) SELECT nextval('seq_{table_name}'), {select_cols_str} FROM {temp_view_name}"

        if uq_cols_str: # 如果有唯一索引定義
            # 確保唯一索引的欄位都在我們要插入的欄位中
            conflict_target_cols = [c.strip() for c in uq_cols_str.split(',')]
            if all(c in insert_cols_list for c in conflict_target_cols):
                 sql_insert += f" ON CONFLICT ({uq_cols_str}) DO NOTHING"
            else:
                logger.warning(f"[{descriptor}] 唯一索引欄位 ({uq_cols_str}) 並不完全存在於插入欄位 ({insert_cols_str}) 中，將不使用 ON CONFLICT 子句。")
                # 也可以選擇在此情況下報錯或採取其他策略
        else: # 如果沒有唯一索引，但 id 是主鍵，可以基於 id 做衝突處理（雖然此處 id 是新生成的）
            # 實際上，由於 id 是新生成的，基於 id 的衝突不太可能發生，除非序列被重置或手動插入了 id
            # 為了安全，可以加上 ON CONFLICT (id) DO NOTHING，但意義不大
            pass # sql_insert += " ON CONFLICT (id) DO NOTHING;" # 通常 id 是 PK

        db_conn.execute(sql_insert)
        # num_inserted = db_conn.execute(f"SELECT count(*) FROM read_parquet('/dev/null') WHERE '{temp_view_name}' = '{temp_view_name}'").fetchone()[0] # REMOVED HACK

        logger.success(f"[{descriptor}] 成功將 Arrow Table ({arrow_table.num_rows} 行) 載入到 DuckDB 表 '{table_name}'。")
        db_conn.unregister(temp_view_name) # 清理註冊的視圖

        # 手動日誌記錄硬體快照，因為 worker_process_file 的 finally 子句移除了
        if hw_mgr: hw_mgr.log_event_snapshot(f"Processed_async: {descriptor[:30]}")

        return {"status": "success", "rows_in_arrow": arrow_table.num_rows, "table": table_name, "descriptor": descriptor, "map_updated": map_updated_locally}

    except Exception as e:
        logger.error(f"[{descriptor}] 處理/載入 Arrow Table 到 DuckDB 表 '{table_name}' 時失敗: {e}")
        # 嘗試清理已註冊的視圖
        try: db_conn.unregister(temp_view_name)
        except: pass
        return {"status": "error_loading_to_db", "descriptor": descriptor, "error_msg": str(e), "map_updated": map_updated_locally}


async def async_main(args):
    """
    非同步主函數，驅動整個 TAIFEX 資料管線的執行。

    主要職責：
    1. 初始化全域日誌記錄器 (`logger`)。
    2. 解析命令列參數 (`args`) 以獲取輸入目錄、輸出目錄、資料庫名稱等配置。
    3. 建立必要的輸出目錄和臨時目錄。
    4. 初始化硬體管理器 (`HardwareManager`) 並顯示硬體狀態。
    5. 載入或初始化 `format_map.json` (格式配方映射表)。
    6. 初始化 DuckDB 資料庫連接：
        - 設定記憶體限制、線程數和臨時目錄。
        - 創建資料表 (來自 `TABLE_DEFINITIONS`)。
        - 創建序列 (來自 `SEQUENCES`)。
        - 創建唯一索引 (來自 `UNIQUE_INDICES`) 以支持後續的 `ON CONFLICT` 操作。
    7. 調用 `discover_and_stream_files` 非同步生成器來發現並讀取輸入目錄中的所有檔案
       (包括 ZIP 壓縮檔內的成員)。
    8. 對於每個發現的檔案內容，創建一個 `asyncio.create_task` 來並行執行 `process_file_content` 函數。
       `process_file_content` 負責該檔案的配方確定、內容解析、Arrow Table 轉換及載入 DuckDB。
    9. 使用 `asyncio.gather` 等待所有檔案處理任務完成。
    10. 匯總所有任務的執行結果，統計處理的檔案數、載入的行數、發生的錯誤等。
    11. 如果 `format_map` 在處理過程中被更新，則將其保存回 `format_map.json`。
    12. 關閉 DuckDB 資料庫連接。
    13. 記錄管線執行的總時長和最終資料庫檔案路徑。
    14. 如果執行過程中出現錯誤，則記錄錯誤訊息。

    Args:
        args: `argparse` 解析後的命令列參數對象。
    """
    global logger
    logger = SimpleLogger(log_level=args.log_level)
    logger.header("TAIFEX Pipeline (Async Stream v19.1) Starting")

    db_out_dir = os.path.abspath(args.db_output_dir)
    db_fpath = os.path.join(db_out_dir, args.db_name)
    fmt_map_fpath = os.path.join(db_out_dir, FORMAT_MAP_FILENAME)
    # temp_dir 和 staging_p 在新流程中可能不再需要，或者用途改變
    # duckdb_tmp_p 仍然重要
    tmp_dir_root = os.path.abspath(args.temp_dir) if args.temp_dir else os.path.join(db_out_dir, "temp_pipeline_async")
    duckdb_tmp_p = os.path.join(tmp_dir_root, "duckdb_temp")
    for p in [db_out_dir, tmp_dir_root, duckdb_tmp_p]: os.makedirs(p, exist_ok=True)

    hw_mgr = HardwareManager(args.max_workers, args.memory_limit_gb)
    hw_mgr.display_initial_dashboard()
    t_start = time.time()

    # 初始化 format_map
    format_map = {}
    if os.path.exists(fmt_map_fpath):
        try:
            with open(fmt_map_fpath, 'r', encoding='utf-8') as f:
                format_map = json.load(f)
            logger.info(f"已載入 format map: {len(format_map)} 個條目。")
        except Exception as e:
            logger.warning(f"載入 format map 失敗: {e}")
    else:
        logger.info("未找到現有 format map，將創建新的。")

    # 初始化 DuckDB 連接
    # DuckDB 連接本身不是非同步的，但在 asyncio 中使用時，
    # 應避免長時間阻塞事件迴圈的操作在主線程中執行。
    # 對於 CPU 綁定或已進行 GIL 優化的操作，直接調用是可行的。
    try:
        # 資料庫連接最好在需要時建立，或在一個 context manager 中管理
        # 此處為了簡化，在 main 開頭建立
        db_conn = duckdb.connect(database=db_fpath, read_only=False)
        db_conn.execute(f"SET memory_limit='{hw_mgr.memory_limit_gb}GB';")
        # SET threads 可能會被 os.environ["OMP_NUM_THREADS"] = "1" 覆蓋或影響，需注意
        db_conn.execute(f"SET threads={hw_mgr.max_workers};") # 或者根據 OMP_NUM_THREADS 調整
        db_conn.execute(f"SET temp_directory='{duckdb_tmp_p}';")

        for sql in SEQUENCES.values(): db_conn.execute(sql)
        for sql in TABLE_DEFINITIONS.values(): db_conn.execute(sql)
        # 唯一索引的創建可能需要在所有數據載入後，或在載入每個表之前/之後進行管理
        # 為了 ON CONFLICT 子句，索引需要在插入前存在。
        for idx_name, idx_sql in UNIQUE_INDICES.items():
            try:
                db_conn.execute(idx_sql)
            except Exception as e_idx: # 可能因表不存在或其他原因失敗
                logger.warning(f"創建唯一索引 {idx_name} 失敗 (可能是首次運行，表尚未創建): {e_idx}")
        logger.info("DuckDB 資料庫結構已初始化。")

    except Exception as e:
        logger.error(f"DuckDB 初始化失敗: {e}")
        if 'db_conn' in locals() and db_conn: db_conn.close()
        return # 無法繼續

    total_files_processed = 0
    total_rows_in_arrow = 0
    overall_map_updated = False
    errors_encountered = 0

    tasks = []
    # discover_and_stream_files 是 AsyncGenerator
    async for descriptor, content_bytes in discover_and_stream_files(os.path.abspath(args.input_dir)):
        # 創建一個任務來處理每個檔案內容
        # 注意：如果檔案非常多，一次性創建所有任務可能消耗大量記憶體
        # 可以考慮使用 asyncio.Semaphore 來限制並行任務的數量
        task = asyncio.create_task(
            process_file_content(descriptor, content_bytes, format_map, db_conn, hw_mgr)
        )
        tasks.append(task)

    # 等待所有處理任務完成
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for res in results:
        if isinstance(res, Exception):
            logger.error(f"任務執行時發生未捕獲異常: {res}")
            errors_encountered += 1
        elif res: # Process result dict
            total_files_processed +=1
            if res.get("map_updated"):
                overall_map_updated = True
            if res.get("status") == "success":
                total_rows_in_arrow += res.get("rows_in_arrow", 0)
            elif res.get("status", "").startswith("error_"):
                errors_encountered += 1
                logger.error(f"處理檔案 {res.get('descriptor')} 時發生錯誤: {res.get('error_msg', '未知錯誤')}")

    logger.header("資料處理階段摘要")
    logger.success(f"總共處理檔案數: {total_files_processed}")
    logger.success(f"從 Arrow 表嘗試載入的總行數: {total_rows_in_arrow:,}")
    logger.info(f"錯誤發生次數: {errors_encountered}")

    if overall_map_updated:
        try:
            with open(fmt_map_fpath, 'w', encoding='utf-8') as f:
                json.dump(format_map, f, indent=4, ensure_ascii=False)
            logger.info(f"Format map 已儲存至 {fmt_map_fpath}")
        except Exception as e:
            logger.error(f"儲存 format map 失敗: {e}")

    # 關閉資料庫連接
    if db_conn:
        db_conn.close()

    logger.header(f"Pipeline finished. Total time: {time.time() - t_start:.2f}s. DB: {db_fpath}")
    if errors_encountered > 0:
        logger.error("管線執行期間發生錯誤。")
        # sys.exit(1) # 在 async main 中退出可能需要特殊處理

def main():
    parser = argparse.ArgumentParser(description="TAIFEX Pipeline (Async Stream v19.1)")
    parser.add_argument("--input-dir", required=True); parser.add_argument("--db-output-dir", required=True)
    parser.add_argument("--db-name", default="taifex_async_stream_analytics.duckdb") # 新的預設DB名稱
    parser.add_argument("--temp-dir", default=None); parser.add_argument("--max-workers",type=int,default=None) # max-workers 可能不再直接適用於async模型
    parser.add_argument("--memory-limit-gb",type=int,default=None); parser.add_argument("--log-level",default="INFO")
    args = parser.parse_args()

    asyncio.run(async_main(args))

if __name__ == "__main__":
    main()
