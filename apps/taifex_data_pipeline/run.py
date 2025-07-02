# -*- coding: utf-8 -*-
# 精煉廠主執行檔 (v16.0 Hotfix 後的批次掃描版本 - 修正 institutional_investors)
import os
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
from typing import Generator, Tuple, Dict, Any, Optional, List
import concurrent.futures

import pandas as pd
import psutil
import pyarrow
import duckdb
import pytz

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

def discover_files_recursively(root_path: str) -> Generator[Tuple[str, bytes], None, None]:
    if not os.path.exists(root_path): logger.warning(f"輸入路徑不存在: {root_path}"); return
    items_to_scan = [(os.path.basename(root_path) if os.path.isfile(root_path) else "", root_path)]
    if os.path.isdir(root_path): items_to_scan = [(name, os.path.join(root_path, name)) for name in sorted(os.listdir(root_path))]
    processed_descriptors = set()
    while items_to_scan:
        descriptor_base, current_path = items_to_scan.pop(0)
        if current_path in processed_descriptors: continue
        processed_descriptors.add(current_path)
        try:
            if os.path.isdir(current_path):
                new_items = [(f"{descriptor_base}/{name}" if descriptor_base else name, os.path.join(current_path, name)) for name in sorted(os.listdir(current_path))]
                items_to_scan = new_items + items_to_scan; continue
            with open(current_path, 'rb') as f_content: content_bytes = f_content.read()
            final_descriptor = descriptor_base if descriptor_base else os.path.basename(current_path)
            import zipfile
            if current_path.lower().endswith('.zip') and zipfile.is_zipfile(io.BytesIO(content_bytes)):
                with zipfile.ZipFile(io.BytesIO(content_bytes), 'r') as zf:
                    for member_info in sorted(zf.infolist(), key=lambda mi: mi.filename):
                        if member_info.is_dir() or '__MACOSX' in member_info.filename: continue
                        with zf.open(member_info) as member_file: member_content_bytes = member_file.read()
                        yield f"{final_descriptor} -> {member_info.filename}", member_content_bytes
            else: yield final_descriptor, content_bytes
        except Exception as e: logger.warning(f"讀取/解壓 '{current_path}' (描述: {descriptor_base}) 失敗: {e}")

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

def pipeline_daily_ohlc(df: pd.DataFrame, source: str) -> pd.DataFrame:
    map_ = {'交易日期':'trading_date','契約':'product_id','商品代號':'product_id','到期月份_週別':'expiry_month','到期月份／週別':'expiry_month','履約價':'strike_price','買賣權':'option_type','開盤價':'open','最高價':'high','最低價':'low','收盤價':'close','成交量':'volume','結算價':'settlement_price','未沖銷契約數':'open_interest','交易時段':'trading_session','漲跌價':'change','漲跌percent':'change_percent'}
    df = _clean_and_prepare_df(df, ['trading_date','product_id','close'], map_)
    df['trading_date'] = pd.to_datetime(df['trading_date'], errors='coerce').dt.strftime('%Y-%m-%d')
    if 'option_type' in df.columns: df['option_type'] = df['option_type'].astype(str).str.strip().map({'買權':'C','賣權':'P','C':'C','P':'P'})
    df['trading_session'] = df.get('trading_session', pd.Series(index=df.index, dtype='str')).fillna('Regular').astype(str).str.strip().replace({'盤後':'AfterHours','一般':'Regular','0':'Regular','1':'AfterHours'})
    num_cols = ['open','high','low','close','volume','settlement_price','open_interest','strike_price','change','change_percent']
    for col in num_cols:
        if col in df.columns: df[col] = pd.to_numeric(df[col].astype(str).str.replace(',','').replace('-','NaN'), errors='coerce')
    df['source'] = source; return df.dropna(subset=['trading_date','product_id','close'])

def pipeline_tick_data(df: pd.DataFrame, source: str) -> pd.DataFrame:
    # Hotfix applied for "成交數量(B+S)"
    map_ = {
        '成交日期':'trade_date','商品代號':'product_id','到期月份_週別':'expiry_month',
        '履約價':'strike_price','買賣權':'option_type','成交時間':'trade_time',
        '成交價格':'price',
        '成交數量_買賣別_':'volume_with_side',
        '成交數量_b_or_s_':'volume_with_side',
        '成交數量_bpluss_': 'volume', # 修正: (B+S) -> _bpluss_
        '成交數量':'volume'
    }
    df = _clean_and_prepare_df(df, ['trade_date','trade_time','price','volume'], map_)
    try: df['trade_datetime'] = pd.to_datetime(df['trade_date'].astype(str)+' '+df['trade_time'].astype(str), format='%Y%m%d %H:%M:%S.%f', errors='coerce')
    except ValueError: df['trade_datetime'] = pd.to_datetime(df['trade_date'].astype(str)+' '+df['trade_time'].astype(str), format='%Y%m%d %H:%M:%S', errors='coerce')
    df['trade_datetime'] = df['trade_datetime'].dt.strftime('%Y-%m-%d %H:%M:%S.%f')
    if 'option_type' in df.columns: df['option_type'] = df['option_type'].astype(str).str.strip().map({'C':'C','P':'P','買':'C','賣':'P'})
    if 'volume' not in df.columns and 'volume_with_side' in df.columns:
        df['volume'] = df['volume_with_side'].astype(str).str.extract(r'(\d+)').iloc[:,0]
    num_cols = ['strike_price','price','volume']
    for col in num_cols:
        if col in df.columns: df[col] = pd.to_numeric(df[col], errors='coerce')
    df['source'] = source; return df.dropna(subset=['trade_datetime','product_id','price','volume'])

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

def pipeline_fx_rates(df: pd.DataFrame, source: str) -> pd.DataFrame:
    map_ = {'日期':'data_date','美元／新台幣':'usd_twd','人民幣／新台幣':'cny_twd','歐元／美元':'eur_usd','美元／日圓':'usd_jpy','英鎊／美元':'gbp_usd','澳幣／美元':'aud_usd','美元／港幣':'usd_hkd','美元／人民幣':'usd_cny','美元／南非幣':'usd_zar','紐幣／美元':'nzd_usd'}
    df = _clean_and_prepare_df(df,['data_date','usd_twd'],map_)
    df['data_date'] = pd.to_datetime(df['data_date'], errors='coerce').dt.strftime('%Y-%m-%d')
    for col in map_.values():
        if col != 'data_date' and col in df.columns: df[col] = pd.to_numeric(df[col], errors='coerce')
    df['source'] = source; return df.dropna(subset=['data_date'])

def pipeline_pcr(df: pd.DataFrame, source: str) -> pd.DataFrame:
    map_ = {'日期':'data_date','賣權成交量':'put_volume','買權成交量':'call_volume','買賣權成交量比率percent':'pcr_volume','賣權未平倉量':'put_oi','買權未平倉量':'call_oi','買賣權未平倉量比率percent':'pcr_oi'}
    df = _clean_and_prepare_df(df,['data_date'],map_)
    df['data_date'] = pd.to_datetime(df['data_date'], errors='coerce').dt.strftime('%Y-%m-%d')
    num_cols = ['put_volume','call_volume','pcr_volume','put_oi','call_oi','pcr_oi']
    for col in num_cols:
        if col in df.columns: df[col] = pd.to_numeric(df[col].astype(str).str.replace('%','').str.replace(',',''), errors='coerce')
        else: df[col] = pd.NA
    df['source'] = source; return df.dropna(subset=['data_date'])

PIPELINE_MAP = {"daily_ohlc":pipeline_daily_ohlc,"institutional_investors":pipeline_institutional_investors,"tick_data":pipeline_tick_data,"fx_rates":pipeline_fx_rates,"pcr":pipeline_pcr,"unknown":lambda df,src: df}

def worker_process_file(args: Tuple[str, bytes, Dict, str, Any]) -> Dict[str, Any]:
    desc, bytes_data, recipe, staging_path, hw_mgr = args
    try:
        df = parse_with_recipe(bytes_data, recipe, desc)
        if df is None or df.empty: return {'status':'skipped_parse_empty','descriptor':desc}
        p_name = recipe.get("pipeline","unknown"); p_func = PIPELINE_MAP.get(p_name)
        if not p_func: return {'status':'skipped_no_pipeline','descriptor':desc}
        df = p_func(df, desc)
        if df is None or df.empty: return {'status':'skipped_clean_empty','descriptor':desc}
        f_hash = hashlib.sha256(desc.encode()).hexdigest()[:16]
        out_path = os.path.join(staging_path, f"{p_name}_{f_hash}.parquet")
        df.to_parquet(out_path, index=False); return {'status':'success','rows':len(df),'pipeline':p_name,'file':out_path,'descriptor':desc}
    except Exception as e: logger.error(f"Worker error ({desc}): {e}"); return {'status':'error','descriptor':desc,'error_msg':str(e)}
    finally:
        if hw_mgr: hw_mgr.log_event_snapshot(f"Processed: {desc[:30]}")

def run_parsing_stage(input_dir:str, staging_dir:str, format_map_path:str, hw_mgr:HardwareManager) -> Tuple[bool,int,int,int,int]:
    logger.header("Phase 1: Parse raw files to local staging (Parquet)")
    f_map = {};
    if os.path.exists(format_map_path):
        try:
            with open(format_map_path,'r',encoding='utf-8') as f: f_map=json.load(f)
            logger.info(f"Loaded format map: {len(f_map)} entries.")
        except Exception as e: logger.warning(f"Failed to load format map: {e}")
    else: logger.info("No existing format map found, will create new.")

    jobs, map_updated = [], False
    all_raw_files = list(discover_files_recursively(input_dir))
    if not all_raw_files: logger.warning(f"No files found in {input_dir}"); return False,0,0,0,0
    logger.info(f"Found {len(all_raw_files)} raw files/members to process.")

    for desc, bytes_data in all_raw_files:
        c_hash = hashlib.sha256(bytes_data).hexdigest()
        recipe = f_map.get(c_hash)
        if not recipe:
            recipe = determine_parsing_recipe(bytes_data, desc)
            if recipe: f_map[c_hash] = recipe; map_updated = True; logger.info(f"Learned recipe for {desc[:50]} -> {recipe.get('pipeline')}")
            else: f_map[c_hash] = {"parser":"unknown","pipeline":"unknown","args":{}}; map_updated=True; logger.warning(f"Failed to learn recipe for {desc[:50]}")
        if recipe and recipe.get("pipeline") != "unknown" and recipe.get("parser") not in ["unknown","unknown_encoding"]:
            jobs.append((desc, bytes_data, recipe, staging_dir, hw_mgr))
        else: logger.info(f"Skipping {desc[:50]} due to unknown/invalid recipe.")

    if not jobs:
        logger.warning("No valid jobs to process after recipe determination.")
        if map_updated:
            try:
                with open(format_map_path,'w',encoding='utf-8') as f: json.dump(f_map,f,indent=4,ensure_ascii=False)
                logger.info("Format map updated (even with no jobs).")
            except Exception as e: logger.error(f"Failed to save format map (no jobs): {e}")
        return map_updated,0,0,0,len(all_raw_files)

    logger.success(f"Created {len(jobs)} processing jobs. Starting parallel execution with {hw_mgr.max_workers} workers.")
    stats = {'s':0,'spe':0,'snp':0,'sce':0,'e':0,'r':0}

    with concurrent.futures.ProcessPoolExecutor(max_workers=hw_mgr.max_workers) as executor:
        futures = {executor.submit(worker_process_file, j): j[0] for j in jobs}
        for i, future in enumerate(concurrent.futures.as_completed(futures)):
            desc = futures[future]; logger.info(f"--- Progress {i+1}/{len(jobs)}: {desc[:60]} ---")
            try:
                res = future.result(); stat = res.get('status','e')
                if stat=='success': stats['s']+=1; stats['r']+=res.get('rows',0); logger.success(f"  -> OK ({res.get('pipeline')}), {res.get('rows'):,} rows to {os.path.basename(res.get('file','N/A'))}")
                elif stat.startswith('skipped'): cat=stat.replace('skipped_','s'); stats[cat[0:3]]+=1; logger.info(f"  -> Skipped: {res.get('message')}")
                else: stats['e']+=1; logger.error(f"  -> Error: {res.get('error_msg')}")
            except Exception as e: stats['e']+=1; logger.error(f"  -> Future for {desc} failed: {e}")

    logger.success("Phase 1 (Parsing) complete.")
    if map_updated:
        try:
            with open(format_map_path,'w',encoding='utf-8') as f: json.dump(f_map,f,indent=4,ensure_ascii=False)
            logger.info(f"Format map saved to {format_map_path}")
        except Exception as e: logger.error(f"Failed to save format map: {e}")
    return map_updated, stats['r'], stats['s'], stats['e'], sum(stats[k] for k in ['spe','snp','sce'])

def run_duckdb_loading_stage(db_path:str, staging_dir:str, hw_mgr:HardwareManager, db_tmp_dir:str) -> int:
    logger.header("Phase 2: Load from Parquet to DuckDB")
    os.makedirs(db_tmp_dir, exist_ok=True)
    try:
        conn = duckdb.connect(db_path); conn.execute(f"SET memory_limit='{hw_mgr.memory_limit_gb}GB'; SET threads={hw_mgr.max_workers}; SET temp_directory='{db_tmp_dir}';")
        for sql in SEQUENCES.values(): conn.execute(sql)
        for sql in TABLE_DEFINITIONS.values(): conn.execute(sql)
        logger.info("DB schema initialized.")
    except Exception as e: logger.error(f"DB init failed: {e}"); return 0

    parquets = [os.path.join(staging_dir,f) for f in os.listdir(staging_dir) if f.endswith('.parquet')]
    if not parquets: logger.warning(f"No Parquet files in {staging_dir}"); conn.close(); return 0

    files_by_table = {}
    for pq_f in parquets:
        tbl_name = os.path.basename(pq_f).rsplit('_',1)[0]
        if tbl_name not in files_by_table: files_by_table[tbl_name] = []
        files_by_table[tbl_name].append(pq_f)

    total_added = 0
    for tbl, files in files_by_table.items():
        if tbl not in TABLE_DEFINITIONS: logger.warning(f"Table '{tbl}' not defined, skipping."); continue
        logger.section(f"Loading to '{tbl}' from {len(files)} Parquets")
        try:
            idx_sql = UNIQUE_INDICES.get(tbl)
            if idx_sql: conn.execute(f"DROP INDEX IF EXISTS idx_{tbl}_unique;")

            pq_glob = [f.replace("\\","/") for f in files]
            conn.execute(f"CREATE OR REPLACE TEMP TABLE tmp_load AS SELECT * FROM read_parquet({pq_glob}, union_by_name=True);")
            cnt = conn.execute("SELECT COUNT(*) FROM tmp_load").fetchone()[0]
            if cnt==0: logger.info("  -> Temp table empty, skipping."); continue
            logger.success(f"  -> Loaded {cnt:,} rows to temp table.")

            uq_cols_str = ""
            if idx_sql: match=re.search(r'\((.*?)\)',idx_sql); uq_cols_str=match.group(1) if match else ""

            from_tbl = "tmp_load"
            if uq_cols_str:
                from_tbl = "tmp_deduped"; conn.execute(f"CREATE OR REPLACE TEMP TABLE {from_tbl} AS SELECT * FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY {uq_cols_str} ORDER BY source DESC NULLS LAST) rn FROM tmp_load) WHERE rn=1;")
                dedup_cnt = conn.execute(f"SELECT COUNT(*) FROM {from_tbl}").fetchone()[0]
                logger.info(f"  -> Deduped to {dedup_cnt:,} rows on ({uq_cols_str})")
                if dedup_cnt==0: conn.execute(f"DROP TABLE IF EXISTS {from_tbl};"); continue

            target_cols = {r[0] for r in conn.execute(f"DESCRIBE {tbl}").fetchall() if r[0]!='id'}
            src_cols = {r[0] for r in conn.execute(f"DESCRIBE {from_tbl}").fetchall()}
            ins_cols = list(target_cols.intersection(src_cols))
            if not ins_cols: logger.warning("  -> No common columns to insert."); continue

            ins_cols_q = ", ".join(f'"{c}"' for c in ins_cols)
            sql = f"INSERT INTO {tbl} (id, {ins_cols_q}) SELECT nextval('seq_{tbl}'), {ins_cols_q} FROM {from_tbl}"
            if idx_sql and uq_cols_str: conn.execute(idx_sql); sql+=f" ON CONFLICT ({uq_cols_str}) DO NOTHING;"
            else: sql+=" ON CONFLICT (id) DO NOTHING;" # Assuming id is PK

            conn.execute(sql)
            attempted = conn.execute(f"SELECT COUNT(*) FROM {from_tbl}").fetchone()[0]
            total_added += attempted; logger.success(f"  -> Attempted to add {attempted:,} unique rows to '{tbl}'.")
            if idx_sql: conn.execute(idx_sql) # Re-ensure index
            conn.execute("DROP TABLE IF EXISTS tmp_load;")
            if uq_cols_str: conn.execute(f"DROP TABLE IF EXISTS {from_tbl};")
        except Exception as e: logger.error(f"  -> Error loading to '{tbl}': {e}")
    conn.close(); logger.success(f"DB operations complete. Attempted to add ~{total_added:,} rows."); return total_added

def main():
    parser = argparse.ArgumentParser(description="TAIFEX Pipeline (Batch v16.0 Hotfix Applied)")
    parser.add_argument("--input-dir", required=True); parser.add_argument("--db-output-dir", required=True)
    parser.add_argument("--db-name", default="taifex_batch_analytics_hotfixed.duckdb")
    parser.add_argument("--temp-dir", default=None); parser.add_argument("--max-workers",type=int,default=None)
    parser.add_argument("--memory-limit-gb",type=int,default=None); parser.add_argument("--log-level",default="INFO")
    args = parser.parse_args()

    global logger; logger = SimpleLogger(log_level=args.log_level)
    logger.header("TAIFEX Pipeline (Batch v16.0 Hotfix Applied) Starting")

    db_out_dir = os.path.abspath(args.db_output_dir)
    db_fpath = os.path.join(db_out_dir, args.db_name)
    fmt_map_fpath = os.path.join(db_out_dir, FORMAT_MAP_FILENAME)
    tmp_dir = os.path.abspath(args.temp_dir) if args.temp_dir else os.path.join(db_out_dir, "temp_pipeline_batch_hotfixed")
    staging_p = os.path.join(tmp_dir, "staging_parquet")
    duckdb_tmp_p = os.path.join(tmp_dir, "duckdb_temp")
    for p in [db_out_dir,tmp_dir,staging_p,duckdb_tmp_p]: os.makedirs(p,exist_ok=True)

    hw_mgr = HardwareManager(args.max_workers, args.memory_limit_gb); hw_mgr.display_initial_dashboard()
    t_start = time.time()
    try:
        updated,r_staged,s_files,e_files,sk_files = run_parsing_stage(os.path.abspath(args.input_dir),staging_p,fmt_map_fpath,hw_mgr)
        logger.header("Phase 1 Summary"); logger.success(f"Files: {s_files} success, {e_files} error, {sk_files} skipped. Rows staged: {r_staged:,}.")
        if updated: logger.info("Format map updated.")
        if s_files > 0 or r_staged > 0:
            added_db = run_duckdb_loading_stage(db_fpath,staging_p,hw_mgr,duckdb_tmp_p)
            logger.header("Phase 2 Summary"); logger.success(f"~{added_db:,} unique rows attempted to load to DB.")
        else: logger.info("Phase 1 produced no data, skipping Phase 2.")
    except Exception as e: logger.error(f"Main process error: {e}")
    finally: logger.header(f"Pipeline finished. Total time: {time.time()-t_start:.2f}s. DB: {db_fpath}")

if __name__ == "__main__":
    main()
