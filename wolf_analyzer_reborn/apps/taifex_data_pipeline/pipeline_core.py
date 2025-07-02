# -*- coding: utf-8 -*-
# @title 🚀 高適應性期交所數據整合管道 v8.0 (核心邏輯移植版)

# --- 標準函式庫 ---
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
import logging # <--- 添加導入
# import html # 原腳本導入但未使用，暫時移除
from datetime import datetime
from typing import Generator, Tuple, Dict, Any, Optional, List
import concurrent.futures

# --- 第三方函式庫 (假設由 requirements.txt 管理安裝) ---
import pandas as pd
import psutil
import pyarrow
import duckdb
import pytz
# try: from pynvml import * # 移除 Colab 特定的 GPU 監控，或使其可選且有回退
# except ImportError: pass


# ==============================================================================
# 日誌與硬體管理 (從 Colab 腳本移植並調整)
# ==============================================================================
class DualLogger:
    def __init__(self, tz_str: str = 'Asia/Taipei', log_to_console: bool = True, log_stream: Optional[io.StringIO] = None):
        self.tz = pytz.timezone(tz_str)
        self.log_to_console = log_to_console
        self.html_mode = False # 在非 Colab 環境通常不使用 HTML

        # 允許可選的外部 log_stream 用於捕獲日誌 (例如測試)
        self._log_stream = log_stream if log_stream is not None else io.StringIO()
        self.lock = threading.Lock()

        # 設定基礎的 logging，以便在 console 和 stream 中都能看到
        self.logger = logging.getLogger("PipelineCore")
        if not self.logger.handlers:
            self.logger.setLevel(logging.INFO)

            # Console handler (stdout)
            if self.log_to_console:
                ch = logging.StreamHandler(sys.stdout)
                ch_formatter = logging.Formatter('%(asctime)s %(levelname)-8s %(message)s', '%Y-%m-%d %H:%M:%S')
                ch.setFormatter(ch_formatter)
                self.logger.addHandler(ch)

            # String IO handler (用於 get_log_content)
            # String IO handler 現在由 self._log_stream 和 _log_plain 處理，此處 logger 實例主要用於控制台
            # 如果也想讓 logging 模塊的輸出進入 _log_stream，可以再加一個 handler
            # sh = logging.StreamHandler(self._log_stream_for_logging_module)
            # sh_formatter = logging.Formatter(...)
            # sh.setFormatter(sh_formatter)
            # self.logger.addHandler(sh)


    def _get_timestamp(self) -> str: return datetime.now(self.tz).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]

    def _log_plain(self, m: str, p: str):
        # 供內部使用，寫入 self._log_stream
        with self.lock:
            self._log_stream.write(f"{self._get_timestamp()} {p} {m.strip()}\n")

    def _console_log(self, message: str, level: int = logging.INFO, prefix: str = ""):
        log_message = f"{prefix}{message}"
        if self.log_to_console:
            self.logger.log(level, log_message)
        # 同時寫入內部 stream 供 get_log_content 使用
        level_name = logging.getLevelName(level)
        self._log_plain(message, f"[{level_name:<8}]")


    def info(self, m): self._console_log(m, logging.INFO, "⚪ ")
    def success(self, m): self._console_log(m, logging.INFO, "✅ ") # INFO level, but with success icon
    def warning(self, m): self._console_log(m, logging.WARNING, "⚠️ ")
    def error(self, m): self._console_log(m, logging.ERROR, "❌ ")

    def header(self, m):
        formatted_header = f"\n{'='*80}\n=== {m.strip()} ===\n{'='*80}\n"
        if self.log_to_console:
            self.logger.info(formatted_header) # 使用 logger.info 打印，保持格式
        self._log_plain(f"=== {m.strip()} ===", "[HEADER] ")


    def section(self, m):
        formatted_section = f"\n--- {m.strip()} ---\n"
        if self.log_to_console:
             self.logger.info(formatted_section)
        self._log_plain(f"--- {m.strip()} ---", "[SECTION]")

    def hw_log(self, m, p="[HW_MONITOR]"):
        self._console_log(m, logging.DEBUG, "⏱️ ") # 硬體日誌通常設為 DEBUG
        self._log_plain(m, p)


    def get_log_content(self) -> str:
        with self.lock: return self._log_stream.getvalue()

# logger = DualLogger() # 實例化將由 run.py 控制或在需要時創建

class HardwareManager:
    def __init__(self, logger_instance: DualLogger):
        self.logger = logger_instance
        self.cpu_cores = os.cpu_count() or 2
        self.total_ram_gb = psutil.virtual_memory().total / (1024**3)
        # 調整 max_workers 和 memory_limit_gb 的預設值，使其更通用
        self.max_workers = max(1, round(self.cpu_cores * 0.8)) # 稍微降低以避免資源競爭
        self.memory_limit_gb = int(self.total_ram_gb * 0.5) # 更保守的記憶體限制
        self.gpu_detected = False # 預設為 False，除非有明確的非 Colab GPU 檢測邏輯

    def get_status_line(self) -> str:
        cpu_percent = psutil.cpu_percent()
        ram = psutil.virtual_memory()
        disk_usage = "/" # 監控根目錄的磁碟使用情況，或由參數指定
        try:
            disk = psutil.disk_usage(disk_usage)
            disk_str = f"Disk ({disk_usage}): {disk.percent:.1f}%"
        except FileNotFoundError:
            disk_str = f"Disk ({disk_usage}): N/A"

        line = f"CPU: {cpu_percent:.1f}% | RAM: {ram.percent:.1f}% ({ram.used/(1024**3):.2f}/{self.total_ram_gb:.2f} GB) | {disk_str}"
        return line

    def display_initial_dashboard(self):
        self.logger.header("硬體狀態儀表板 (啟動時)")
        # 使用 logger 打印信息，而不是 display(HTML)
        cpu_percent_i = psutil.cpu_percent(interval=0.1) # 短間隔獲取一次
        ram_i = psutil.virtual_memory()

        disk_usage_path = "/"
        try:
            disk_i = psutil.disk_usage(disk_usage_path)
            disk_status_str = f"{disk_i.used / (1024**3):.2f} GB / {disk_i.total / (1024**3):.2f} GB ({disk_i.percent:.1f}%)"
        except FileNotFoundError:
            disk_status_str = "N/A (路徑錯誤)"

        status_lines = [
            f"✅ CPU: {cpu_percent_i:.1f}% 使用率 (總核心數: {self.cpu_cores})",
            f"✅ 記憶體 (RAM): {ram_i.used / (1024**3):.2f} GB / {self.total_ram_gb:.2f} GB ({ram_i.percent:.1f}%)",
            f"✅ 本地磁碟 ({disk_usage_path}): {disk_status_str}"
        ]
        # GPU 檢測部分已移除 pynvml，保持簡單
        status_lines.append("⚪ GPU: 未在本移植版本中啟用詳細監控。")

        for line in status_lines:
            self.logger.info(line)

        self.logger.section("自動化執行參數")
        self.logger.info(f"⚙️ 並行處理核心數 (max_workers): {self.max_workers}")
        self.logger.info(f"⚙️ DuckDB 記憶體預算 (memory_limit): {self.memory_limit_gb} GB")


    def log_event_snapshot(self, event_name: str):
        self.logger.hw_log(f"[{event_name}] {self.get_status_line()}", "[HW_SNAPSHOT]")

class RealtimeHardwareMonitor: # 可選的高頻監控
    def __init__(self, logger_instance: DualLogger, hw_manager_instance: HardwareManager, interval: int = 8):
        self._logger, self._hw_manager, self._interval = logger_instance, hw_manager_instance, interval
        self._stop_event, self._thread = threading.Event(), threading.Thread(target=self._run, daemon=True)
        self._is_running = False

    def _run(self):
        while not self._stop_event.is_set():
            self._logger.hw_log(self._hw_manager.get_status_line())
            # 使用 is_set() 檢查停止事件，允許更快的退出
            if self._stop_event.wait(self._interval): # 等待 interval 秒或直到事件被設置
                break

    def start(self):
        if not self._is_running:
            self._logger.info("啟動高頻率即時硬體監控...")
            self._stop_event.clear()
            self._thread.start()
            self._is_running = True
        else:
            self._logger.info("硬體監控已在運行中。")

    def stop(self):
        if self._is_running and self._thread.is_alive():
            self._logger.info("停止高頻率即時硬體監控...")
            self._stop_event.set()
            self._thread.join(timeout=self._interval + 1) # 等待線程結束
            if self._thread.is_alive():
                self._logger.warning("硬體監控線程未能及時停止。")
            self._is_running = False
            # 重置線程以便可以重新啟動 (如果需要)
            self._thread = threading.Thread(target=self._run, daemon=True)
        else:
            self._logger.info("硬體監控未運行或線程已結束。")


# ==============================================================================
# 常數與核心配置 (從 Colab 腳本移植)
# 路徑常數將由 run.py 傳遞或基於 run.py 的參數動態構建
# 此處保留的是與數據庫和檔案格式相關的常數
# ==============================================================================
DB_FILE_NAME, FORMAT_MAP_FILENAME = "taifex_analytics_v8.0.duckdb", "format_map.json"
# LOG_FILE_NAME 將由 run.py 基於時間戳生成

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

# ---- 為 FWF Tick Data 硬編碼的解析參數 ----
TICK_DATA_FWF_COLUMNS = ['成交日期', '商品代號', '到期月份/週別', '履約價', '買賣權', '成交時間', '成交價格', '成交數量']
TICK_DATA_FWF_COLSPECS = [
    (0, 8), (9, 16), (17, 29), (30, 37), (38, 40),
    (41, 53), (54, 61), (62, None)
]
# 確保 SKIPROWS 跳過表頭和分隔線 '---'
TICK_DATA_FWF_SKIPROWS = 2


# ==============================================================================
# 核心功能模組 (檔案處理 & 數據清洗)
# ==============================================================================
def discover_files_recursively(root_path: str, logger: DualLogger) -> Generator[Tuple[str, bytes], None, None]:
    if not os.path.exists(root_path):
        logger.warning(f"指定的輸入路徑不存在: {root_path}")
        return
    if not os.path.isdir(root_path): # 如果不是目錄，則直接處理該檔案
        try:
            with open(root_path, 'rb') as f:
                content = f.read()
            # 如果是 ZIP，仍需解壓
            import zipfile # 延後導入
            if zipfile.is_zipfile(io.BytesIO(content)):
                with zipfile.ZipFile(io.BytesIO(content), 'r') as z:
                    for n in sorted(z.namelist()):
                        if n.endswith('/') or '__MACOSX' in n: continue
                        with z.open(n) as i:
                            yield f"{os.path.basename(root_path)} -> {n}", i.read()
            else: # 非 ZIP 的單一檔案
                yield os.path.basename(root_path), content
        except Exception as e:
            logger.warning(f"讀取或解壓縮單一檔案 '{root_path}' 時出錯: {e}")
        return


    items = [(os.path.basename(root_path), root_path)] # 初始 descriptor, path

    # 使用 os.walk 實現更簡潔的遞歸掃描
    for dirpath, _, filenames in os.walk(root_path):
        for filename in sorted(filenames):
            full_path = os.path.join(dirpath, filename)
            # descriptor 需要相對於初始的 root_path
            relative_path = os.path.relpath(full_path, start=os.path.dirname(root_path)) # start 是 root_path 的父目錄
                                                                                        # 以便 descriptor 包含 root_path 的基礎名稱

            try:
                with open(full_path, 'rb') as f:
                    c = f.read()
                import zipfile # 延後導入
                if zipfile.is_zipfile(io.BytesIO(c)):
                    with zipfile.ZipFile(io.BytesIO(c), 'r') as z:
                        for n in sorted(z.namelist()): # n 是 zip 內的路徑
                            if n.endswith('/') or '__MACOSX' in n: continue
                            with z.open(n) as i:
                                yield f"{relative_path} -> {n}", i.read()
                else:
                    yield relative_path, c
            except Exception as e:
                logger.warning(f"讀取或解壓縮 '{relative_path}' 時出錯: {e}")


def determine_parsing_recipe(content_bytes: bytes, descriptor: str) -> Optional[Dict[str, Any]]:
    if not content_bytes: return None
    if descriptor.lower().endswith('.ods'): return {"parser": "excel_ods", "args": {}, "pipeline": "unknown"}

    sample_lines, detected_encoding = [], None # 延後檢測編碼

    # 編碼檢測邏輯
    try:
        for enc_try in ['ms950', 'utf-8', 'utf-8-sig']:
            try:
                sample_text_test = content_bytes.decode(enc_try)
                detected_encoding = enc_try
                break # 找到有效編碼
            except UnicodeDecodeError:
                continue
        if not detected_encoding: # 如果都失敗
             return None # 或者標記為二進位，但這裡返回 None 以跳過

        sample_text = content_bytes.decode(detected_encoding)
        sample_lines = sample_text.splitlines()[:20] # 取前20行做判斷
    except Exception: # 其他解碼或分割錯誤
        return None

    if not sample_lines: return None

    header_line = sample_lines[0].strip()
    first_data_line = next((line.strip() for line in sample_lines[1:] if line.strip()), "")
    # 移除 args 中的 "dtype": str，使其 JSON 安全
    args = {"encoding": detected_encoding, "skipinitialspace": True, "thousands": ','}

    # 針對 '---' 分隔的固定寬度格式 (通常是 Tick Data)
    if "成交日期" in header_line and "成交時間" in header_line and "---" in first_data_line:
        # 使用硬編碼的 colspecs, names, 和 skiprows
        return {"parser": "fwf",
                "args": {**args,
                         "names": TICK_DATA_FWF_COLUMNS,
                         "colspecs": TICK_DATA_FWF_COLSPECS,
                         "skiprows": TICK_DATA_FWF_SKIPROWS},
                "pipeline": "tick_data"}

    # 針對 CSV 格式，嘗試動態偵測表頭
    try:
        header_idx = 0
        found_header_keywords = False
        for i, line in enumerate(sample_lines):
            if any(k in line for k in ['交易日期', '商品', '身份別', '日期', '美元／新台幣', '買賣權成交量比率']):
                header_idx = i
                found_header_keywords = True
                break

        if not found_header_keywords and len(sample_lines) > 1: # 如果沒有關鍵字，但有多行，嘗試以第一行為表頭
            header_idx = 0

        # 使用 BytesIO 進行解析，避免重複 decode
        df_header_test_stream = io.BytesIO(content_bytes)
        df_header = pd.read_csv(df_header_test_stream, header=header_idx, nrows=0, **args)
        cols = {str(c).strip().replace(' ', '_').replace('(', '').replace(')', '') for c in df_header.columns}

        dyn_args = {**args, "header": header_idx} # dyn_args 用於實際解析

        if {'身份別', '商品名稱'}.issubset(cols): return {"parser": "csv_dynamic_header", "args": dyn_args, "pipeline": "institutional_investors"}
        if {'美元／新台幣', '日期'}.issubset(cols): return {"parser": "csv_dynamic_header", "args": dyn_args, "pipeline": "fx_rates"}
        if {'買賣權成交量比率', '日期'}.issubset(cols): return {"parser": "csv_dynamic_header", "args": dyn_args, "pipeline": "pcr"}
        # '交易日期', '契約', '收盤價' (期貨日報) vs '成交日期', '商品代號', '成交價格' (期貨/選擇權 Tick)
        if {'交易日期', '契約', '收盤價'}.issubset(cols): return {"parser": "csv_dynamic_header", "args": dyn_args, "pipeline": "daily_ohlc"}
        if {'成交日期', '商品代號', '成交價格'}.issubset(cols) and not "---" in first_data_line : # 避免與fwf衝突
            return {"parser": "csv_dynamic_header", "args": dyn_args, "pipeline": "tick_data"}

    except Exception: # 如果 pandas 讀取表頭失敗
        pass # 繼續嘗試其他配方

    # 針對手動指定欄位的 CSV (通常是舊格式的每日行情)
    # 修改判斷邏輯：優先基於表頭關鍵字
    names_key_manual = None
    # 檢查是否有 '到期月份/週別' 或 '到期月份／週別' (兼容全形斜線)
    # 並結合其他欄位來區分
    if ('到期月份/週別' in header_line or '到期月份／週別' in header_line):
        if '漲跌%' in header_line and '履約價' in header_line:
            names_key_manual = 'options_daily_v2'
        elif '履約價' in header_line:
            names_key_manual = 'options_daily_v1'
        elif '商品代號' in header_line and '結算價' in header_line: # 這是期貨每日行情的特徵
            names_key_manual = 'futures_daily'

    if names_key_manual:
        # 確保欄位數量大致匹配，避免錯誤應用到完全不同的CSV
        num_header_cols = len(header_line.split(','))
        expected_num_cols = len(MANUAL_COLUMN_NAMES[names_key_manual])
        # 允許一定程度的欄位數差異，例如 +/- 2，因為有些檔案末尾可能有額外空欄位
        if abs(num_header_cols - expected_num_cols) <= 2 or num_header_cols > 10 : #  或者是一個明顯的寬表
             # 並且第一行數據的欄位數與表頭相似 (或更多，如果原始條件想保留)
            num_data_cols = len(first_data_line.split(','))
            if num_data_cols >= num_header_cols -1 : # 允許數據行少一個欄位（可能是尾部逗號）
                return {"parser": "csv_manual_cols", "args": {**args, "names_key": names_key_manual, "header": None, "skiprows": 1}, "pipeline": "daily_ohlc"}

    return None # 未找到匹配配方

def parse_with_recipe(content_bytes: bytes, recipe: Dict[str, Any]) -> Optional[pd.DataFrame]: # 移除 logger
    parser_type, args = recipe.get("parser"), recipe.get("args", {}).copy()
    stream = io.BytesIO(content_bytes)
    # 在子進程中，警告/錯誤最好通過返回結果傳遞，而不是直接打印或記錄
    parse_message = None
    try:
        # 在所有 pandas read_* 函數中加入 dtype=str
        if parser_type == "excel_ods": return pd.read_excel(stream, engine='odf', header=None, dtype=str)
        if parser_type == "fwf":
            return pd.read_fwf(stream, dtype=str, **args)
        if parser_type in ["csv", "csv_dynamic_header"]:
            return pd.read_csv(stream, dtype=str, **args)
        if parser_type == "csv_manual_cols":
            names_key = args.pop("names_key", None)
            if not names_key or names_key not in MANUAL_COLUMN_NAMES:
                # logger.warning(f"手動欄位解析錯誤：未知的 names_key '{names_key}'") # 無法使用 logger
                parse_message = f"手動欄位解析錯誤：未知的 names_key '{names_key}'"
                return None # 或者 return (None, parse_message) 如果 worker_process_file 期望元組
            names = MANUAL_COLUMN_NAMES[names_key]
            return pd.read_csv(stream, names=names, usecols=range(len(names)), **args)
    except Exception as e:
        # logger.warning(f"使用配方 {parser_type} 解析時出錯: {e}") # 無法使用 logger
        parse_message = f"使用配方 {parser_type} 解析時出錯: {e}"
        # 為了讓 worker_process_file 能獲取此信息，可以修改返回結構
        # 但目前 worker_process_file 期望 parse_with_recipe 返回 DataFrame 或 None
        # 所以這裡還是返回 None，錯誤信息會丟失，除非修改 worker_process_file 的期望
    return None # 返回 (None, parse_message) 如果要傳遞消息

def _clean_and_prepare_df(df: pd.DataFrame, required_cols: List[str], rename_map: Dict[str, str]) -> pd.DataFrame:
    # 確保 df 不是 None 且有欄位
    if df is None or df.empty or df.columns.empty:
        raise ValueError("傳入的 DataFrame 為空或沒有欄位。")

    # 清理欄位名：轉小寫、去首尾空格、替換特殊字符
    df.columns = [str(col).strip().replace(' ', '_').replace('(', '').replace(')', '').lower() for col in df.columns]

    # 根據 rename_map 重命名欄位
    df_clean = df.rename(columns=lambda c: rename_map.get(c, c))

    current_cols = set(df_clean.columns)
    missing_cols = [col for col in required_cols if col not in current_cols]
    if missing_cols:
        raise KeyError(f"缺少必要欄位: {missing_cols}。可用欄位: {list(current_cols)}")
    return df_clean

# --- 各類數據的清洗管線 ---
def pipeline_daily_ohlc(df: pd.DataFrame, source: str) -> pd.DataFrame:
    rename_map = { '交易日期': 'trading_date', '契約': 'product_id', '商品代號': 'product_id', '到期月份週別': 'expiry_month', '到期月份／週別': 'expiry_month', '履約價': 'strike_price', '買賣權': 'option_type', '開盤價': 'open', '最高價': 'high', '最低價': 'low', '收盤價': 'close', '成交量': 'volume', '結算價': 'settlement_price', '未沖銷契約數': 'open_interest', '交易時段': 'trading_session', '漲跌價': 'change', '漲跌%': 'change_percent' }
    df_clean = _clean_and_prepare_df(df, ['trading_date', 'product_id', 'close'], rename_map)
    df_clean['trading_date'] = pd.to_datetime(df_clean['trading_date'], errors='coerce').dt.strftime('%Y-%m-%d')
    if 'option_type' in df_clean.columns: df_clean['option_type'] = df_clean['option_type'].astype(str).str.strip().map({'買權': 'C', '賣權': 'P', 'Call': 'C', 'Put':'P'})
    if 'trading_session' not in df_clean.columns: df_clean['trading_session'] = 'Regular' # 預設值
    else: df_clean['trading_session'] = df_clean['trading_session'].astype(str).str.strip().replace('盤後', 'AfterHours').replace('一般', 'Regular')

    numeric_cols = ['open', 'high', 'low', 'close', 'volume', 'settlement_price', 'open_interest', 'strike_price', 'change', 'change_percent']
    for col in numeric_cols:
        if col in df_clean.columns: df_clean[col] = pd.to_numeric(df_clean[col].astype(str).str.replace(',', '').replace('-', 'NaN'), errors='coerce')
    df_clean['source'] = source
    return df_clean.dropna(subset=['trading_date', 'product_id'])

def pipeline_tick_data(df: pd.DataFrame, source: str) -> pd.DataFrame:
    # 欄位名在 read_fwf 時已處理，這裡主要是類型轉換和衍生欄位
    rename_map = {
        '成交日期': 'trade_date',
        '商品代號': 'product_id',
        '到期月份週別': 'expiry_month', # FWF 表頭提取的是 '到期月份/週別'，_clean_and_prepare_df 會轉為 '到期月份_週別'
        '到期月份_週別': 'expiry_month', # 增加這個以兼容
        '履約價': 'strike_price',
        '買賣權': 'option_type',
        '成交時間': 'trade_time',
        '成交價格': 'price',
        '成交數量b_or_s': 'volume', # 來自某些CSV
        '成交數量_買賣別_': 'volume', # 來自某些FWF的header_names清理結果
        '成交數量': 'volume' # 來自FWF的原始表頭 '成交數量'
    }
    df_clean = _clean_and_prepare_df(df, ['trade_date', 'trade_time', 'price', 'volume'], rename_map)

    # 處理成交時間中的毫秒 (如果有)
    df_clean['trade_time_formatted'] = df_clean['trade_time'].astype(str).str.replace(r'(\d{2}:\d{2}:\d{2})(\d{3})', r'\1.\2', regex=True)
    df_clean['trade_datetime'] = pd.to_datetime(df_clean['trade_date'].astype(str) + ' ' + df_clean['trade_time_formatted'], format='%Y%m%d %H:%M:%S.%f', errors='coerce')
    # 如果沒有毫秒，嘗試不帶毫秒的格式
    mask_no_ms = df_clean['trade_datetime'].isna() & (~df_clean['trade_time'].astype(str).str.contains(r'\d{6}')) # 不含6位數字（潛在毫秒）
    df_clean.loc[mask_no_ms, 'trade_datetime'] = pd.to_datetime(df_clean.loc[mask_no_ms, 'trade_date'].astype(str) + ' ' + df_clean.loc[mask_no_ms, 'trade_time'].astype(str), format='%Y%m%d %H:%M:%S', errors='coerce')

    df_clean['trade_datetime'] = df_clean['trade_datetime'].dt.strftime('%Y-%m-%d %H:%M:%S.%f')

    if 'option_type' in df_clean.columns: df_clean['option_type'] = df_clean['option_type'].astype(str).str.strip().map({'C': 'C', 'P': 'P', '買': 'C', '賣': 'P'})
    for col in ['strike_price', 'price', 'volume']:
        if col in df_clean.columns: df_clean[col] = pd.to_numeric(df_clean[col], errors='coerce')
    df_clean['source'] = source
    df_clean = df_clean.drop(columns=['trade_date', 'trade_time', 'trade_time_formatted'], errors='ignore')
    return df_clean.dropna(subset=['trade_datetime', 'product_id'])


def pipeline_institutional_investors(df: pd.DataFrame, source: str) -> pd.DataFrame:
    rename_map = {'身份別': 'investor_type', '商品名稱': 'product_name', '日期':'data_date', '交易日':'data_date'}
    # '買賣權' 欄位用於區分期貨和選擇權，以及 Call/Put
    df_clean = _clean_and_prepare_df(df, ['data_date', 'investor_type', 'product_name'], rename_map)
    df_clean['data_date'] = pd.to_datetime(df_clean['data_date'].astype(str).str.replace('/','-'), errors='coerce').dt.strftime('%Y-%m-%d')

    df_clean['instrument_type'] = df_clean.apply(lambda r: 'Option' if '買賣權' in r.index and pd.notna(r['買賣權']) and str(r['買賣權']).strip() in ['買權', '賣權'] else 'Future', axis=1)
    if '買賣權' in df_clean.columns:
        df_clean['option_type'] = df_clean['買賣權'].astype(str).str.strip().map({'買權': 'C', '賣權': 'P'})
    else:
        df_clean['option_type'] = None # 或 pd.NA

    col_map = { '多方交易_口數_': 'long_pos_vol', '多方交易_契約金額_千元_': 'long_pos_val_twd_k', '空方交易_口數_': 'short_pos_vol', '空方交易_契約金額_千元_': 'short_pos_val_twd_k', '多空交易淨口數': 'net_pos_vol', '多空交易淨額_千元_': 'net_pos_val_twd_k', '未平倉_多方_口數_': 'long_oi_vol', '未平倉_多方_契約金額_千元_': 'long_oi_val_twd_k', '未平倉_空方_口數_': 'short_oi_vol', '未平倉_空方_契約金額_千元_': 'short_oi_val_twd_k', '未平倉淨口數': 'net_oi_vol', '未平倉淨額_千元_': 'net_oi_val_twd_k',}
    df_clean = df_clean.rename(columns=lambda c: col_map.get(c,c)) # 重命名剩餘的特定欄位
    for col in col_map.values(): # 確保所有目標數值欄位存在並轉換
        if col in df_clean.columns:
            df_clean[col] = pd.to_numeric(df_clean[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0).astype(int)
        else: # 如果欄位不存在，則創建並填充0
            df_clean[col] = 0

    df_clean['source'] = source
    return df_clean.dropna(subset=['data_date', 'product_name', 'investor_type'])

def pipeline_fx_rates(df: pd.DataFrame, source: str) -> pd.DataFrame:
    rename_map = {'日期': 'data_date', '美元／新台幣': 'usd_twd', '人民幣／新台幣': 'cny_twd', '歐元／美元': 'eur_usd', '美元／日圓': 'usd_jpy', '英鎊／美元': 'gbp_usd', '澳幣／美元': 'aud_usd', '美元／港幣': 'usd_hkd', '美元／人民幣': 'usd_cny', '美元／南非幣': 'usd_zar', '紐幣／美元': 'nzd_usd'}
    df_clean = _clean_and_prepare_df(df, ['data_date', 'usd_twd'], rename_map)
    df_clean['data_date'] = pd.to_datetime(df_clean['data_date'], errors='coerce').dt.strftime('%Y-%m-%d')
    for col in [c for c in rename_map.values() if c != 'data_date']:
        if col in df_clean.columns: df_clean[col] = pd.to_numeric(df_clean[col], errors='coerce')
    df_clean['source'] = source
    return df_clean.dropna(subset=['data_date'])

def pipeline_pcr(df: pd.DataFrame, source: str) -> pd.DataFrame:
    rename_map = {'日期': 'data_date', '賣權成交量': 'put_volume', '買權成交量': 'call_volume', '買賣權成交量比率%': 'pcr_volume', '賣權未平倉量': 'put_oi', '買權未平倉量': 'call_oi', '買賣權未平倉量比率%': 'pcr_oi'}
    df_clean = _clean_and_prepare_df(df, ['data_date'], rename_map) # 只需要日期是必要的
    df_clean['data_date'] = pd.to_datetime(df_clean['data_date'], errors='coerce').dt.strftime('%Y-%m-%d')

    # PCR 比率通常帶有 %，需要移除
    for col in ['pcr_volume', 'pcr_oi']:
        if col in df_clean.columns:
            df_clean[col] = pd.to_numeric(df_clean[col].astype(str).str.replace('%', '').str.replace(',', ''), errors='coerce')
            if col == 'pcr_volume': # 如果原數據是百分比，轉為小數
                 df_clean[col] = df_clean[col] / 100 if df_clean[col].max() > 5 else df_clean[col] # 簡單判斷是否已是小數
            if col == 'pcr_oi':
                 df_clean[col] = df_clean[col] / 100 if df_clean[col].max() > 5 else df_clean[col]


    for col in ['put_volume', 'call_volume', 'put_oi', 'call_oi']:
        if col in df_clean.columns: df_clean[col] = pd.to_numeric(df_clean[col].astype(str).str.replace(',', ''), errors='coerce')

    df_clean['source'] = source
    return df_clean.dropna(subset=['data_date'])

PIPELINE_MAP = {"daily_ohlc": pipeline_daily_ohlc, "institutional_investors": pipeline_institutional_investors, "tick_data": pipeline_tick_data, "fx_rates": pipeline_fx_rates, "pcr": pipeline_pcr}

# ==============================================================================
# 並行處理與主要流程的輔助函數
# ==============================================================================
def worker_process_file(args: Tuple[str, bytes, Dict, str]) -> Dict[str, Any]: # 移除了 DualLogger, HardwareManager
    descriptor, content_bytes, recipe, staging_path = args

    try:
        # parse_with_recipe 現在不接收 logger，如果需要，它應在其返回中包含警告/錯誤信息
        parse_result = parse_with_recipe(content_bytes, recipe)

        if isinstance(parse_result, tuple): # 假設返回 (DataFrame, message) 或 (None, message)
            df, parse_message = parse_result
            if parse_message: # 如果有來自解析器的消息，可以考慮如何處理它
                # 為了簡單起見，我們先假設解析成功才繼續
                pass
        else: # 假設只返回 DataFrame 或 None
            df = parse_result
            parse_message = None

        if df is None or df.empty:
            return {'status': 'skipped_empty_parse',
                    'message': f"解析後為空或解析失敗。解析器消息: {parse_message}",
                    'descriptor': descriptor}

        pipeline_name = recipe.get("pipeline")
        pipeline_func = PIPELINE_MAP.get(pipeline_name)

        if not pipeline_func: return {'status': 'skipped_no_pipeline', 'message': f"找不到對應的管線 '{pipeline_name}'", 'descriptor': descriptor}

        cleaned_df = pipeline_func(df, descriptor) # pipeline_func 內部應有自己的錯誤處理

        if cleaned_df is None or cleaned_df.empty: return {'status': 'skipped_empty_clean', 'message': "數據清洗後為空", 'descriptor': descriptor, 'pipeline': pipeline_name}

        # 特定欄位類型轉換 (例如，確保 expiry_month 是字串)
        if pipeline_name == 'daily_ohlc' and 'expiry_month' in cleaned_df.columns:
            cleaned_df['expiry_month'] = cleaned_df['expiry_month'].astype(str)
        if pipeline_name == 'tick_data' and 'expiry_month' in cleaned_df.columns:
            cleaned_df['expiry_month'] = cleaned_df['expiry_month'].astype(str)

        file_hash = hashlib.sha256(content_bytes).hexdigest()[:16] # 短 hash
        staging_file_path = os.path.join(staging_path, f"{pipeline_name}_{descriptor.replace('/', '_').replace(' -> ', '__')}_{file_hash}.parquet")
        os.makedirs(os.path.dirname(staging_file_path), exist_ok=True) # 確保暫存子目錄存在

        cleaned_df.to_parquet(staging_file_path, engine='pyarrow')

        # hw_manager.log_event_snapshot(f"處理後: {descriptor[:40]}...") # 這個在 worker 中調用會有問題

        return {'status': 'success', 'rows_processed': len(cleaned_df), 'pipeline': pipeline_name, 'staging_file': staging_file_path, 'descriptor': descriptor}
    except Exception as e:
        # logger_instance.error(f"Worker 處理 '{descriptor}' 時發生嚴重錯誤: {e}") # 這也可能無法正確記錄
        return {'status': 'error', 'message': f"Worker 錯誤 ({type(e).__name__}): {e}", 'descriptor': descriptor, 'pipeline': recipe.get("pipeline", "unknown")}


# run_parsing_stage 和 run_duckdb_loading_stage 將在 run.py 中實現或調用
# 因為它們依賴於由 run.py 傳遞的路徑參數和 logger 實例。
