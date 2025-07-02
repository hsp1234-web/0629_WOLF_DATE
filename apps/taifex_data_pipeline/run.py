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

# [佔位符 - 檔案發現與解析邏輯]
def discover_files_recursively(root_path: str) -> Generator[Tuple[str, bytes], None, None]:
    # (與原 Colab 腳本中的實作相同或微調)
    if not os.path.exists(root_path):
        logger.warning(f"指定的輸入路徑不存在: {root_path}")
        return
    items_to_scan = [] # 使用 list 來模擬 queue，方便 sorted
    for item_name in sorted(os.listdir(root_path)):
        items_to_scan.append((item_name, os.path.join(root_path, item_name)))

    processed_count = 0
    while items_to_scan:
        descriptor, current_path = items_to_scan.pop(0) # FIFO for breadth-first like
        try:
            if os.path.isdir(current_path):
                # logger.debug(f"掃描目錄: {current_path}")
                # 將子項目加入佇列前端，以優先處理當前目錄的子項 (若要深度優先則反之)
                # 為了保持與原 discover_files_recursively 的行為一致 (似乎是某種混合)，這裡直接添加
                new_items = []
                for name_in_dir in sorted(os.listdir(current_path)): # 排序以確保一致性
                    new_items.append((f"{descriptor}/{name_in_dir}", os.path.join(current_path, name_in_dir)))
                items_to_scan = new_items + items_to_scan # 潛在的深度優先行為
                continue

            # logger.debug(f"準備讀取檔案: {current_path} (描述符: {descriptor})")
            with open(current_path, 'rb') as f_content:
                content_bytes = f_content.read()

            import zipfile # 延後 import
            if current_path.lower().endswith('.zip') and zipfile.is_zipfile(io.BytesIO(content_bytes)):
                # logger.debug(f"解壓縮 ZIP 檔案: {descriptor}")
                with zipfile.ZipFile(io.BytesIO(content_bytes), 'r') as zf:
                    zip_members = []
                    for member_info in sorted(zf.infolist(), key=lambda mi: mi.filename): # 按名稱排序
                        if member_info.is_dir() or '__MACOSX' in member_info.filename:
                            continue
                        # logger.debug(f"  準備從 ZIP 讀取: {member_info.filename}")
                        with zf.open(member_info) as member_file:
                            member_content_bytes = member_file.read()
                        yield f"{descriptor} -> {member_info.filename}", member_content_bytes
                        processed_count +=1
            else:
                yield descriptor, content_bytes
                processed_count += 1
        except FileNotFoundError:
            logger.warning(f"掃描時檔案消失: {current_path}")
        except Exception as e:
            logger.warning(f"讀取或解壓縮 '{descriptor}' ({current_path}) 時出錯: {e}")
    # logger.info(f"discover_files_recursively 完成，共處理 {processed_count} 個檔案/成員。")


def determine_parsing_recipe(content_bytes: bytes, descriptor: str) -> Optional[Dict[str, Any]]:
    # (與原 Colab 腳本中的實作相同或微調)
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
    # 問題2修正: "dtype": str -> "dtype": "str" (或其他 pandas 可接受的字串表示)
    # pandas read_csv 的 dtype 可以接受 'str' 或 object (代表字串)
    # 為了 JSON 序列化，這裡使用字串 'str'
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

        # 可以為 options_daily_v1 (18欄位) / v2 (20欄位) 也加入類似的無表頭判斷 (如果需要)
        # 例如 options_daily_v1:
        # elif potential_data_cols_count == len(MANUAL_COLUMN_NAMES['options_daily_v1']):
        #     first_field = header_line_raw.split(',')[0].strip()
        #     if re.match(r"^\d{8}$", first_field) or re.match(r"^\d{4}/\d{2}/\d{2}$", first_field):
        #         logger.info(f"檔案 {descriptor} 符合 options_daily_v1 (無表頭) 的欄位數 ({potential_data_cols_count}) 和日期格式，嘗試使用 csv_manual_cols。")
        #         manual_csv_args = {**base_args, "names_key": 'options_daily_v1', "header": None, "skiprows": 0}
        #         return {"parser": "csv_manual_cols", "args": manual_csv_args, "pipeline": "daily_ohlc"}

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
    df.columns = [str(col).strip().lower().replace(' ', '_').replace('(', '').replace(')', '').replace('/', '_').replace('%', 'percent') for col in df.columns]
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
        '成交數量_b+s_': 'volume', # 新增：對應真實數據 "成交數量(B+S)" -> "成交數量_b+s_" (由 _clean_and_prepare_df 轉換)
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

# [佔位符 - 並行處理與主流程 worker_process_file, run_parsing_stage, run_duckdb_loading_stage]
def worker_process_file(args_tuple: Tuple[str, bytes, Dict, str, Any]) -> Dict[str, Any]:
    descriptor, content_bytes, recipe, staging_dir_path, hw_manager_ref = args_tuple
    # logger.debug(f"工人開始處理: {descriptor[:50]}...")
    try:
        parsed_df = parse_with_recipe(content_bytes, recipe, descriptor)
        if parsed_df is None or parsed_df.empty:
            # logger.info(f"檔案 {descriptor} 解析後為空或解析失敗，跳過。")
            return {'status': 'skipped_empty_or_parse_fail', 'message': "解析後為空或解析失敗", 'descriptor': descriptor}

        pipeline_name = recipe.get("pipeline", "unknown")
        pipeline_func = PIPELINE_MAP.get(pipeline_name)

        if not pipeline_func:
            logger.warning(f"檔案 {descriptor} 找不到對應的管線 '{pipeline_name}'，跳過清洗。")
            # 即使沒有管線，如果解析成功，也可以考慮是否要儲存原始解析結果
            # 目前行為：沒有對應管線則不儲存到 staging
            return {'status': 'skipped_no_pipeline', 'message': f"找不到管線 '{pipeline_name}'", 'descriptor': descriptor}

        # logger.debug(f"檔案 {descriptor} 應用管線: {pipeline_name}")
        cleaned_df = pipeline_func(parsed_df, descriptor) #傳遞 descriptor 給 source 欄位

        if cleaned_df is None or cleaned_df.empty: # 管線可能返回 None 或空 DataFrame
            # logger.info(f"檔案 {descriptor} 經過管線 {pipeline_name} 清洗後為空，跳過。")
            return {'status': 'skipped_empty_after_clean', 'message': "數據清洗後為空", 'descriptor': descriptor}

        # 為 Parquet 檔案產生唯一的名稱，避免衝突
        # 使用 descriptor 的 hash，因為 descriptor 包含原始路徑和 ZIP 內路徑，比較唯一
        file_hash = hashlib.sha256(descriptor.encode('utf-8')).hexdigest()[:16]
        staging_file_path = os.path.join(staging_dir_path, f"{pipeline_name}_{file_hash}.parquet")

        # logger.debug(f"準備將 {descriptor} 寫入 Parquet: {staging_file_path} (行數: {len(cleaned_df)})")
        cleaned_df.to_parquet(staging_file_path, engine='pyarrow', index=False)
        # logger.info(f"檔案 {descriptor} 成功處理並儲存為 Parquet。")
        return {'status': 'success', 'rows_processed': len(cleaned_df), 'pipeline': pipeline_name, 'descriptor': descriptor, 'staged_file': staging_file_path}

    except Exception as e:
        logger.error(f"工人處理檔案 {descriptor} 時發生嚴重錯誤: {e}") # 移除 exc_info=True
        return {'status': 'error', 'message': f"{type(e).__name__}: {e}", 'descriptor': descriptor}
    finally:
        if hw_manager_ref: # 檢查是否存在 (雖然應該總是存在)
             hw_manager_ref.log_event_snapshot(f"處理後: {descriptor[:40]}...")


def run_parsing_stage(input_dir: str, staging_dir: str, format_map_path: str, hw_manager: HardwareManager) -> Tuple[bool, int, int, int, int]:
    logger.header("階段一: 解析原始檔至本地暫存區 (Parquet)")

    # 載入或初始化 format_map
    format_map = {}
    if os.path.exists(format_map_path):
        try:
            with open(format_map_path, 'r', encoding='utf-8') as f_map:
                format_map = json.load(f_map)
            logger.info(f"已成功從 {format_map_path} 載入格式地圖，包含 {len(format_map)} 筆配方。")
        except Exception as e_map_load:
            logger.warning(f"載入格式地圖 {format_map_path} 失敗: {e_map_load}。將建立新地圖。")
            format_map = {} # 確保是空字典
    else:
        logger.info("未找到現有格式地圖，將在執行過程中自動建立。")

    logger.section("掃描本地輸入目錄並建立工作清單...")
    jobs_to_process = []
    format_map_updated_in_session = False

    all_files_discovered = list(discover_files_recursively(input_dir))
    if not all_files_discovered:
        logger.warning(f"在輸入目錄 {input_dir} 中未發現任何檔案。")
        return False, 0, 0, 0, 0

    logger.info(f"在 {input_dir} 中發現 {len(all_files_discovered)} 個檔案/壓縮檔成員。")

    for descriptor, content_bytes in all_files_discovered:
        # 使用內容的 hash 作為 key，因為檔名可能重複，但內容決定格式
        content_hash = hashlib.sha256(content_bytes).hexdigest()

        recipe_for_file = format_map.get(content_hash)
        if not recipe_for_file: # 如果此內容的配方不在地圖中
            # logger.debug(f"內容雜湊 {content_hash} (來自 {descriptor}) 不在格式地圖中，嘗試動態判斷...")
            recipe_for_file = determine_parsing_recipe(content_bytes, descriptor)
            if recipe_for_file: # 如果成功判斷出配方
                format_map[content_hash] = recipe_for_file # 更新地圖
                format_map_updated_in_session = True
                # logger.info(f"動態學習: 為 '{descriptor[:70]}...' (雜湊: {content_hash[:8]}) 建立配方 -> {recipe_for_file.get('pipeline', 'unknown')}")
            else: # 如果無法判斷配方
                logger.warning(f"學習失敗: 無法為 '{descriptor[:70]}...' (雜湊: {content_hash[:8]}) 建立有效配方。將標記為未知。")
                format_map[content_hash] = {"parser": "unknown", "pipeline": "unknown", "args": {}} # 存入未知標記，避免重複判斷
                format_map_updated_in_session = True # 即使是未知，也算更新了地圖

        # 只有當配方有效且管線不是 "unknown" 時才加入處理任務
        if recipe_for_file and recipe_for_file.get("pipeline") != "unknown" and recipe_for_file.get("parser") not in ["unknown", "unknown_encoding"]:
            jobs_to_process.append((descriptor, content_bytes, recipe_for_file, staging_dir, hw_manager))
        else:
            logger.info(f"檔案 '{descriptor[:70]}...' 的配方為未知或無效，將跳過處理。配方: {recipe_for_file}")


    if not jobs_to_process:
        logger.warning("掃描後，沒有找到任何有效且可處理的檔案。請檢查輸入檔案或格式地圖。")
        # 即使沒有任務，如果 format_map 更新了，也應該返回 True
        if format_map_updated_in_session:
            try:
                with open(format_map_path, 'w', encoding='utf-8') as f_map_save:
                    json.dump(format_map, f_map_save, indent=4, ensure_ascii=False)
                logger.info(f"格式地圖已更新並儲存至 {format_map_path} (即使沒有處理任務)。")
            except Exception as e_map_save_empty:
                logger.error(f"儲存更新後的格式地圖 (空任務) 至 {format_map_path} 時失敗: {e_map_save_empty}")
        return format_map_updated_in_session, 0, 0, 0, (len(all_files_discovered) - len(jobs_to_process))

    logger.success(f"工作清單建立完畢，共 {len(jobs_to_process)} 個有效檔案待處理。")
    logger.section(f"啟動 {hw_manager.max_workers} 個工人進程，開始並行處理...")

    # 初始化統計數據
    stats = {'success': 0, 'skipped_empty_or_parse_fail': 0, 'skipped_no_pipeline':0, 'skipped_empty_after_clean':0, 'error': 0, 'rows': 0}

    processed_job_count = 0
    with concurrent.futures.ProcessPoolExecutor(max_workers=hw_manager.max_workers) as executor:
        future_to_job_desc = {executor.submit(worker_process_file, job_args): job_args[0] for job_args in jobs_to_process}

        for future in concurrent.futures.as_completed(future_to_job_desc):
            job_descriptor = future_to_job_desc[future]
            processed_job_count += 1
            logger.info(f"--- [進度 {processed_job_count}/{len(jobs_to_process)}] '{job_descriptor[:70]}...' ---")
            try:
                result = future.result()
                status_category = result.get('status', 'error') # 預設為 error

                if status_category == 'success':
                    stats['success'] += 1
                    stats['rows'] += result.get('rows_processed', 0)
                    logger.success(f"  ↳ 處理成功 ({result.get('pipeline', 'N/A')})，暫存 {result.get('rows_processed', 0):,} 筆記錄到 {os.path.basename(result.get('staged_file', 'N/A'))}。")
                elif status_category.startswith('skipped'):
                    stats[status_category] = stats.get(status_category, 0) + 1
                    logger.info(f"  ↳ 跳過處理: {result.get('message', '未知原因')}")
                else: # error
                    stats['error'] += 1
                    logger.error(f"  ↳ 處理失敗: {result.get('message', '未知錯誤')}")

            except Exception as exc: # Future 本身可能拋出例外 (例如 worker process 崩潰)
                stats['error'] += 1
                logger.error(f"處理任務 '{job_descriptor}' 時，Future 產生嚴重例外: {exc}") # 移除 exc_info=True

    logger.success(f"階段一所有檔案處理完畢。")
    total_skipped = stats['skipped_empty_or_parse_fail'] + stats['skipped_no_pipeline'] + stats['skipped_empty_after_clean']

    # 儲存更新後的 format_map
    if format_map_updated_in_session:
        try:
            with open(format_map_path, 'w', encoding='utf-8') as f_map_save:
                json.dump(format_map, f_map_save, indent=4, ensure_ascii=False)
            logger.info(f"格式地圖已更新並儲存至 {format_map_path}")
        except Exception as e_map_save:
            logger.error(f"儲存更新後的格式地圖至 {format_map_path} 時失敗: {e_map_save}")

    return format_map_updated_in_session, stats['rows'], stats['success'], stats['error'], total_skipped


def run_duckdb_loading_stage(db_file_path: str, staging_dir: str, hw_manager: HardwareManager, db_temp_dir: str) -> int:
    logger.header("階段二: 從本地暫存區高速載入至 DuckDB")

    if not os.path.exists(db_temp_dir):
        os.makedirs(db_temp_dir, exist_ok=True)
        logger.info(f"DuckDB 臨時目錄已建立: {db_temp_dir}")

    try:
        # 連接資料庫，如果不存在則會建立
        conn = duckdb.connect(database=db_file_path, read_only=False)
        logger.success(f"成功連接至 DuckDB 資料庫: {db_file_path}")

        # 設定 DuckDB 環境參數
        conn.execute(f"SET memory_limit = '{hw_manager.memory_limit_gb}GB';")
        conn.execute(f"SET threads = {hw_manager.max_workers};")
        conn.execute(f"SET temp_directory = '{db_temp_dir}';") # 設定臨時目錄
        logger.info(f"DuckDB 環境設定完畢 (Memory: {hw_manager.memory_limit_gb}GB, Threads: {hw_manager.max_workers}, TempDir: {db_temp_dir})")

        # 建立序列和表格 (如果不存在)
        for seq_sql in SEQUENCES.values(): conn.execute(seq_sql)
        for table_sql in TABLE_DEFINITIONS.values(): conn.execute(table_sql)
        logger.info("所有資料庫表格與序列 (SEQUENCE) 已確認或建立。")

    except Exception as e_db_init:
        logger.error(f"資料庫初始化或連接失敗 ({db_file_path}): {e_db_init}")
        return 0 # 返回0表示沒有記錄被加入

    # 查找所有在 staging 區的 Parquet 檔案
    all_staged_parquet_files = [os.path.join(staging_dir, f) for f in os.listdir(staging_dir) if f.endswith('.parquet')]
    if not all_staged_parquet_files:
        logger.warning(f"本地暫存區 {staging_dir} 中沒有找到任何 .parquet 檔案，無需載入。")
        conn.close()
        return 0

    logger.info(f"在暫存區發現 {len(all_staged_parquet_files)} 個 Parquet 檔案準備載入。")

    # 按目標表格名稱分組 Parquet 檔案
    files_by_target_table = {}
    for parquet_file_path in all_staged_parquet_files:
        # 檔名格式預期為: {pipeline_name}_{file_hash}.parquet
        # pipeline_name 即為目標表格名稱
        # 問題3修正: .split('_', 1)[0] -> .rsplit('_', 1)[0]
        target_table_name = os.path.basename(parquet_file_path).rsplit('_', 1)[0]
        if target_table_name not in files_by_target_table:
            files_by_target_table[target_table_name] = []
        files_by_target_table[target_table_name].append(parquet_file_path)

    total_rows_added_to_db = 0
    for table_name, list_of_parquet_files in files_by_target_table.items():
        if table_name not in TABLE_DEFINITIONS:
            logger.warning(f"從 Parquet 檔名解析出的目標表格 '{table_name}' 不在預定義的表格 ({list(TABLE_DEFINITIONS.keys())}) 中，將跳過這些檔案: {list_of_parquet_files}")
            continue

        logger.section(f"正在載入資料至 '{table_name}' 表格 (來源檔案數: {len(list_of_parquet_files)})")
        try:
            # 1. (可選) 移除唯一索引以加速大量插入，完成後再重建
            #    對於 DuckDB，ON CONFLICT DO NOTHING 通常也很快，但可以測試比較
            unique_index_name = f"idx_{table_name}_unique" # 假設索引命名規則
            if UNIQUE_INDICES.get(table_name): # 檢查是否有定義唯一索引
                try:
                    conn.execute(f"DROP INDEX IF EXISTS {unique_index_name};")
                    logger.info(f"  ↳ 已暫時移除索引 '{unique_index_name}' (如果存在)。")
                except Exception as e_drop_idx:
                    logger.warning(f"  ↳ 移除索引 '{unique_index_name}' 時發生非致命錯誤: {e_drop_idx} (可能索引不存在)")


            # 2. 將 Parquet 檔案載入到一個臨時 staging 表格
            #    使用 union_by_name=True 來處理可能的 schema 差異 (雖然理想情況下應該一致)
            #    使用 glob 模式讀取多個檔案
            parquet_files_glob_pattern = [f.replace("\\","/") for f in list_of_parquet_files] # DuckDB 需要正斜線

            conn.execute(f"CREATE OR REPLACE TEMP TABLE temp_staging_for_{table_name} AS SELECT * FROM read_parquet({parquet_files_glob_pattern}, union_by_name=True);")

            initial_count_in_temp = conn.execute(f"SELECT COUNT(*) FROM temp_staging_for_{table_name}").fetchone()[0]
            if initial_count_in_temp == 0:
                logger.info(f"  ↳ 臨時表 temp_staging_for_{table_name} 為空，跳過此表格的後續載入。")
                continue
            logger.success(f"  ↳ 已將 {len(list_of_parquet_files)} 個 Parquet 檔案 ({initial_count_in_temp:,} 筆記錄) 載入至臨時表 temp_staging_for_{table_name}。")

            # 3. 從臨時表去重並插入到目標表
            #    使用 ON CONFLICT DO NOTHING (基於唯一索引)
            #    或者，如果沒有預先移除索引，也可以先插入到另一個帶有 ROW_NUMBER() 的去重臨時表，再插入目標表

            # 獲取目標表和臨時表的欄位定義，以確保只插入共同存在的欄位 (除了id)
            target_table_cols_desc = conn.execute(f"DESCRIBE {table_name};").fetchall()
            temp_table_cols_desc = conn.execute(f"DESCRIBE temp_staging_for_{table_name};").fetchall()

            target_cols_set = {col_info[0] for col_info in target_table_cols_desc if col_info[0] != 'id'} # 不含 id
            temp_cols_set = {col_info[0] for col_info in temp_table_cols_desc}

            common_cols_for_insert = list(target_cols_set.intersection(temp_cols_set))
            if not common_cols_for_insert:
                logger.warning(f"  ↳ 表格 '{table_name}' 與其臨時表之間沒有共同欄位 (除了id)，無法插入數據。")
                continue

            common_cols_str = ", ".join([f'"{c}"' for c in common_cols_for_insert]) # 加引號以處理特殊字元/大小寫

            # 核心修正: 預先去重 (來自 Colab v8.0)
            # 這裡需要 UNIQUE_INDICES[table_name] 中的欄位列表
            unique_constraint_cols_str = ""
            if table_name in UNIQUE_INDICES:
                match = re.search(r'\((.*?)\)', UNIQUE_INDICES[table_name])
                if match:
                    unique_constraint_cols_str = match.group(1)

            if not unique_constraint_cols_str:
                logger.warning(f"  ↳ 表格 '{table_name}' 未定義唯一約束欄位，將不進行預先去重，直接嘗試插入。")
                # 如果沒有唯一約束欄位，直接從 temp_staging 插入，依賴 ON CONFLICT
                # 但 ON CONFLICT 需要主鍵或唯一約束，這裡的 'id' 是主鍵，但不是業務邏輯上的唯一性
                # 這種情況下，如果沒有其他唯一索引，可能會插入重複數據 (除了id不同)
                # 為了安全，如果沒有明確的業務唯一鍵，我們應該只做簡單插入，或者報錯
                # 暫時假設所有表都有合理的唯一性定義，或者 'id' 的序列能處理
                # 如果要嚴格去重，這裡需要一個 fallback 策略或報錯
                # 這裡先簡化：如果沒有 UNIQUE_INDICES，則不進行 ROW_NUMBER() 去重
                insert_from_table = f"temp_staging_for_{table_name}"
            else:
                # 進行 ROW_NUMBER() 去重
                dedup_temp_table_name = f"temp_deduped_for_{table_name}"
                dedup_sql = f"""
                    CREATE OR REPLACE TEMP TABLE {dedup_temp_table_name} AS
                    SELECT * FROM (
                        SELECT *, ROW_NUMBER() OVER (PARTITION BY {unique_constraint_cols_str} ORDER BY source DESC NULLS LAST) as rn
                        FROM temp_staging_for_{table_name}
                    ) WHERE rn = 1;
                """ # ORDER BY source DESC NULLS LAST 讓有 source 的優先，且 NULL source 排後面
                conn.execute(dedup_sql)
                insert_from_table = dedup_temp_table_name

                deduped_count = conn.execute(f"SELECT COUNT(*) FROM {insert_from_table}").fetchone()[0]
                logger.info(f"  ↳ 在臨時表中基於 ({unique_constraint_cols_str}) 去重完成，剩餘 {deduped_count:,} 筆唯一記錄。")
                if deduped_count == 0:
                    logger.info(f"  ↳ 去重後記錄為0，跳過插入。")
                    if UNIQUE_INDICES.get(table_name): # 重建索引
                        conn.execute(UNIQUE_INDICES[table_name])
                    continue


            # 插入數據，使用 ON CONFLICT DO NOTHING
            # 這裡的 id 由序列產生，其他共同欄位從臨時表選取
            insert_sql = f"""
            INSERT INTO {table_name} (id, {common_cols_str})
            SELECT nextval('seq_{table_name}'), {common_cols_str}
            FROM {insert_from_table}
            ON CONFLICT (id) DO NOTHING;
            """
            # 如果要基於業務鍵的衝突處理 (例如 UNIQUE_INDICES 中的欄位)
            # 則需要 ON CONFLICT (col1, col2) DO UPDATE SET ... 或 DO NOTHING
            # 但由於我們已經做了預先去重，理論上不應該有業務鍵衝突，除非是與DB中已有的數據衝突
            # 如果要處理與DB中已有數據的業務鍵衝突，則 UNIQUE_INDICES 需要先建立
            # 這裡先假設 UNIQUE_INDICES 主要用於最終的數據完整性保證，插入時依賴預先去重

            # 為了處理與資料庫中已存在數據的衝突 (基於業務唯一鍵)
            # 我們需要在插入前確保唯一索引存在，然後使用 ON CONFLICT (業務鍵) DO NOTHING
            if UNIQUE_INDICES.get(table_name):
                try:
                    conn.execute(UNIQUE_INDICES[table_name]) # 先嘗試建立索引
                    logger.info(f"  ↳ 已在 '{table_name}' 上確認/建立唯一性索引。")

                    # 使用業務鍵進行 ON CONFLICT
                    conflict_target_cols = re.search(r'\((.*?)\)', UNIQUE_INDICES[table_name]).group(1)
                    insert_sql_with_biz_conflict = f"""
                    INSERT INTO {table_name} (id, {common_cols_str})
                    SELECT nextval('seq_{table_name}'), {common_cols_str}
                    FROM {insert_from_table}
                    ON CONFLICT ({conflict_target_cols}) DO NOTHING;
                    """
                    conn.execute(insert_sql_with_biz_conflict)
                    logger.info(f"  ↳ 已執行基於業務鍵 ({conflict_target_cols}) 的去重插入操作。")

                except Exception as e_create_idx_early:
                    logger.warning(f"  ↳ 嘗試在插入前建立唯一索引 '{UNIQUE_INDICES[table_name]}' 失敗: {e_create_idx_early}。將使用基於ID的衝突處理。")
                    conn.execute(insert_sql) # 回退到基於 ID 的衝突處理
                    logger.info(f"  ↳ 已執行基於ID的衝突插入操作。")
            else: # 沒有定義唯一業務索引
                conn.execute(insert_sql)
                logger.info(f"  ↳ 已執行基於ID的衝突插入操作 (無業務唯一索引定義)。")


            # 獲取實際插入的行數 (這比較 tricky，DuckDB 的 INSERT ... ON CONFLICT 不直接返回影響行數)
            # 可以通過比較前後行數，但如果表很大，這可能較慢
            # 這裡我們用去重後的數量作為一個近似值
            rows_in_final_table_after = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
            # logger.info(f"  ↳ 表格 '{table_name}' 目前總行數: {rows_in_final_table_after:,}")
            # 實際增加的行數可以用 deduped_count (如果進行了去重) 或 initial_count_in_temp (如果未去重)
            # 但這不完全準確，因為 ON CONFLICT DO NOTHING 的影響
            # 這裡我們只記錄一個大概的 "新加入或更新" 的概念性數字
            # 為了簡化，我們使用去重後的數量 (如果進行了去重)
            current_op_rows = conn.execute(f"SELECT COUNT(*) FROM {insert_from_table}").fetchone()[0]
            total_rows_added_to_db += current_op_rows # 累加的是嘗試插入的唯一記錄數
            logger.success(f"  ↳ 向 '{table_name}' 表格嘗試加入 {current_op_rows:,} 筆唯一記錄。")

            # 4. (如果之前移除了) 重建唯一索引
            if UNIQUE_INDICES.get(table_name): # 再次確保索引存在
                try:
                    conn.execute(UNIQUE_INDICES[table_name])
                    logger.success(f"  ↳ 已在 '{table_name}' 上成功確認/重建唯一性索引。")
                except Exception as e_rebuild_idx:
                    logger.error(f"  ↳ 在 '{table_name}' 上重建唯一索引時發生錯誤: {e_rebuild_idx}")

            # 清理臨時表
            conn.execute(f"DROP TABLE IF EXISTS temp_staging_for_{table_name};")
            if unique_constraint_cols_str : # 如果創建了去重表
                conn.execute(f"DROP TABLE IF EXISTS {dedup_temp_table_name};")


        except Exception as e_load_table:
            logger.error(f"  ↳ 載入資料至 '{table_name}' 時發生嚴重錯誤: {e_load_table}") # 移除 exc_info=True

    conn.close()
    logger.success(f"資料庫操作完成並已關閉連線。總共嘗試加入約 {total_rows_added_to_db:,} 筆唯一記錄至各表格。")
    return total_rows_added_to_db


# ==============================================================================
# 主執行函數
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(description="TAIFEX 數據精煉廠：執行 ETL 作業，將原始數據載入 DuckDB。")
    parser.add_argument("--input-dir", required=True, help="包含原始數據檔案的輸入目錄路徑。")
    parser.add_argument("--db-output-dir", required=True, help="DuckDB 資料庫檔案及格式地圖的輸出目錄路徑。")
    parser.add_argument("--db-name", default="taifex_pipeline_analytics.duckdb", help="DuckDB 資料庫的檔案名稱 (預設: taifex_pipeline_analytics.duckdb)。")
    parser.add_argument("--temp-dir", default=None, help="DuckDB 及處理過程的臨時檔案目錄 (預設: 在 db-output-dir 下建立 'temp_pipeline_work')。")
    parser.add_argument("--max-workers", type=int, default=None, help="並行處理核心數 (預設: CPU核心數 * 0.8)。")
    parser.add_argument("--memory-limit-gb", type=int, default=None, help="DuckDB 記憶體預算 (GB) (預設: 系統記憶體 * 0.5)。")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], help="日誌級別 (預設: INFO)。")

    args = parser.parse_args()

    # 設定日誌級別
    global logger
    logger = SimpleLogger(log_level=args.log_level)

    logger.header("TAIFEX 數據精煉廠 v1.0 啟動")

    # 設定路徑
    input_dir = os.path.abspath(args.input_dir)
    db_output_dir = os.path.abspath(args.db_output_dir)
    db_file_name = args.db_name
    db_file_full_path = os.path.join(db_output_dir, db_file_name)
    format_map_full_path = os.path.join(db_output_dir, FORMAT_MAP_FILENAME) # 格式地圖與 DB 同目錄

    if args.temp_dir:
        temp_work_dir = os.path.abspath(args.temp_dir)
    else:
        temp_work_dir = os.path.join(db_output_dir, "temp_pipeline_work")

    local_staging_path = os.path.join(temp_work_dir, "staging_parquet") # Parquet 檔案的中間儲存區
    duckdb_temp_path_for_db = os.path.join(temp_work_dir, "duckdb_temp_files") # DuckDB 自身的臨時檔案

    # 建立必要的目錄
    os.makedirs(db_output_dir, exist_ok=True)
    os.makedirs(temp_work_dir, exist_ok=True)
    os.makedirs(local_staging_path, exist_ok=True)
    os.makedirs(duckdb_temp_path_for_db, exist_ok=True)

    logger.info(f"輸入目錄: {input_dir}")
    logger.info(f"資料庫輸出目錄: {db_output_dir}")
    logger.info(f"資料庫檔案: {db_file_full_path}")
    logger.info(f"格式地圖檔案: {format_map_full_path}")
    logger.info(f"工作臨時目錄: {temp_work_dir}")
    logger.info(f"  ↳ Parquet 暫存區: {local_staging_path}")
    logger.info(f"  ↳ DuckDB 臨時檔案區: {duckdb_temp_path_for_db}")


    hw_manager = HardwareManager(user_max_workers=args.max_workers, user_memory_limit_gb=args.memory_limit_gb)
    hw_manager.display_initial_dashboard()

    start_time_total = time.time()

    try:
        # 階段一：解析與暫存
        map_updated, rows_staged, success_files, error_files, skipped_files = run_parsing_stage(
            input_dir=input_dir,
            staging_dir=local_staging_path,
            format_map_path=format_map_full_path,
            hw_manager=hw_manager
        )
        logger.header(f"✅ 階段一執行完畢 ✅")
        logger.success(f"總結: 成功 {success_files} 個檔案，失敗 {error_files} 個，跳過 {skipped_files} 個。")
        logger.success(f"共 {rows_staged:,} 筆數據記錄被成功解析並寫入 Parquet 暫存區。")
        if map_updated:
            logger.info("格式地圖已在本輪執行中更新。")

        # 階段二：載入至 DuckDB
        if success_files > 0 or rows_staged > 0 : # 只有在有東西可以載入時才執行
            rows_added_db = run_duckdb_loading_stage(
                db_file_path=db_file_full_path,
                staging_dir=local_staging_path,
                hw_manager=hw_manager,
                db_temp_dir=duckdb_temp_path_for_db
            )
            logger.header(f"🎉 階段二執行完畢 🎉")
            logger.success(f"總結: 資料庫共嘗試加入約 {rows_added_db:,} 筆唯一數據記錄。")
        else:
            logger.info("階段一未產生可供載入的數據，跳過階段二。")
            rows_added_db = 0


        # (可選) 清理 staging_parquet 目錄，因為數據已載入 DuckDB
        # 如果希望保留 Parquet 檔案以供其他用途，則不要清理
        # logger.info(f"正在清理 Parquet 暫存區: {local_staging_path}")
        # shutil.rmtree(local_staging_path) # 注意：這會刪除所有 parquet 檔案
        # os.makedirs(local_staging_path, exist_ok=True) # 重新建立空目錄

    except Exception as e_main:
        logger.error(f"主流程發生未預期的嚴重錯誤: {e_main}") # 移除 exc_info=True
    finally:
        end_time_total = time.time()
        total_duration_seconds = end_time_total - start_time_total
        logger.header(f"🏁 精煉廠全部流程執行完畢 (總耗時: {total_duration_seconds:.2f} 秒) 🏁")
        logger.info(f"最終資料庫檔案位於: {db_file_full_path}")
        logger.info(f"最終格式地圖位於: {format_map_full_path}")

        # 提示：如果需要在 Colab 或類似環境下載結果，使用者需要手動處理
        # 例如，使用 files.download(db_file_full_path)

if __name__ == "__main__":
    main()
