# -*- coding: utf-8 -*-
# 精煉廠主執行檔
import os
import sys
import json
import hashlib
import warnings
import shutil
import io
import re
import time
import threading
import argparse
from datetime import datetime
from typing import Generator, Tuple, Dict, Any, Optional, List
import concurrent.futures

# --- 第三方函式庫 (將由 requirements.txt 管理) ---
import pandas as pd
import psutil
import pyarrow # 用於 to_parquet
import duckdb
import pytz
# from pynvml import * # pynvml 設為可選，通常用於監控 GPU

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

warnings.filterwarnings("ignore", message=".*_PyDriveImportHook.find_spec.*") # 如果原始碼有

# ==============================================================================
# 輔助類別與函式 (從 Colab 腳本遷移和調整)
# ==============================================================================

class SimpleLogger:
    """一個簡化的日誌記錄器，取代原來的 DualLogger，主要輸出到控制台。"""
    def __init__(self, tz_str: str = 'Asia/Taipei', log_level: str = "INFO"):
        self.tz = pytz.timezone(tz_str)
        self.log_level_map = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
        self.current_log_level = self.log_level_map.get(log_level.upper(), 20)
        # TODO: 可以考慮整合到標準的 logging 模組

    def _get_timestamp(self) -> str:
        return datetime.now(self.tz).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]

    def _log(self, message: str, level: str, details: str = ""):
        if self.log_level_map.get(level.upper(), 0) >= self.current_log_level:
            print(f"[{self._get_timestamp()}] [{level.upper()}] {message} {details}")

    def debug(self, m, d=""): self._log(m, "DEBUG", d)
    def info(self, m, d=""): self._log(m, "INFO", d)
    def success(self, m, d=""): self._log(m, "INFO", f"✅ {d}") # Success視為INFO級別
    def warning(self, m, d=""): self._log(m, "WARNING", f"⚠️ {d}")
    def error(self, m, d=""): self._log(m, "ERROR", f"❌ {d}")
    def header(self, m): self.info(f"\n{'='*60}\n=== {m.strip()} ===\n{'='*60}")
    def section(self, m): self.info(f"\n--- {m.strip()} ---")
    def hw_log(self, m, p="[HW_MONITOR]"): self.info(m, p) # 硬體日誌視為INFO

logger = SimpleLogger() # 全域日誌實例

class HardwareManager:
    """管理硬體相關資訊和設定。"""
    def __init__(self, user_max_workers: Optional[int] = None, user_memory_limit_gb: Optional[int] = None):
        self.cpu_cores = os.cpu_count() or 2
        self.total_ram_gb = psutil.virtual_memory().total / (1024**3)

        self.max_workers = user_max_workers if user_max_workers is not None else max(1, round(self.cpu_cores * 0.8))
        self.memory_limit_gb = user_memory_limit_gb if user_memory_limit_gb is not None else int(self.total_ram_gb * 0.5)
        # self.gpu_detected = self._detect_gpu() # 暫不啟用 GPU 偵測

    # def _detect_gpu(self):
    #     try:
    #         # from pynvml import nvmlInit, NVMLError # 延後導入
    #         # nvmlInit()
    #         return True
    #     except Exception: # (NameError, NVMLError, ImportError)
    #         return False

    def get_status_line(self) -> str:
        cpu_percent = psutil.cpu_percent()
        ram = psutil.virtual_memory()
        disk = psutil.disk_usage('/') # 在 Colab 中是 /content，本地可能是 /
        return f"CPU: {cpu_percent:.1f}% | RAM: {ram.percent:.1f}% ({ram.used/(1024**3):.2f}/{self.total_ram_gb:.2f} GB) | Disk: {disk.percent:.1f}%"

    def display_initial_dashboard(self):
        logger.header("硬體狀態與執行參數")
        logger.info(f"CPU 核心數: {self.cpu_cores}")
        logger.info(f"總記憶體: {self.total_ram_gb:.2f} GB")
        # if self.gpu_detected: logger.info("GPU: 已偵測到 (詳細監控未在此版本啟用)")
        # else: logger.info("GPU: 未偵測到或未使用")

        logger.info(f"並行處理核心數 (max_workers): {self.max_workers}")
        logger.info(f"DuckDB 記憶體預算 (memory_limit): {self.memory_limit_gb} GB")
        logger.info(self.get_status_line())

    def log_event_snapshot(self, event_name: str):
        logger.hw_log(f"[{event_name}] {self.get_status_line()}", "[HW_SNAPSHOT]")


# --- 常數與核心配置 (部分從 Colab 腳本遷移) ---
# 資料庫表格定義等保持與原腳本一致
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
    'tick_data': "CREATE UNIQUE INDEX IF NOT EXISTS idx_tick_data_unique ON tick_data(trade_datetime, product_id, expiry_month, strike_price, option_type, price, volume);", # 原始 tick data 沒有 price, volume in index
    'institutional_investors': "CREATE UNIQUE INDEX IF NOT EXISTS idx_inst_inv_unique ON institutional_investors(data_date, product_name, investor_type, instrument_type, option_type);",
    'pcr': "CREATE UNIQUE INDEX IF NOT EXISTS idx_pcr_unique ON pcr(data_date);",
    'fx_rates': "CREATE UNIQUE INDEX IF NOT EXISTS idx_fx_rates_unique ON fx_rates(data_date);"
}
MANUAL_COLUMN_NAMES = {
    'futures_daily': ['trading_date', 'product_id', 'expiry_month', 'open', 'high', 'low', 'close', 'change', 'change_percent', 'volume', 'settlement_price', 'open_interest', 'last_best_bid_price', 'last_best_ask_price', 'historical_high', 'historical_low', 'is_suspended', 'trading_session', 'spread_volume'],
    'options_daily_v1': ['trading_date', 'product_id', 'expiry_month', 'strike_price', 'option_type', 'open', 'high', 'low', 'close', 'volume', 'settlement_price', 'open_interest', 'last_best_bid_price', 'last_best_ask_price', 'historical_high', 'historical_low', 'is_suspended', 'trading_session'],
    'options_daily_v2': ['trading_date', 'product_id', 'expiry_month', 'strike_price', 'option_type', 'open', 'high', 'low', 'close', 'volume', 'settlement_price', 'open_interest', 'last_best_bid_price', 'last_best_ask_price', 'historical_high', 'historical_low', 'is_suspended', 'trading_session', 'change', 'change_percent']
}
FORMAT_MAP_FILENAME = "format_map.json" # 將與 DB 同目錄存放

# --- 核心功能模組 (檔案處理 & 數據清洗) ---
# discover_files_recursively, determine_parsing_recipe, parse_with_recipe,
# pipeline_* functions, worker_process_file, run_parsing_stage, run_duckdb_loading_stage
# 將會從 Colab 腳本逐步遷移並適應化到這裡。
# 由於這些函式很多且複雜，我將分批次加入並確保它們能在此獨立腳本環境中運作。

# [v18.0] 移除 discover_files_recursively, run_parsing_stage, worker_process_file (其邏輯將整合)

def determine_parsing_recipe(content_bytes: bytes, descriptor: str) -> Optional[Dict[str, Any]]:
    # (與原 Colab 腳otá中的實作相同或微調)
    if not content_bytes: return None
    if descriptor.lower().endswith('.ods'): return {"parser": "excel_ods", "args": {}, "pipeline": "unknown"} # ods 暫不深入處理

    sample_lines, detected_encoding = [], 'ms950' # 預設為 ms950
    try:
        # 先嘗試 ms950，因為這是 TAIFEX 數據常見的舊編碼
        try:
            sample_text = content_bytes.decode('ms950')
        except UnicodeDecodeError:
            # 如果 ms950 失敗，再嘗試 utf-8 或 utf-8-sig
            try:
                detected_encoding = 'utf-8-sig'
                sample_text = content_bytes.decode(detected_encoding)
            except UnicodeDecodeError:
                detected_encoding = 'utf-8'
                sample_text = content_bytes.decode(detected_encoding)

        sample_lines = sample_text.splitlines()[:20] # 取前20行做判斷
    except UnicodeDecodeError: # 如果所有常見編碼都失敗
        logger.warning(f"檔案 {descriptor} 未能使用 ms950, utf-8, utf-8-sig 解碼，可能為二進制或未知編碼。")
        return {"parser": "unknown_encoding", "args": {}, "pipeline": "unknown"}
    except Exception as e:
        logger.warning(f"讀取 {descriptor} 樣本行時出錯: {e}")
        return None # 無法讀取樣本

    if not sample_lines: return None

    header_line_raw = sample_lines[0].strip()
    # 尋找第一個非空資料行，以避免中間有空行影響判斷
    first_data_line_raw = ""
    for line_idx in range(1, len(sample_lines)):
        line_content = sample_lines[line_idx].strip()
        if line_content: # 找到第一個非空行
            first_data_line_raw = line_content
            break

    # 基礎參數
    base_args = {"encoding": detected_encoding, "skipinitialspace": True, "thousands": ',', "dtype": "str", "on_bad_lines": "warn"}

    # --- 開始根據內容特徵判斷 ---
    # 1. 固定寬度檔案 (Tick Data)
    if "成交日期" in header_line_raw and "成交時間" in header_line_raw and "---" in first_data_line_raw:
        # 這是最明顯的特徵
        header_cols = [col.strip() for col in re.split(r'\s{2,}', header_line_raw)] # 用多個空格分割
        skip_rows_count = next((i for i, line in enumerate(sample_lines) if '---' in line), 0) + 1
        return {"parser": "fwf", "args": {**base_args, "skiprows": skip_rows_count, "names": header_cols}, "pipeline": "tick_data"}

    # 2. CSV 檔案 (動態表頭)
    try:
        # 嘗試找到包含關鍵字的表頭行在哪一行
        header_row_index = 0
        found_header_keywords = False
        for i, line_text in enumerate(sample_lines):
            if any(keyword in line_text for keyword in ['交易日期', '商品', '身份別', '日期', '美元／新台幣', '買賣權成交量比率', '契約']):
                header_row_index = i
                found_header_keywords = True
                break

        if found_header_keywords:
            # 使用找到的表頭行來讀取欄位名稱
            df_header_test = pd.read_csv(io.BytesIO(content_bytes), header=header_row_index, nrows=0, **base_args)
            column_names_set = {str(c).strip().replace(' ', '_').replace('(', '').replace(')', '') for c in df_header_test.columns}

            dynamic_csv_args = {**base_args, "header": header_row_index}
            if {'身份別', '商品名稱'}.issubset(column_names_set): return {"parser": "csv_dynamic_header", "args": dynamic_csv_args, "pipeline": "institutional_investors"}
            if {'美元／新台幣', '日期'}.issubset(column_names_set): return {"parser": "csv_dynamic_header", "args": dynamic_csv_args, "pipeline": "fx_rates"}
            if {'買賣權成交量比率', '日期'}.issubset(column_names_set): return {"parser": "csv_dynamic_header", "args": dynamic_csv_args, "pipeline": "pcr"}
            # 日行情檔的判斷需要更小心，因為欄位可能與期貨/選擇權 tick data 部分重疊
            if {'交易日期', '契約', '收盤價'}.issubset(column_names_set) and not {'成交時間'}.issubset(column_names_set): # 避免與 tick data 混淆
                return {"parser": "csv_dynamic_header", "args": dynamic_csv_args, "pipeline": "daily_ohlc"}
            if {'成交日期', '商品代號', '成交價格'}.issubset(column_names_set) and {'成交時間'}.issubset(column_names_set): # Tick data CSV (較少見)
                 return {"parser": "csv_dynamic_header", "args": dynamic_csv_args, "pipeline": "tick_data"}


    except Exception as e_csv_dynamic:
        logger.debug(f"動態表頭 CSV 判斷時發生例外 ({descriptor}): {e_csv_dynamic}")
        pass # 繼續其他判斷

    # 3. CSV 檔案 (手動指定欄位) - 通常是舊版日行情檔
    # 特徵：第一行是表頭，但逗號分隔後的欄位數可能與實際數據行不完全一致，或者表頭不夠規範
    # 這裡的判斷比較經驗性，依賴於 MANUAL_COLUMN_NAMES
    if header_line_raw and first_data_line_raw: # 確保有表頭和數據行
        header_parts_count = len(header_line_raw.split(','))
        data_parts_count = len(first_data_line_raw.split(','))

        # 如果數據行的欄位數多於表頭行 (常見於舊格式，表頭不完整) 且表頭行至少有幾個逗號 (表明是CSV)
        if data_parts_count > header_parts_count and header_parts_count > 3:
             # 檢查表頭是否包含某些關鍵字，以決定使用哪個 manual_cols set
            if '漲跌%' in header_line_raw or ('履約價' in header_line_raw and '到期月份' in header_line_raw and '買賣權' in header_line_raw and '成交量' in header_line_raw and '收盤價' in header_line_raw and '結算價' in header_line_raw and '未沖銷契約數' in header_line_raw and len(header_line_raw.split(',')) > 15) : # options_daily_v2 的特徵更明顯
                names_key_to_use = 'options_daily_v2'
            elif '履約價' in header_line_raw : # options_daily_v1
                names_key_to_use = 'options_daily_v1'
            elif '到期月份' in header_line_raw : # futures_daily
                names_key_to_use = 'futures_daily'
            else:
                names_key_to_use = None

            if names_key_to_use:
                manual_csv_args = {**base_args, "names_key": names_key_to_use, "header": None, "skiprows": 1} # 跳過第一行表頭
                return {"parser": "csv_manual_cols", "args": manual_csv_args, "pipeline": "daily_ohlc"}

    # 4. 新增：嘗試匹配無表頭但欄位數符合 MANUAL_COLUMN_NAMES 的情況 (作為最後的 CSV 嘗試)
    #    futures_daily_sample.csv 只有一行數據，所以 header_line_raw 就是數據行，first_data_line_raw 可能為空
    #    我們應該檢查 header_line_raw (即第一行) 的欄位數
    if header_line_raw: # 至少要有一行內容
        potential_data_cols_count = len(header_line_raw.split(','))

        # 檢查是否匹配 futures_daily (19個欄位)
        if potential_data_cols_count == len(MANUAL_COLUMN_NAMES['futures_daily']):
            # 簡易檢查第一個欄位是否像日期 (YYYYMMDD 或 YYYY/MM/DD)
            first_field = header_line_raw.split(',')[0].strip()
            if re.match(r"^\d{8}$", first_field) or re.match(r"^\d{4}/\d{2}/\d{2}$", first_field):
                logger.info(f"檔案 {descriptor} 符合 futures_daily (無表頭) 的欄位數 ({potential_data_cols_count}) 和日期格式，嘗試使用 csv_manual_cols。")
                manual_csv_args = {**base_args, "names_key": 'futures_daily', "header": None, "skiprows": 0}
                return {"parser": "csv_manual_cols", "args": manual_csv_args, "pipeline": "daily_ohlc"}

    logger.warning(f"未能為檔案 {descriptor} 確定解析配方。內容預覽 (前3行):\n{sample_lines[:3]}")
    return {"parser": "unknown", "args": {"encoding": detected_encoding}, "pipeline": "unknown"}

def parse_with_recipe(content_bytes: bytes, recipe: Dict[str, Any], descriptor: str) -> Optional[pd.DataFrame]:
    # (與原 Colab 腳本中的實作相同或微調)
    parser_type = recipe.get("parser")
    parse_args = recipe.get("args", {}).copy() # 複製以避免修改原始 recipe

    if parser_type == "unknown" or parser_type == "unknown_encoding":
        logger.warning(f"跳過檔案 {descriptor}，因其解析配方為 '{parser_type}'。")
        return None

    stream = io.BytesIO(content_bytes)
    try:
        if parser_type == "excel_ods":
            # ODS 通常第一行就是資料或表頭不明確，這裡先簡單讀取，後續 pipeline 再處理
            return pd.read_excel(stream, engine='odf', header=None, dtype=str)
        elif parser_type == "fwf":
            return pd.read_fwf(stream, **parse_args)
        elif parser_type in ["csv", "csv_dynamic_header"]:
            return pd.read_csv(stream, **parse_args)
        elif parser_type == "csv_manual_cols":
            names_key = parse_args.pop("names_key", None)
            if not names_key or names_key not in MANUAL_COLUMN_NAMES:
                logger.error(f"解析 {descriptor} 時，手動欄位鍵 '{names_key}' 無效。")
                return None
            manual_names = MANUAL_COLUMN_NAMES[names_key]
            # usecols 確保只讀取定義的欄位數，避免 trailing commas 等問題
            return pd.read_csv(stream, names=manual_names, usecols=range(len(manual_names)), **parse_args)
        else:
            logger.error(f"未知的解析器類型 '{parser_type}' (檔案: {descriptor})。")
            return None
    except pd.errors.EmptyDataError:
        logger.warning(f"檔案 {descriptor} 為空或不包含數據，解析跳過。")
        return None
    except UnicodeDecodeError as e_decode:
        logger.error(f"使用配方 {parser_type} 及編碼 {parse_args.get('encoding')} 解析檔案 {descriptor} 時發生 UnicodeDecodeError: {e_decode}。請檢查 determine_parsing_recipe 的編碼偵測邏輯。")
        return None
    except Exception as e:
        logger.error(f"使用配方 {parser_type} 解析檔案 {descriptor} 時發生未預期錯誤: {e}")
        # logger.error(f"  配方參數: {parse_args}") # 可選：打印詳細參數
        # logger.error(f"  檔案內容預覽 (前100 bytes): {content_bytes[:100]}") # 可選：打印內容預覽
        return None

# [佔位符 - 數據清洗管線 PIPELINE_MAP 和相關函式]
# _clean_and_prepare_df, pipeline_daily_ohlc, pipeline_tick_data, etc.
# 這些將從 Colab 腳本遷移
def _clean_and_prepare_df(df: pd.DataFrame, required_cols: List[str], rename_map: Dict[str, str]) -> pd.DataFrame:
    """標準化欄位名，檢查必要欄位。"""
    # 統一小寫，替換特殊字元
    df.columns = [str(col).strip().lower().replace(' ', '_').replace('(', '').replace(')', '').replace('/', '_').replace('%', 'percent').replace('+', 'plus') for col in df.columns]
    # 執行更名
    df_clean = df.rename(columns=lambda c: rename_map.get(c, c))

    current_cols = set(df_clean.columns)
    missing_cols = [col for col in required_cols if col not in current_cols]
    if missing_cols:
        # 警告而非直接拋出錯誤，讓管線決定如何處理 (例如填補空值)
        logger.warning(f"清洗準備時發現缺少必要欄位: {missing_cols}。可用欄位: {list(current_cols)}")
        # 為缺少的必要欄位填充 None，以保證後續操作的欄位存在性
        for col in missing_cols:
            df_clean[col] = None

    return df_clean

def pipeline_daily_ohlc(df: pd.DataFrame, source_descriptor: str) -> pd.DataFrame:
    rename_map = {
        '交易日期': 'trading_date', '契約': 'product_id', '商品代號': 'product_id',
        '到期月份_週別': 'expiry_month', '到期月份／週別': 'expiry_month', # 處理不同寫法
        '履約價': 'strike_price', '買賣權': 'option_type',
        '開盤價': 'open', '最高價': 'high', '最低價': 'low', '收盤價': 'close',
        '成交量': 'volume', '結算價': 'settlement_price', '未沖銷契約數': 'open_interest',
        '交易時段': 'trading_session', '漲跌價': 'change', '漲跌percent': 'change_percent', # 處理'%'
        # 來自 manual_cols 的可能名稱 (已是小寫下劃線)
        'last_best_bid_price': 'last_best_bid_price',
        'last_best_ask_price': 'last_best_ask_price',
        'historical_high': 'historical_high',
        'historical_low': 'historical_low',
        'is_suspended': 'is_suspended',
        'spread_volume': 'spread_volume'
    }
    # 'close' 是核心欄位，'product_id' 也非常重要
    df_clean = _clean_and_prepare_df(df, ['trading_date', 'product_id', 'close'], rename_map)

    df_clean['trading_date'] = pd.to_datetime(df_clean['trading_date'], errors='coerce').dt.strftime('%Y-%m-%d')

    if 'option_type' in df_clean.columns:
        df_clean['option_type'] = df_clean['option_type'].astype(str).str.strip().map({'買權': 'C', '賣權': 'P', 'C': 'C', 'P': 'P'})

    if 'trading_session' not in df_clean.columns or df_clean['trading_session'].isnull().all():
        df_clean['trading_session'] = 'Regular' # 預設為一般交易時段
    else: # 標準化交易時段名稱
        df_clean['trading_session'] = df_clean['trading_session'].astype(str).str.strip().replace({'盤後': 'AfterHours', '一般': 'Regular', '0':'Regular', '1':'AfterHours'})


    numeric_cols = ['open', 'high', 'low', 'close', 'volume', 'settlement_price', 'open_interest', 'strike_price', 'change', 'change_percent']
    for col in numeric_cols:
        if col in df_clean.columns:
            df_clean[col] = pd.to_numeric(df_clean[col].astype(str).str.replace(',', '').replace('-', 'NaN'), errors='coerce')

    df_clean['source'] = source_descriptor
    # 移除日期或產品ID無效的核心記錄
    return df_clean.dropna(subset=['trading_date', 'product_id', 'close'])


def pipeline_tick_data(df: pd.DataFrame, source_descriptor: str) -> pd.DataFrame:
    rename_map = {
        '成交日期': 'trade_date', '商品代號': 'product_id',
        '到期月份_週別': 'expiry_month', '履約價': 'strike_price',
        '買賣權': 'option_type', '成交時間': 'trade_time',
        '成交價格': 'price',
        '成交數量_買賣別_': 'volume_with_side', # 舊的 "成交數量(B/S)" (帶括號)
        '成交數量_b_or_s_': 'volume_with_side', # 另一種可能的 "成交數量(B/S)" (無括號但有底線)
        '成交數量_bplus_s_': 'volume', # Hotfix: 對應真實數據 "成交數量(B+S)" -> "成交數量_bplus_s_" (由 _clean_and_prepare_df 轉換)
        '成交數量': 'volume' # 如果直接有 "成交數量" 欄位
    }
    # 確保 'volume' 是 pipeline_tick_data 的核心欄位之一，即使它可能來自不同原始名稱
    df_clean = _clean_and_prepare_df(df, ['trade_date', 'trade_time', 'price', 'volume'], rename_map)


    # 合併日期和時間
    # 時間格式可能是 HH:MM:SS 或 HH:MM:SS.ffffff
    # 先嘗試帶毫秒的格式，如果失敗，再嘗試不帶毫秒的
    try:
        df_clean['trade_datetime'] = pd.to_datetime(
            df_clean['trade_date'].astype(str) + ' ' + df_clean['trade_time'].astype(str),
            format='%Y%m%d %H:%M:%S.%f', errors='coerce'
        )
    except ValueError: # 如果 %f 格式失敗 (例如時間不含毫秒)
         df_clean['trade_datetime'] = pd.to_datetime(
            df_clean['trade_date'].astype(str) + ' ' + df_clean['trade_time'].astype(str),
            format='%Y%m%d %H:%M:%S', errors='coerce'
        )
    df_clean['trade_datetime'] = df_clean['trade_datetime'].dt.strftime('%Y-%m-%d %H:%M:%S.%f')


    if 'option_type' in df_clean.columns:
        df_clean['option_type'] = df_clean['option_type'].astype(str).str.strip().map({'C': 'C', 'P': 'P', '買': 'C', '賣': 'P'})

    # 處理成交量 (可能在 'volume' 或 'volume_with_side' 中)
    # 這段邏輯在 rename_map 直接對應到 'volume' 後，可能大部分情況下不再需要
    # 但保留它以處理 'volume_with_side' 這種更複雜的原始欄位名
    if 'volume' not in df_clean.columns and 'volume_with_side' in df_clean.columns:
        # 從 '成交數量(B/S)' 中提取數字部分作為 volume
        df_clean['volume'] = df_clean['volume_with_side'].astype(str).str.extract(r'(\d+)').iloc[:, 0]
        # side_info = df_clean['volume_with_side'].astype(str).str.extract(r'\((B|S)\)').iloc[:, 0] # 可選：提取買賣方向

    numeric_cols = ['strike_price', 'price', 'volume']
    for col in numeric_cols:
        if col in df_clean.columns:
            df_clean[col] = pd.to_numeric(df_clean[col], errors='coerce')

    df_clean['source'] = source_descriptor
    return df_clean.dropna(subset=['trade_datetime', 'product_id', 'price', 'volume'])


def pipeline_institutional_investors(df: pd.DataFrame, source_descriptor: str) -> pd.DataFrame:
    rename_map = {'身份別': 'investor_type', '商品名稱': 'product_name'}
    df_clean = _clean_and_prepare_df(df, ['investor_type', 'product_name'], rename_map)

    date_col_found = next((c for c in df_clean.columns if c in ['日期', '交易日', 'data_date']), None)
    if not date_col_found:
        logger.error(f"清洗三大法人數據 ({source_descriptor}) 時找不到日期欄位。")
        return pd.DataFrame() # 返回空 DataFrame
    df_clean = df_clean.rename(columns={date_col_found: 'data_date'})
    df_clean['data_date'] = pd.to_datetime(df_clean['data_date'], errors='coerce').dt.strftime('%Y-%m-%d')

    # 判斷是期貨還是選擇權，並設定買賣權類型
    df_clean['instrument_type'] = df_clean.apply(
        lambda r: 'Option' if '買賣權' in r and pd.notna(r['買賣權']) and str(r['買賣權']).strip() in ['買權', '賣權'] else 'Future',
        axis=1
    )
    if '買賣權' in df_clean.columns: # 確保欄位存在
        df_clean['option_type'] = df_clean['買賣權'].astype(str).str.strip().map({'買權': 'C', '賣權': 'P'})
    else:
        df_clean['option_type'] = None


    # 欄位對應 (將中文欄位名轉為英文)
    col_map_pos = {
        '多方交易口數': 'long_pos_vol', '多方交易契約金額_千元_': 'long_pos_val_twd_k',
        '空方交易口數': 'short_pos_vol', '空方交易契約金額_千元_': 'short_pos_val_twd_k',
        '多空交易淨口數': 'net_pos_vol', '多空交易淨額_千元_': 'net_pos_val_twd_k',
    }
    col_map_oi = {
        '未平倉多方口數': 'long_oi_vol', '未平倉多方契約金額_千元_': 'long_oi_val_twd_k',
        '未平倉空方口數': 'short_oi_vol', '未平倉空方契約金額_千元_': 'short_oi_val_twd_k',
        '未平倉淨口數': 'net_oi_vol', '未平倉淨額_千元_': 'net_oi_val_twd_k',
    }
    df_clean = df_clean.rename(columns=lambda c: col_map_pos.get(c,c))
    df_clean = df_clean.rename(columns=lambda c: col_map_oi.get(c,c))

    all_numeric_cols = list(col_map_pos.values()) + list(col_map_oi.values())
    for col in all_numeric_cols:
        if col in df_clean.columns:
            df_clean[col] = pd.to_numeric(df_clean[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0).astype(int)
        else: # 如果欄位不存在，則創建並填充0 (某些舊格式可能缺少部分欄位)
            df_clean[col] = 0

    df_clean['source'] = source_descriptor
    return df_clean.dropna(subset=['data_date', 'product_name', 'investor_type'])


def pipeline_fx_rates(df: pd.DataFrame, source_descriptor: str) -> pd.DataFrame:
    rename_map = {
        '日期': 'data_date', '美元／新台幣': 'usd_twd', '人民幣／新台幣': 'cny_twd',
        '歐元／美元': 'eur_usd', '美元／日圓': 'usd_jpy', '英鎊／美元': 'gbp_usd',
        '澳幣／美元': 'aud_usd', '美元／港幣': 'usd_hkd', '美元／人民幣': 'usd_cny',
        '美元／南非幣': 'usd_zar', '紐幣／美元': 'nzd_usd'
    }
    df_clean = _clean_and_prepare_df(df, ['data_date', 'usd_twd'], rename_map)
    df_clean['data_date'] = pd.to_datetime(df_clean['data_date'], errors='coerce').dt.strftime('%Y-%m-%d')

    for col_key in rename_map.keys(): # 迭代原始中文名以確保轉換後的英文名存在
        eng_col_name = rename_map[col_key]
        if eng_col_name != 'data_date' and eng_col_name in df_clean.columns:
            df_clean[eng_col_name] = pd.to_numeric(df_clean[eng_col_name], errors='coerce')

    df_clean['source'] = source_descriptor
    return df_clean.dropna(subset=['data_date'])


def pipeline_pcr(df: pd.DataFrame, source_descriptor: str) -> pd.DataFrame:
    rename_map = {
        '日期': 'data_date', '賣權成交量': 'put_volume', '買權成交量': 'call_volume',
        '買賣權成交量比率percent': 'pcr_volume',
        '賣權未平倉量': 'put_oi', '買權未平倉量': 'call_oi',
        '買賣權未平倉量比率percent': 'pcr_oi'
    }
    # pcr_volume 和 pcr_oi 是核心，但來源檔可能只有一個或兩個
    df_clean = _clean_and_prepare_df(df, ['data_date'], rename_map)
    df_clean['data_date'] = pd.to_datetime(df_clean['data_date'], errors='coerce').dt.strftime('%Y-%m-%d')

    # 確保所有可能的數值欄位都被處理
    pcr_numeric_cols = ['put_volume', 'call_volume', 'pcr_volume', 'put_oi', 'call_oi', 'pcr_oi']
    for col in pcr_numeric_cols:
        if col in df_clean.columns:
            # 移除 '%' 並轉換為數字
            df_clean[col] = pd.to_numeric(df_clean[col].astype(str).str.replace('%', '').str.replace(',', ''), errors='coerce')
        else: # 如果欄位不存在，創建並設為 None (或 NaN)
            df_clean[col] = pd.NA

    df_clean['source'] = source_descriptor
    return df_clean.dropna(subset=['data_date'])


PIPELINE_MAP = {
    "daily_ohlc": pipeline_daily_ohlc,
    "institutional_investors": pipeline_institutional_investors,
    "tick_data": pipeline_tick_data,
    "fx_rates": pipeline_fx_rates,
    "pcr": pipeline_pcr,
    "unknown": lambda df, source: logger.warning(f"資料來源 {source} 的管線未知，跳過清洗。") or df # 未知管線直接返回原 df
}

# determine_parsing_recipe, parse_with_recipe, _clean_and_prepare_df,
# pipeline_* functions, PIPELINE_MAP 保持不變 (或微調以適應單檔案處理流程)
# ... (這些函式定義不變，此處省略以簡潔) ...
# (此處假設以上函式定義與前一版本相同，僅作標記)
# [舊函式定義結束] - 實際程式碼中這些函式仍然存在

# 新的單檔案處理核心函式
def process_single_file_entry(
    file_path: str,
    descriptor: str, # 描述符，例如 "zip_filename -> internal_csv_filename" 或僅檔名
    db_conn: duckdb.DuckDBPyConnection,
    format_map: dict, # 共享的格式地圖 (dict)
    hw_manager: HardwareManager # 用於日誌
    # temp_processing_dir: str # 如果需要解壓縮ZIP內的檔案到臨時位置
) -> Dict[str, Any]:
    """
    處理從佇列接收到的單個檔案條目：讀取、解析、清洗並直接寫入 DuckDB。
    返回處理結果字典。
    """
    logger.info(f"開始處理佇列項目: {descriptor}")
    hw_manager.log_event_snapshot(f"開始處理: {descriptor[:40]}")

    processed_rows_count = 0
    status = "error" # 預設狀態
    message = ""

    try:
        # 1. 讀取檔案內容
        #    如果 file_path 是 ZIP 內的檔案，協調器需要先解壓到一個臨時位置，
        #    然後將該臨時 CSV 的路徑放入佇列。或者此函式處理 ZIP 解壓。
        #    為簡化，假設 file_path 已是可直接讀取的 CSV 檔案路徑。
        #    如果佇列項目是 {'type':'zip', 'path':'/path/to.zip', 'member':'file.csv'}
        #    則需要先處理解壓縮。
        #    目前計畫是 downloader 將下載的 ZIP 路徑放入佇列，
        #    pipeline 的 worker 需要能處理 ZIP。
        #    或者，downloader 解壓後將 CSV 路徑放入佇列。
        #    根據 v18.0 階段一，downloader 放入的是 ZIP 的本地路徑。
        #    因此，pipeline worker 需要處理 ZIP。

        content_bytes_list_with_descriptors = []
        if file_path.lower().endswith('.zip'):
            import zipfile # 延後 import
            try:
                with open(file_path, 'rb') as f_zip:
                    zip_content_bytes = f_zip.read() # 讀取整個 ZIP
                if not zipfile.is_zipfile(io.BytesIO(zip_content_bytes)):
                    raise ValueError(f"檔案 {file_path} 不是有效的 ZIP 檔案。")

                with zipfile.ZipFile(io.BytesIO(zip_content_bytes), 'r') as zf:
                    for member_info in sorted(zf.infolist(), key=lambda mi: mi.filename):
                        if member_info.is_dir() or '__MACOSX' in member_info.filename:
                            continue
                        with zf.open(member_info) as member_file:
                            content_bytes = member_file.read()
                        content_bytes_list_with_descriptors.append(
                            (f"{descriptor} -> {member_info.filename}", content_bytes)
                        )
                if not content_bytes_list_with_descriptors:
                    logger.info(f"ZIP 檔案 {descriptor} 為空或不包含有效成員。")
                    return {'status': 'skipped_empty_zip', 'message': "ZIP為空或無有效成員", 'descriptor': descriptor, 'rows_added': 0}

            except Exception as e_zip:
                logger.error(f"處理 ZIP 檔案 {descriptor} 時發生錯誤: {e_zip}")
                return {'status': 'error_zip_processing', 'message': str(e_zip), 'descriptor': descriptor, 'rows_added': 0}
        else: # 非 ZIP 檔案，直接作為單個內容處理
            with open(file_path, 'rb') as f_direct:
                content_bytes = f_direct.read()
            content_bytes_list_with_descriptors.append((descriptor, content_bytes))

        # 迭代處理（可能是 ZIP 內的多個檔案，或單個檔案）
        total_rows_added_for_entry = 0
        all_successful_pipelines = []

        for item_descriptor, item_content_bytes in content_bytes_list_with_descriptors:
            if not item_content_bytes:
                logger.info(f"內容為空: {item_descriptor}，跳過。")
                continue

            # 2. 獲取/更新解析配方 (使用 item_content_bytes 的雜湊)
            content_hash = hashlib.sha256(item_content_bytes).hexdigest()
            recipe = format_map.get(content_hash)
            map_updated_by_this_item = False
            if not recipe:
                recipe = determine_parsing_recipe(item_content_bytes, item_descriptor)
                if recipe:
                    format_map[content_hash] = recipe
                    map_updated_by_this_item = True # 標記 format_map 已更新
                    logger.info(f"動態學習: 為 '{item_descriptor[:60]}' 配方 -> {recipe.get('pipeline','unknown')}")
                else:
                    format_map[content_hash] = {"parser": "unknown", "pipeline": "unknown", "args": {}}
                    map_updated_by_this_item = True
                    logger.warning(f"學習失敗: 無法為 '{item_descriptor[:60]}' 建立配方。")

            if not recipe or recipe.get("pipeline") == "unknown" or recipe.get("parser") in ["unknown", "unknown_encoding"]:
                logger.warning(f"'{item_descriptor[:60]}' 配方未知或無效，跳過。")
                continue

            # 3. 解析
            parsed_df = parse_with_recipe(item_content_bytes, recipe, item_descriptor)
            if parsed_df is None or parsed_df.empty:
                logger.info(f"'{item_descriptor[:60]}' 解析後為空，跳過。")
                continue

            # 4. 清洗
            pipeline_name = recipe.get("pipeline")
            pipeline_func = PIPELINE_MAP.get(pipeline_name)
            if not pipeline_func:
                logger.warning(f"'{item_descriptor[:60]}' 無對應管線 '{pipeline_name}'，跳過。")
                continue

            cleaned_df = pipeline_func(parsed_df, item_descriptor) # descriptor 作為 source
            if cleaned_df is None or cleaned_df.empty:
                logger.info(f"'{item_descriptor[:60]}' 清洗後為空，跳過。")
                continue

            # 5. 直接寫入 DuckDB (整合原 run_duckdb_loading_stage 的邏輯)
            target_table_name = pipeline_name # pipeline 名稱即為表格名稱
            if target_table_name not in TABLE_DEFINITIONS:
                logger.warning(f"目標表格 '{target_table_name}' (來自 {item_descriptor}) 未定義，跳過寫入。")
                continue

            # --- DuckDB 寫入邏輯開始 (針對 cleaned_df) ---
            # 創建臨時表名，基於原始描述符和內容哈希，以確保唯一性
            # temp_staging_table_for_df = f"temp_staging_{target_table_name}_{content_hash[:8]}"

            # DuckDB 的 register 方法可以直接將 Pandas DataFrame 註冊為臨時視圖/表
            # db_conn.register(temp_staging_table_for_df, cleaned_df)
            # logger.info(f"  ↳ DataFrame ({len(cleaned_df)}筆) 已註冊為臨時表 '{temp_staging_table_for_df}'")

            # 獲取目標表和 DataFrame 的欄位 (已在 cleaned_df 中標準化)
            target_table_cols_desc = db_conn.execute(f"DESCRIBE {target_table_name};").fetchall()
            df_cols_set = set(cleaned_df.columns)

            target_cols_set = {col_info[0] for col_info in target_table_cols_desc if col_info[0] != 'id'}
            common_cols_for_insert = list(target_cols_set.intersection(df_cols_set))

            if not common_cols_for_insert:
                logger.warning(f"  ↳ 表格 '{target_table_name}' 與 DataFrame ({item_descriptor}) 無共同欄位 (除id)，無法插入。")
                # db_conn.unregister(temp_staging_table_for_df) # 清理臨時註冊
                continue

            common_cols_str_quoted = ", ".join([f'"{c}"' for c in common_cols_for_insert])

            # 預先去重邏輯 (如果適用)
            unique_constraint_cols_str = ""
            final_df_to_insert = cleaned_df[common_cols_for_insert] # 只選擇共同欄位

            if target_table_name in UNIQUE_INDICES:
                match_uq = re.search(r'\((.*?)\)', UNIQUE_INDICES[target_table_name])
                if match_uq:
                    unique_constraint_cols_str = match_uq.group(1)
                    # 確保 unique_constraint_cols_str 中的所有欄位都在 final_df_to_insert 中
                    unique_cols_list = [c.strip() for c in unique_constraint_cols_str.split(',')]
                    if all(c in final_df_to_insert.columns for c in unique_cols_list):
                         # 使用 DataFrame 的 drop_duplicates
                        final_df_to_insert = final_df_to_insert.drop_duplicates(subset=unique_cols_list, keep='first')
                        logger.info(f"  ↳ DataFrame ({item_descriptor}) 基於 ({unique_constraint_cols_str}) 去重後剩餘 {len(final_df_to_insert)} 筆。")
                    else:
                        logger.warning(f"  ↳ 表格 '{target_table_name}' 的唯一約束欄位 {unique_cols_list} 部分不在DataFrame欄位中，跳過基於業務鍵的DataFrame去重。")
                        unique_constraint_cols_str = "" # 重置，以避免後續錯誤使用

            if final_df_to_insert.empty:
                logger.info(f"  ↳ DataFrame ({item_descriptor}) 去重後為空，跳過插入。")
                # db_conn.unregister(temp_staging_table_for_df)
                continue

            # 插入數據
            # DuckDB Python client can directly insert a Pandas DataFrame into a table.
            # We need to handle the 'id' column generation using sequence.
            # One way is to insert common_cols and let 'id' be generated by default if table is set up with it,
            # or select nextval explicitly if inserting all columns.
            # For simplicity with ON CONFLICT, it's often easier if the DataFrame matches the target table structure (minus id).

            # 由於我們要使用 nextval('seq_...') 和 ON CONFLICT，直接使用 SQL 插入更可靠
            # 將 DataFrame 註冊為臨時表，然後用 SQL 插入
            temp_df_view_name = f"temp_df_view_{content_hash[:8]}"
            db_conn.register(temp_df_view_name, final_df_to_insert)

            insert_sql_core = f"""
                INSERT INTO {target_table_name} (id, {common_cols_str_quoted})
                SELECT nextval('seq_{target_table_name}'), {common_cols_str_quoted}
                FROM {temp_df_view_name}
            """

            if UNIQUE_INDICES.get(target_table_name) and unique_constraint_cols_str:
                # 確保索引存在 (如果不存在會創建)
                try: db_conn.execute(UNIQUE_INDICES[target_table_name])
                except Exception: pass # 可能已存在，忽略錯誤

                conflict_target_cols_quoted = ", ".join([f'"{c.strip()}"' for c in unique_constraint_cols_str.split(',')])
                insert_sql = f"{insert_sql_core} ON CONFLICT ({conflict_target_cols_quoted}) DO NOTHING;"
                logger.info(f"  ↳ 執行基於業務鍵 ({conflict_target_cols_quoted}) 的去重插入。")
            else: # 基於 id 的衝突 (如果 id 是主鍵)
                insert_sql = f"{insert_sql_core} ON CONFLICT (id) DO NOTHING;"
                logger.info(f"  ↳ 執行基於ID的衝突插入。")

            db_conn.execute(insert_sql)
            db_conn.unregister(temp_df_view_name) # 清理臨時視圖

            # 這裡無法輕易獲得實際插入的行數，除非用 SELECT COUNT(*) 前後比較
            # 但我們知道 final_df_to_insert 的行數是嘗試插入的行數
            rows_attempted_this_df = len(final_df_to_insert)
            total_rows_added_for_entry += rows_attempted_this_df
            all_successful_pipelines.append(pipeline_name)
            logger.success(f"  ↳ DataFrame ({item_descriptor}) 的 {rows_attempted_this_df} 筆記錄嘗試寫入 '{target_table_name}'。")
            # --- DuckDB 寫入邏輯結束 ---

        # 如果整個佇列項目（可能是ZIP）被成功處理了至少一個內部檔案
        if total_rows_added_for_entry > 0 or not content_bytes_list_with_descriptors: # 如果zip為空也算成功處理
            status = "success"
            message = f"成功處理 {len(content_bytes_list_with_descriptors)} 個內部項目, 嘗試加入 {total_rows_added_for_entry} 筆記錄到 {list(set(all_successful_pipelines))}。"
        elif not message : # 如果沒有任何成功，但也沒有特定錯誤訊息
            status = "skipped_all_items"
            message = "所有內部項目均被跳過或為空。"


    except FileNotFoundError:
        logger.error(f"處理佇列項目時檔案不存在: {file_path}")
        status = "error_file_not_found"
        message = f"檔案不存在: {file_path}"
    except Exception as e:
        logger.error(f"處理佇列項目 {descriptor} 時發生未預期錯誤: {e}")
        status = "error_processing_entry"
        message = str(e)

    hw_manager.log_event_snapshot(f"處理完成: {descriptor[:40]} -> {status}")
    return {'status': status, 'message': message, 'descriptor': descriptor, 'rows_added': total_rows_added_for_entry, 'map_updated_internally': map_updated_by_this_item if 'map_updated_by_this_item' in locals() else False}


# 原 run_duckdb_loading_stage 函式將被移除或其邏輯併入 process_single_file_entry 和 run_pipeline_from_queue


# ==============================================================================
# 主執行函數 (v18.0 佇列驅動版本)
# ==============================================================================
def run_pipeline_from_queue(
    task_queue: Any, # multiprocessing.Queue or queue.Queue
    db_file_path: str,
    format_map_path: str,
    processing_temp_dir: str, # 用於解壓縮等，如果 process_single_file_entry 需要
    hw_settings: Dict[str, Any],
    stop_sentinel: Any = "STOP_PROCESSING_PIPELINE" # 哨兵值
):
    logger.header("TAIFEX 數據精煉廠 v18.0 (佇列模式) 啟動")

    hw_manager = HardwareManager(
        user_max_workers=hw_settings.get("max_workers"),
        user_memory_limit_gb=hw_settings.get("memory_limit_gb")
    )
    hw_manager.display_initial_dashboard()

    # 建立必要的目錄 (通常由協調器或 main 函式處理，但這裡也檢查一下)
    os.makedirs(os.path.dirname(db_file_path), exist_ok=True)
    os.makedirs(os.path.dirname(format_map_path), exist_ok=True)
    os.makedirs(processing_temp_dir, exist_ok=True)

    # 載入或初始化 format_map
    format_map = {}
    if os.path.exists(format_map_path):
        try:
            with open(format_map_path, 'r', encoding='utf-8') as f_map:
                format_map = json.load(f_map)
            logger.info(f"已成功從 {format_map_path} 載入格式地圖 ({len(format_map)}筆)。")
        except Exception as e_map_load:
            logger.warning(f"載入格式地圖 {format_map_path} 失敗: {e_map_load}。將建立新地圖。")

    format_map_changed_during_run = False

    # 初始化 DuckDB 連接
    db_conn = None
    try:
        db_conn = duckdb.connect(database=db_file_path, read_only=False)
        logger.success(f"成功連接至 DuckDB: {db_file_path}")
        # 設定 DuckDB 環境 (如果需要，但通常在連接字串或PRAGMA中設定)
        # db_conn.execute(f"SET memory_limit = '{hw_manager.memory_limit_gb}GB';") # 已由 hw_manager 內部設定
        # db_conn.execute(f"SET threads = {hw_manager.max_workers};")
        db_conn.execute(f"SET temp_directory = '{os.path.join(processing_temp_dir, 'duckdb_worker_temp')}';")

        # 建立 schema (表格、序列、索引)
        for seq_sql in SEQUENCES.values(): db_conn.execute(seq_sql)
        for table_sql in TABLE_DEFINITIONS.values(): db_conn.execute(table_sql)
        # 唯一索引通常在數據插入後或期間建立，以優化大量插入性能
        # 但如果依賴 ON CONFLICT (業務鍵)，則需先建立
        # 這裡的邏輯是 process_single_file_entry 內部會嘗試建立
        logger.info("資料庫 schema (表格、序列) 已確認/建立。唯一索引將在寫入時處理。")

    except Exception as e_db_init:
        logger.error(f"佇列處理器：資料庫初始化失敗 ({db_file_path}): {e_db_init}")
        # 如果DB無法初始化，則無法繼續處理佇列
        if db_conn: db_conn.close()
        return # 或拋出例外

    total_files_processed = 0
    total_rows_accumulated = 0

    while True:
        try:
            # 佇列項目預期是下載器放入的檔案路徑 (str) 或包含路徑的字典
            # 例如: {'type': 'file', 'path': '/path/to/downloaded_file.zip', 'original_url': '...'}
            # 或直接是 '/path/to/downloaded_file.zip'
            # 哨兵值用於停止
            queue_item = task_queue.get(timeout=5) # timeout 防止永久阻塞，但協調器應保證哨兵

            if queue_item == stop_sentinel:
                logger.info("收到停止信號，結束佇列處理。")
                break

            file_path_to_process = None
            item_descriptor_for_log = "未知項目"

            if isinstance(queue_item, str): # 直接是路徑
                file_path_to_process = queue_item
                item_descriptor_for_log = os.path.basename(file_path_to_process)
            elif isinstance(queue_item, dict) and 'path' in queue_item: # 字典格式
                file_path_to_process = queue_item['path']
                item_descriptor_for_log = queue_item.get('source_url', os.path.basename(file_path_to_process))
            else:
                logger.warning(f"佇列中收到未知格式項目: {queue_item}，跳過。")
                continue

            if not os.path.exists(file_path_to_process):
                logger.warning(f"佇列提供的檔案路徑不存在: {file_path_to_process}，跳過。")
                continue

            # 調用核心處理函式
            result = process_single_file_entry(
                file_path=file_path_to_process,
                descriptor=item_descriptor_for_log,
                db_conn=db_conn,
                format_map=format_map, # 傳遞整個字典的引用
                hw_manager=hw_manager
                # processing_temp_dir # 如果 process_single_file_entry 需要解壓到特定位置
            )

            total_files_processed += 1
            if result.get('status') == 'success':
                total_rows_accumulated += result.get('rows_added', 0)
            if result.get('map_updated_internally', False): # 檢查 format_map 是否在 process_single_file_entry 中被修改
                format_map_changed_during_run = True

            # 檔案處理完畢後可以考慮是否刪除本地原始檔 (如果它是從downloader的暫存區來的)
            # 這取決於協調器的策略，pipeline worker 不應自行決定刪除佇列中的原始檔
            # if file_path_to_process.startswith(downloader_local_temp_area):
            #    try: os.remove(file_path_to_process) except OSError: pass


        except queue.Empty: # queue.Empty 是 queue 模組的例外
            logger.info("佇列在超時時間內為空，繼續等待...")
            # 這裡可以加入一個計數器，如果連續多次為空，可能意味著上游已結束但未發送哨兵
            # 不過，正常的停止依賴於哨兵值
            continue
        except Exception as e_queue_loop:
            logger.error(f"處理佇列時發生未預期錯誤: {e_queue_loop}")
            # 決定是否要中斷整個 worker，或只是記錄錯誤並繼續
            # 暫時選擇繼續，除非是嚴重到無法操作DB的錯誤
            # time.sleep(1) # 避免快速連續失敗

    # 循環結束後
    logger.info(f"佇列處理完畢。共處理 {total_files_processed} 個檔案條目，嘗試加入約 {total_rows_accumulated} 筆記錄。")

    if format_map_changed_during_run: # 只有在運行中確實修改了才儲存
        try:
            with open(format_map_path, 'w', encoding='utf-8') as f_map_save:
                json.dump(format_map, f_map_save, indent=4, ensure_ascii=False)
            logger.info(f"格式地圖已更新並儲存至 {format_map_path}")
        except Exception as e_map_save_final:
            logger.error(f"儲存最終格式地圖至 {format_map_path} 時失敗: {e_map_save_final}")

    if db_conn:
        try:
            # 在關閉前確保所有索引都已建立 (如果之前是延後建立)
            for table_name_idx, idx_sql in UNIQUE_INDICES.items():
                if table_name_idx in TABLE_DEFINITIONS: # 只為存在的表建立索引
                    try:
                        db_conn.execute(idx_sql)
                        logger.info(f"已在 '{table_name_idx}' 上確認/重建最終唯一性索引。")
                    except Exception as e_final_idx:
                         logger.warning(f"在 '{table_name_idx}' 上建立最終唯一索引時發生警告/錯誤: {e_final_idx} (可能已存在或表結構問題)")
            db_conn.close()
            logger.success(f"DuckDB 連線已關閉: {db_file_path}")
        except Exception as e_db_close:
            logger.error(f"關閉 DuckDB 連線 ({db_file_path}) 時發生錯誤: {e_db_close}")

    logger.header("TAIFEX 數據精煉廠 v18.0 (佇列模式) 執行完畢")


def main(): # main 現在主要用於獨立測試或作為一個可被協調器調用的入口點的包裝
    parser = argparse.ArgumentParser(description="TAIFEX 數據精煉廠 v18.0 (佇列驅動測試模式)。")
    # parser.add_argument("--input-dir", required=True, help="包含原始數據檔案的輸入目錄路徑。") # 已移除
    parser.add_argument("--db-output-dir", required=True, help="DuckDB 資料庫檔案及格式地圖的輸出目錄路徑。")
    parser.add_argument("--db-name", default="taifex_pipeline_analytics_q.duckdb", help="DuckDB 資料庫的檔案名稱 (預設: taifex_pipeline_analytics_q.duckdb)。")
    parser.add_argument("--processing-temp-dir", default=None, help="處理過程的臨時檔案目錄 (預設: 在 db-output-dir 下建立 'temp_pipeline_proc')。")
    parser.add_argument("--test-file-path", default=None, help="[測試用] 單個或多個 (逗號分隔) 檔案路徑，用於填充測試佇列。")

    # 硬體相關參數，傳遞給 hw_settings
    parser.add_argument("--max-workers", type=int, default=None, help="並行處理核心數 (預設: CPU核心數 * 0.8) - 主要影響 hw_manager 日誌，實際並行由協調器控制。")
    parser.add_argument("--memory-limit-gb", type=int, default=None, help="DuckDB 記憶體預算 (GB) (預設: 系統記憶體 * 0.5) - 主要影響 hw_manager 日誌。")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], help="日誌級別 (預設: INFO)。")

    args = parser.parse_args()

    global logger
    logger = SimpleLogger(log_level=args.log_level)

    db_output_dir = os.path.abspath(args.db_output_dir)
    db_file_full_path = os.path.join(db_output_dir, args.db_name)
    format_map_full_path = os.path.join(db_output_dir, FORMAT_MAP_FILENAME)

    if args.processing_temp_dir:
        processing_temp_dir = os.path.abspath(args.processing_temp_dir)
    else:
        processing_temp_dir = os.path.join(db_output_dir, "temp_pipeline_proc") # 改名以區分

    hw_settings_dict = {
        "max_workers": args.max_workers,
        "memory_limit_gb": args.memory_limit_gb
    }

    # --- 模擬佇列和協調器行為進行測試 ---
    import queue # 使用標準佇列進行單進程測試
    test_q = queue.Queue()

    if args.test_file_path:
        paths_to_test = args.test_file_path.split(',')
        for p_test in paths_to_test:
            p_test_abs = os.path.abspath(p_test.strip())
            if os.path.exists(p_test_abs):
                # 放入佇列的項目可以是簡單的路徑，或更結構化的字典
                # 這裡用字典模擬 downloader 可能放入的格式
                test_q.put({'type': 'file', 'path': p_test_abs, 'source_url': f'local_test_file:{os.path.basename(p_test_abs)}'})
                logger.info(f"已將測試檔案 {p_test_abs} 加入佇列。")
            else:
                logger.warning(f"提供的測試檔案路徑不存在: {p_test_abs}")
    else:
        logger.info("未提供 --test-file-path，佇列為空。僅測試初始化和空佇列處理。")

    stop_signal = "STOP_PIPELINE_PROCESSING_PLEASE" # 哨兵值
    test_q.put(stop_signal) # 加入哨兵值

    run_pipeline_from_queue(
        task_queue=test_q,
        db_file_path=db_file_full_path,
        format_map_path=format_map_full_path,
        processing_temp_dir=processing_temp_dir,
        hw_settings=hw_settings_dict,
        stop_sentinel=stop_signal
    )

    logger.info("獨立測試模式執行完畢。")


if __name__ == "__main__":
    main()
