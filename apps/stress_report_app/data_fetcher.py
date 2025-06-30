# -*- coding: utf-8 -*-
"""
數據獲取模組 (data_fetcher.py) - v3.0

功能：
- 從 FRED 獲取經濟數據 (包含單序列獲取輔助函式)
- 從 Yahoo Finance 獲取市場數據 (包含單序列獲取輔助函式)
- 從 NY Fed 獲取一級交易商持倉數據
- 實作 MOVE 指數的多層備援獲取邏輯 (FRED -> TLT IV)
- 實作 VIX 指數的雙層備援獲取邏輯 (FRED -> Yahoo Finance)
- (新增) 計算 TLT 期權隱含波動率作為 MOVE 備援
"""

import pandas as pd
import numpy as np
import yfinance as yf
from fredapi import Fred
import requests
import io
from datetime import datetime, timedelta
import logging
from typing import Optional, Dict, List, Any

# 嘗試導入 py_vollib，如果失敗則在需要時處理
try:
    from py_vollib.black_scholes.implied_volatility import implied_volatility
    PY_VOLLIB_AVAILABLE = True
except ImportError:
    PY_VOLLIB_AVAILABLE = False
    implied_volatility = None # 確保變數存在

logger = logging.getLogger(__name__)

# --- 輔助函式 ---
def get_fred_data_series(series_id: str, start_date: str, end_date: str, api_key: str) -> Optional[pd.Series]:
    """
    從 FRED 獲取單個經濟時間序列數據。
    """
    logger.debug(f"輔助函式：嘗試從 FRED 獲取序列 '{series_id}' (日期範圍應用於獲取後: {start_date} 至 {end_date})")
    try:
        fred = Fred(api_key=api_key)
        # 先獲取全部歷史數據
        s = fred.get_series(series_id)

        if s.empty or s.isna().all():
            logger.warning(f"FRED 序列 '{series_id}' 返回數據為空或全為 NaN (在應用日期範圍之前)。")
            return None

        s.index = pd.to_datetime(s.index).tz_localize(None) # 標準化索引
        s = s.sort_index() # 確保索引排序

        # 在獲取所有數據後再進行日期篩選
        s_dt = pd.to_datetime(start_date)
        e_dt = pd.to_datetime(end_date)
        s_filtered = s[(s.index >= s_dt) & (s.index <= e_dt)]

        if s_filtered.empty:
            logger.warning(f"FRED 序列 '{series_id}' 在日期範圍 {start_date} 到 {end_date} 內無數據 (篩選後為空)。")
            return None

        logger.debug(f"FRED 序列 '{series_id}' 獲取並篩選後成功，共 {len(s_filtered)} 筆數據。")
        return s_filtered
    except requests.exceptions.RequestException as re:
        logger.error(f"FRED 序列 '{series_id}' 獲取時發生網路錯誤: {re}")
        return None
    except ValueError as ve: # FredAPI 可能拋出 ValueError (例如序列不存在)
        logger.error(f"FRED 序列 '{series_id}' 獲取時發生錯誤: {ve}")
        return None
    except Exception as e:
        logger.error(f"獲取 FRED 序列 '{series_id}' 時發生未預期錯誤: {e}", exc_info=True)
        return None

def get_yahoo_finance_series(ticker_symbol: str, start_date: str, end_date: str, column: str = 'Close') -> Optional[pd.Series]:
    """
    從 Yahoo Finance 獲取單個 Ticker 的指定欄位數據。
    """
    logger.debug(f"輔助函式：嘗試從 Yahoo Finance 獲取 Ticker '{ticker_symbol}' 的 '{column}' 欄位 (從 {start_date} 至 {end_date})")
    try:
        ticker = yf.Ticker(ticker_symbol)
        # yfinance 的 end 參數是 "up to, but not including"，所以需要將結束日期加一天
        end_date_for_yf = (pd.to_datetime(end_date) + timedelta(days=1)).strftime('%Y-%m-%d')

        hist = ticker.history(start=start_date, end=end_date_for_yf, auto_adjust=True)

        if hist.empty or column not in hist.columns:
            logger.warning(f"Yahoo Finance Ticker '{ticker_symbol}' 返回數據為空或缺少 '{column}' 欄位。")
            return None

        series_data = hist[column].copy()
        # 將索引的時區和時間部分移除，只保留日期，並確保是 DatetimeIndex
        series_data.index = pd.to_datetime(series_data.index.date)
        series_data = series_data.sort_index() # 確保索引排序
        logger.debug(f"Yahoo Finance Ticker '{ticker_symbol}' 的 '{column}' 欄位獲取成功，共 {len(series_data)} 筆原始數據。")
        return series_data
    except Exception as e:
        logger.error(f"獲取 Yahoo Finance Ticker '{ticker_symbol}' 時發生錯誤: {e}", exc_info=True)
        return None

# --- 主要數據獲取函式 (修改後) ---
def fetch_fred_data(api_key: str, start_date: str, end_date: str, series_map: Dict[str, str]) -> pd.DataFrame:
    """
    從 FRED 獲取指定的經濟時間序列數據，並對齊到業務日。
    """
    logger.info(f"主函式：從 FRED 獲取數據 (從 {start_date} 到 {end_date}) 針對 {len(series_map)} 個序列...")
    fred_data_temp = {}

    s_dt = pd.to_datetime(start_date)
    e_dt = pd.to_datetime(end_date)
    # 創建日曆日索引，用於低頻數據的填充對齊
    daily_index_for_fred = pd.date_range(start=s_dt, end=e_dt, freq='D')

    for target_name, series_id in series_map.items():
        logger.info(f"  - 正在抓取 FRED {target_name} ({series_id})...")
        series_data = get_fred_data_series(series_id, start_date, end_date, api_key)

        if series_data is not None:
            # 頻率處理與對齊 (類似原邏輯，但更清晰)
            original_count = len(series_data.dropna())

            if len(series_data.index) < 3:
                logger.debug(f"    序列 '{target_name}' 的數據點 ({len(series_data.index)}) 過少，直接進行每日向前填充。")
                s_aligned = series_data.reindex(daily_index_for_fred).ffill()
            else:
                freq_str = pd.infer_freq(series_data.index)
                if freq_str and ('W' in freq_str.upper() or 'M' in freq_str.upper()):
                    logger.debug(f"    序列 '{target_name}' 是 {freq_str} 頻率，將重新索引到每日並向前填充。")
                    s_aligned = series_data.reindex(daily_index_for_fred).ffill()
                else: # 日頻或無法推斷，直接對齊
                    s_aligned = series_data.reindex(daily_index_for_fred, method='ffill') # 保證所有日期都有值

            aligned_count = s_aligned.count()
            logger.info(f"    成功 (原始 {original_count} -> 每日填充後 {aligned_count} 點)")
            fred_data_temp[target_name] = s_aligned
        else:
            logger.warning(f"    FRED 序列 '{target_name}' ({series_id}) 獲取失敗。")
            fred_data_temp[target_name] = pd.Series(dtype='float64', index=daily_index_for_fred) # 創建空 Series 以保持結構

    if not fred_data_temp:
        logger.warning("未能從 FRED 抓取任何數據。")
        return pd.DataFrame()

    # 合併並對齊到業務日
    combined_df = pd.DataFrame(fred_data_temp)
    if combined_df.empty:
        logger.warning("FRED 數據合併後為空。")
        return pd.DataFrame()

    business_day_index = pd.date_range(start=s_dt, end=e_dt, freq='B')
    fred_final_df = combined_df.reindex(business_day_index) # 直接 reindex 到業務日

    if fred_final_df.empty or fred_final_df.isna().all(axis=None):
        logger.warning("FRED 數據對齊到業務日後變為空或全為 NaN。")
        return pd.DataFrame(index=business_day_index) # 返回帶業務日索引的空 DataFrame

    logger.info(f"FRED 數據已獲取並對齊至業務日索引 ({len(fred_final_df)} 行)。")
    return fred_final_df

def fetch_yahoo_data(start_date: str, end_date: str, tickers_map: Dict[str, str]) -> pd.DataFrame:
    """
    從 Yahoo Finance 獲取市場數據，並對齊到業務日。
    """
    logger.info(f"主函式：從 Yahoo Finance 獲取數據 (從 {start_date} 到 {end_date}) 針對 {len(tickers_map)} 個 Ticker...")
    s_dt = pd.to_datetime(start_date)
    e_dt = pd.to_datetime(end_date)
    business_day_index = pd.date_range(start=s_dt, end=e_dt, freq='B')
    final_yahoo_df = pd.DataFrame(index=business_day_index)

    for target_col_name, ticker_symbol in tickers_map.items():
        logger.info(f"  - 正在請求 Yahoo Finance {ticker_symbol} (目標欄位: {target_col_name})...")
        series_data = get_yahoo_finance_series(ticker_symbol, start_date, end_date, 'Close') # 預設抓取收盤價

        if series_data is not None:
            # 對齊到業務日並向前填充 (如果需要)
            # yfinance 返回的數據通常已是對齊交易日的，但 reindex 可確保與 FRED 一致
            aligned_series = series_data.reindex(business_day_index, method='ffill')
            final_yahoo_df[target_col_name] = aligned_series
            logger.info(f"    成功抓取並對齊 {len(series_data)} 筆 {ticker_symbol} 數據到 {target_col_name}。")
        else:
            logger.warning(f"    未能成功抓取或處理 Yahoo Finance {ticker_symbol} 的數據。將以 NaN 填充 {target_col_name}。")
            final_yahoo_df[target_col_name] = np.nan # 確保列存在

    logger.info(f"Yahoo Finance 數據獲取完成。最終 DataFrame 維度: {final_yahoo_df.shape}")
    return final_yahoo_df

# calculate_tlt_iv, get_move_index, get_vix_index 將在後續步驟中添加
def calculate_tlt_iv(tlt_price_series: pd.Series,
                     options_chain_data: Any, # 這裡的類型取決於如何獲取和組織期權數據
                     risk_free_rate_series: pd.Series,
                     dividend_yield: float,
                     current_date: datetime) -> Optional[float]:
    """
    計算給定日期 TLT 的隱含波動率。
    此為簡化版，實際需處理期權選擇、到期日、履約價等。
    """
    if not PY_VOLLIB_AVAILABLE:
        logger.error("py_vollib 函式庫不可用，無法計算隱含波動率。")
        return None
    # logger.debug(f"calculate_tlt_iv: 嘗試為日期 {current_date.strftime('%Y-%m-%d')} 計算 IV")
    # # 實際的期權選擇和 Black-Scholes 計算邏輯將在此處實現
    # # 1. 從 options_chain_data 中為 current_date 選擇合適的期權 (最近到期、平價)
    # # 2. 準備 Black-Scholes 模型所需參數 (S, K, t, r, q, flag)
    # # 3. 使用 implied_volatility() 計算
    # # 這裡僅為佔位符
    # logger.warning("calculate_tlt_iv 尚未完全實現期權選擇和 IV 計算細節。")
    # return np.random.uniform(0.10, 0.30) # 返回一個隨機的 IV 作為佔位符
    # 已決定放棄 TLT IV 方案，故註解此函式
    pass


def get_move_index(start_date: str, end_date: str, config: dict) -> Optional[pd.Series]:
    """
    獲取 MOVE 指數，包含備援邏輯。
    第一優先級: FRED。
    第二優先級: Yahoo Finance (^MOVE)。
    """
    logger.info("獲取 MOVE 指數...")
    api_key = config.get('api_keys', {}).get('fred')
    data_fetching_config = config.get('data_fetching', {})

    # 第一優先級: FRED
    fred_move_id = data_fetching_config.get('fred_move_ticker', 'MOVEIX') # 從設定檔讀取
    if api_key and fred_move_id:
        logger.info(f"  嘗試從 FRED 獲取 MOVE 指數 (ID: {fred_move_id})...")
        move_series_fred = get_fred_data_series(fred_move_id, start_date, end_date, api_key)
        if move_series_fred is not None and not move_series_fred.empty:
            logger.info(f"成功從 FRED (ID: {fred_move_id}) 獲取 MOVE 指數數據。")
            return move_series_fred.rename("MOVE_Index")
    else:
        logger.warning(f"FRED API Key 或 FRED MOVE Ticker ('{fred_move_id}') 未設定，跳過從 FRED 獲取 MOVE。")

    # 第二優先級: Yahoo Finance (^MOVE)
    yahoo_move_ticker = None
    # 從 yahoo_tickers_map 中找到鍵為 '^MOVE' 的值 (即目標欄位名)
    # 或者直接使用 '^MOVE' 作為 ticker，然後將結果重命名
    for target_col, ticker_sym in data_fetching_config.get('yahoo_tickers_map', {}).items():
        if ticker_sym == '^MOVE': # 假設設定檔中 ^MOVE 會被映射到某個欄位名
            yahoo_move_ticker = ticker_sym # 使用設定檔中的 ticker 符號
            break
    if not yahoo_move_ticker: # 如果沒在 map 中找到，直接用 '^MOVE'
        yahoo_move_ticker = '^MOVE'

    logger.warning(f"從 FRED 獲取 MOVE 指數失敗或跳過。啟用備援：從 Yahoo Finance 獲取 {yahoo_move_ticker}。")
    move_series_yahoo = get_yahoo_finance_series(yahoo_move_ticker, start_date, end_date, 'Close')
    if move_series_yahoo is not None and not move_series_yahoo.empty:
        logger.info(f"成功從 Yahoo Finance ({yahoo_move_ticker}) 獲取 MOVE 指數數據。")
        return move_series_yahoo.rename("MOVE_Index")

    logger.error(f"無法從 FRED 或 Yahoo Finance ({yahoo_move_ticker}) 獲取 MOVE 指數數據。")
    return None


def get_vix_index(start_date: str, end_date: str, config: dict) -> Optional[pd.Series]:
    """
    獲取 VIX 指數，包含備援邏輯。
    第一優先級: FRED (VIXCLS)。
    第二優先級: Yahoo Finance (^VIX)。
    """
    logger.info("獲取 VIX 指數...")
    api_key = config.get('api_keys', {}).get('fred')
    if not api_key:
        logger.error("FRED API Key 未在設定檔中提供，影響 VIX 指數主要來源獲取。")
        # 即使 FRED key 缺失，仍然嘗試 Yahoo Finance

    # 第一優先級: FRED (VIXCLS)
    if api_key: # 只有在有 FRED key 時才嘗試
        logger.info("  嘗試從 FRED 獲取 VIX 指數 (ID: VIXCLS)...")
        vix_series_fred = get_fred_data_series('VIXCLS', start_date, end_date, api_key)
        if vix_series_fred is not None and not vix_series_fred.empty:
            logger.info("成功從 FRED (ID: VIXCLS) 獲取 VIX 指數數據。")
            return vix_series_fred.rename("VIX_Index")

    # 第二優先級: Yahoo Finance (^VIX)
    logger.warning("從 FRED (ID: VIXCLS) 獲取 VIX 指數失敗或 API Key 缺失。啟用備援：從 Yahoo Finance 獲取 ^VIX。")
    vix_series_yahoo = get_yahoo_finance_series('^VIX', start_date, end_date, 'Close')
    if vix_series_yahoo is not None and not vix_series_yahoo.empty:
        logger.info("成功從 Yahoo Finance (^VIX) 獲取 VIX 指數數據。")
        return vix_series_yahoo.rename("VIX_Index")

    logger.error("無法從 FRED 或 Yahoo Finance 獲取 VIX 指數數據。")
    return None


def fetch_nyfed_data(ny_fed_positions_urls: List[str], sbp_cols_config: Dict[str, List[str]]) -> Optional[pd.Series]:
    """
    從紐約聯儲 (NY Fed) 網站獲取一級交易商的美國公債持有量數據。
    此版本整合了來自 `一級交易pro.py` Cell 6 的經過驗證的解析邏輯。

    主要步驟：
    1. 遍歷提供的 URL 列表。
    2. 對於每個 URL，使用 `requests.Session` 下載 Excel 檔案內容至記憶體。
    3. 解析 Excel：
        - 嘗試自動檢測表頭行和日期/數值列。
        - 讀取數據，將日期列設為索引，處理無效數據。
    4. 數據轉換與提取：
        - 將長格式數據透視為寬格式 (使用 `pivot_table`)。
        - 根據 URL 特徵（如包含 "SBN" 或 "SBP"）及 `sbp_cols_config`
          (對應原始腳本中的 `sbp2013_cols_to_sum`, `sbp2001_cols_to_sum`) 決定要加總的欄位。
        - 加總選定的欄位以獲得每日總持有量，並清理結果 (移除 NaN 和 0)。
    5. 合併來自所有成功處理的檔案的數據。
    6. 清理最終的時間序列（排序、去重、保留最新值）。

    Args:
        ny_fed_positions_urls (List[str]): 包含 NY Fed Excel 檔案 URL 的列表。
        sbp_cols_config (Dict[str, List[str]]):
            一個字典，用於配置如何根據文件名中的關鍵字 (例如 'SBP2013', 'SBP2001')
            來選擇要加總的欄位。鍵是關鍵字，值是要加總的欄位名列表。
            例如: {'SBP2013': ['col_A', 'col_B'], 'SBP2001': ['col_X', 'col_Y']}
            如果文件名包含 'SBN'，則會嘗試加總所有以 'PDPOSGSC-' 開頭的欄位。
            此參數對應原始腳本中 `PROJECT_CONFIG` 內的 `sbpXXXX_cols_to_sum`。
            在此實現中，我們期望 `sbp_cols_config` 的鍵直接是 'SBP2013', 'SBP2001' 等。

    Returns:
        pd.Series: 一個時間序列 (索引為日期，值為百萬美元的總持有量，命名為 'Total_Gross_Positions_Millions')。
                   如果獲取或處理失敗，則返回一個空的 Series。
    """
    logger.info(f"開始從 {len(ny_fed_positions_urls)} 個 URL 獲取 NY Fed 持有量數據 (使用 '一級交易pro.py' Cell 6 邏輯)。")
    all_positions_data = []  # 儲存從各個文件讀取的 Series
    processed_files_count = 0
    failed_files_info = [] # 記錄失敗文件及其原因

    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    })

    for i, url in enumerate(ny_fed_positions_urls):
        file_source_name = url.split('/')[-3] if len(url.split('/')) > 2 else f"File_{i+1}"
        logger.info(f"處理文件 {i + 1}/{len(ny_fed_positions_urls)} ({file_source_name}): {url}")

        try:
            # --- 1a. 下載 Excel 文件 ---
            logger.debug(f"文件 {file_source_name}: 正在下載...")
            response_excel = session.get(url, timeout=120)
            response_excel.raise_for_status()
            excel_content = io.BytesIO(response_excel.content)
            logger.info(f"文件 {file_source_name}: 下載成功。")

            # --- 1b. 解析 Excel (自動檢測表頭) ---
            logger.debug(f"文件 {file_source_name}: 正在解析 (嘗試自動檢測表頭)...")
            header_row_detected = None # 檢測到的表頭行號 (0-indexed)
            date_col_name_parsed = None # 解析時檢測到的日期列名
            data_positions_long = None

            possible_headers = [3, 4, 0] # 優先嘗試的表頭行號
            for h_val in possible_headers:
                try:
                    df_peek = pd.read_excel(excel_content, header=h_val, nrows=5, engine='openpyxl')
                    excel_content.seek(0)

                    cols_lower = [str(c).lower() for c in df_peek.columns]
                    ts_col_cand = next((col for col in df_peek.columns if str(col).lower() in ['time series', 'series name']), None)
                    val_col_cand = next((col for col in df_peek.columns if str(col).lower() in ['value (millions)', 'value']), None)
                    date_col_cand_for_idx = None
                    if len(df_peek.columns) > 0 :
                        first_col_name = df_peek.columns[0]
                        # 優先 'effective date'，其次才是第一列 (如果它像日期)
                        if 'effective date' in cols_lower:
                            date_col_cand_for_idx = df_peek.columns[cols_lower.index('effective date')]
                        elif not pd.to_datetime(df_peek.iloc[:, 0], errors='coerce').isna().all():
                             date_col_cand_for_idx = first_col_name

                    if ts_col_cand and val_col_cand and date_col_cand_for_idx:
                        header_row_detected = h_val
                        date_col_name_parsed = date_col_cand_for_idx
                        data_positions_long = pd.read_excel(excel_content, header=header_row_detected,
                                                            index_col=date_col_name_parsed,
                                                            parse_dates=True, engine='openpyxl')
                        logger.info(f"文件 {file_source_name}: 檢測到有效表頭在第 {header_row_detected + 1} 行, 日期列: '{date_col_name_parsed}'.")
                        excel_content.seek(0)
                        break
                except Exception:
                    excel_content.seek(0)
                    continue

            if data_positions_long is None:
                logger.warning(f"文件 {file_source_name}: 無法自動檢測有效的表頭行或日期/數值列。跳過此文件。")
                failed_files_info.append({'file': file_source_name, 'url': url, 'reason': '解析失敗 (無法檢測表頭/關鍵列)'})
                continue

            # --- 1c. 清理長格式數據 ---
            logger.debug(f"文件 {file_source_name}: 正在清理長格式數據...")
            if not isinstance(data_positions_long.index, pd.DatetimeIndex): # 確保索引是日期
                 data_positions_long.index = pd.to_datetime(data_positions_long.index, errors='coerce')
                 data_positions_long.dropna(subset=[data_positions_long.index.name], inplace=True) # 移除轉換失敗的
            data_positions_long.index = data_positions_long.index.normalize() # 標準化日期

            # 找到實際的 Time Series 和 Value 列名 (因為大小寫和名稱可能多樣)
            actual_ts_col_final = next((col for col in data_positions_long.columns if str(col).lower() in ['time series', 'series name']), None)
            actual_val_col_final = next((col for col in data_positions_long.columns if str(col).lower() in ['value (millions)', 'value']), None)

            if not actual_ts_col_final or not actual_val_col_final:
                logger.warning(f"文件 {file_source_name}: 清理後仍缺少 'Time Series' 或 'Value' 欄位。跳過。")
                failed_files_info.append({'file': file_source_name, 'url': url, 'reason': "缺少 'Time Series' 或 'Value' 欄位"})
                continue

            data_positions_long[actual_val_col_final] = pd.to_numeric(data_positions_long[actual_val_col_final], errors='coerce')
            initial_rows_count = len(data_positions_long)
            data_positions_long.dropna(subset=[actual_val_col_final, actual_ts_col_final], inplace=True)
            logger.debug(f"文件 {file_source_name}: 清理完成 (移除 {initial_rows_count - len(data_positions_long)} 行無效數據)。")

            if data_positions_long.empty:
                logger.warning(f"文件 {file_source_name}: 清理後無有效數據。跳過。")
                failed_files_info.append({'file': file_source_name, 'url': url, 'reason': '清理後無數據'})
                continue

            # --- 1d. 轉換為寬格式 ---
            logger.debug(f"文件 {file_source_name}: 正在轉換為寬格式...")
            try:
                # 重置索引以將日期變為列 (原日期索引名變為第一列的列名)
                data_positions_long.reset_index(inplace=True)
                # 此時 date_col_name_parsed 應該是第一列的名稱

                # 處理可能的重複項 (同一天同一序列) - 在 pivot_table 中用 aggfunc='mean'
                data_positions_wide = pd.pivot_table(
                    data_positions_long,
                    index=date_col_name_parsed, # 使用解析時得到的日期列名
                    columns=actual_ts_col_final,
                    values=actual_val_col_final,
                    aggfunc='mean'
                )
                logger.info(f"文件 {file_source_name}: 轉換寬格式成功 ({len(data_positions_wide)} 行 x {len(data_positions_wide.columns)} 欄)。")
            except Exception as e_pivot:
                logger.error(f"文件 {file_source_name}: 轉換寬格式失敗: {e_pivot}。跳過。", exc_info=True)
                failed_files_info.append({'file': file_source_name, 'url': url, 'reason': f'Pivot失敗: {e_pivot}'})
                continue

            # --- 1e. 加總持有量 ---
            target_cols_list_for_sum = []
            source_type_id = "未知"

            if 'SBN' in url.upper():
                 source_type_id = "SBN"
                 target_cols_list_for_sum = [c for c in data_positions_wide.columns if isinstance(c, str) and c.startswith('PDPOSGSC-')]
            else: # 檢查 sbp_cols_config (例如 SBP2013, SBP2001)
                for config_key_from_param, cols_in_config in sbp_cols_config.items():
                    if config_key_from_param.upper() in url.upper():
                        source_type_id = config_key_from_param
                        target_cols_list_for_sum = cols_in_config
                        break

            if not target_cols_list_for_sum:
                 logger.warning(f"文件 {file_source_name}: 未找到用於加總的目標欄位規則 ({source_type_id})。跳過加總。")
                 failed_files_info.append({'file': file_source_name, 'url': url, 'reason': f'無加總規則 ({source_type_id})'})
                 continue

            actual_cols_in_df_to_sum = [c for c in target_cols_list_for_sum if c in data_positions_wide.columns]

            if not actual_cols_in_df_to_sum:
                 logger.warning(f"文件 {file_source_name}: 配置的目標欄位 ({source_type_id}) 在數據中均未找到。跳過加總。")
                 failed_files_info.append({'file': file_source_name, 'url': url, 'reason': f'目標欄位未找到 ({source_type_id})'})
                 continue

            if len(actual_cols_in_df_to_sum) < len(target_cols_list_for_sum):
                 missing_cols_list = set(target_cols_list_for_sum) - set(actual_cols_in_df_to_sum)
                 logger.warning(f"文件 {file_source_name}: 部分目標欄位 ({source_type_id}) 未找到: {missing_cols_list}")

            logger.info(f"文件 {file_source_name}: 正在加總 {len(actual_cols_in_df_to_sum)} 個欄位 ({source_type_id}, 單位: 百萬美元)...")
            try:
                for col_name in actual_cols_in_df_to_sum: # 確保數值類型
                    data_positions_wide[col_name] = pd.to_numeric(data_positions_wide[col_name], errors='coerce')

                daily_total_millions_series = data_positions_wide[actual_cols_in_df_to_sum].sum(axis=1, skipna=True)
                daily_total_millions_series = daily_total_millions_series.dropna()
                daily_total_millions_series = daily_total_millions_series[daily_total_millions_series != 0] # 移除0值

                if not daily_total_millions_series.empty:
                     all_positions_data.append(daily_total_millions_series)
                     processed_files_count += 1
                     logger.info(f"文件 {file_source_name}: 成功加總並清理，獲得 {len(daily_total_millions_series)} 筆數據。")
                else:
                     logger.warning(f"文件 {file_source_name}: 加總後未能計算出有效的非零數據。")
                     failed_files_info.append({'file': file_source_name, 'url': url, 'reason': '加總後無有效數據'})
            except Exception as e_sum:
                logger.error(f"文件 {file_source_name}: 加總欄位時出錯: {e_sum}。跳過。", exc_info=True)
                failed_files_info.append({'file': file_source_name, 'url': url, 'reason': f'加總失敗: {e_sum}'})
                continue

        except requests.exceptions.RequestException as e_req:
            logger.error(f"文件 {file_source_name}: 下載失敗: {e_req}。跳過。", exc_info=True)
            failed_files_info.append({'file': file_source_name, 'url': url, 'reason': f'下載失敗: {e_req}'})
        except pd.errors.EmptyDataError:
            logger.warning(f"文件 {file_source_name}: Excel 文件為空或無數據可讀。跳過。")
            failed_files_info.append({'file': file_source_name, 'url': url, 'reason': 'Excel文件為空'})
        except ValueError as e_val:
            logger.warning(f"文件 {file_source_name}: 處理時發生數值或格式錯誤: {e_val}。跳過。")
            failed_files_info.append({'file': file_source_name, 'url': url, 'reason': f'數值/格式錯誤: {e_val}'})
        except Exception as e_file:
            logger.error(f"文件 {file_source_name}: 處理時發生未預期錯誤: {e_file}。跳過。", exc_info=True)
            failed_files_info.append({'file': file_source_name, 'url': url, 'reason': f'未知處理錯誤: {e_file}'})

    logger.info(f"NY Fed 文件處理循環結束。成功處理 {processed_files_count}/{len(ny_fed_positions_urls)} 個文件。")
    if failed_files_info:
        logger.warning(f"以下 NY Fed 文件處理失敗或被跳過:")
        for item in failed_files_info: # 修正迭代變數名
            logger.warning(f"  - 文件: {item['file']}, URL: {item['url']}, 原因: {item['reason']}")


    # --- 2. 合併所有文件的持有量數據 ---
    if not all_positions_data:
        logger.warning("未能從任何 NY Fed 文件中成功提取持有量數據。返回空 Series。")
        return pd.Series(dtype='float64', name='Total_Gross_Positions_Millions')

    try:
        logger.info("正在合併所有成功處理的 NY Fed 文件數據...")
        combined_positions_series = pd.concat(all_positions_data)
        combined_positions_series = combined_positions_series.sort_index()
        final_nyfed_series_result = combined_positions_series.groupby(level=0).last() # 保留重疊日期的最新值
        final_nyfed_series_result.name = 'Total_Gross_Positions_Millions'
        final_nyfed_series_result = final_nyfed_series_result.dropna()
        final_nyfed_series_result = final_nyfed_series_result[final_nyfed_series_result != 0]

        if final_nyfed_series_result.empty:
            logger.warning("合併所有 NY Fed 文件數據後，最終序列為空或全為零值。")
            return pd.Series(dtype='float64', name='Total_Gross_Positions_Millions')
        else:
            logger.info(f"NY Fed 持有量數據合併完成。最終序列包含 {len(final_nyfed_series_result)} 筆有效數據 "
                        f"(從 {final_nyfed_series_result.index.min().strftime('%Y-%m-%d')} 到 {final_nyfed_series_result.index.max().strftime('%Y-%m-%d')})。")
            return final_nyfed_series_result
    except Exception as e_concat:
        logger.error(f"合併 NY Fed 持有量數據時出錯: {e_concat}", exc_info=True)
        return pd.Series(dtype='float64', name='Total_Gross_Positions_Millions')


if __name__ == '__main__':
    # 此處為測試代碼，實際運行時不會執行，主要用於開發時的模組獨立測試。
    # 設定基礎日誌以查看測試輸出
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger.info("data_fetcher.py 模組被直接執行 (用於測試)。")

    # 測試配置 (模擬 config/project_config.yaml 的部分內容)
    mock_config = {
        "api_keys": {"fred": "c85a224a0e0d72a7bccb471c0021eb7b"}, # 請替換為您的有效 FRED API Key
        "data_fetching": {
            "fred_series_map": {
                'SOFR': 'SOFR',
                'DGS10': 'DGS10',
                'DTB3': 'DTB3' # 3個月期國庫券利率，用於 TLT IV
            },
            "yahoo_tickers_map": {
                'TLT_Close': 'TLT', # 用於 TLT IV 計算中的標的價格
                # '^VIX': 'VIX_Yahoo' # 若要測試 Yahoo VIX
            },
            "fred_move_ticker": "MOVE", # 假設 FRED MOVE 的 ID 是 'MOVE' (需確認)
            "ny_fed_positions_urls": [
                "https://markets.newyorkfed.org/api/pd/get/SBN2024/timeseries/PDPOSGSC-L2_PDPOSGSC-G2L3_PDPOSGSC-G3L6_PDPOSGSC-G6L7_PDPOSGSC-G7L11_PDPOSGSC-G11L21_PDPOSGSC-G21.xlsx"
            ], # 僅測試一個 SBN 文件以節省時間
            "sbp_cols_config": {
                'sbp2013_cols_to_sum': [], # 測試時可留空
                'sbp2001_cols_to_sum': []
            }
        }
    }
    test_start_date = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
    test_end_date = datetime.now().strftime('%Y-%m-%d')

    logger.info(f"\n--- 測試 fetch_fred_data ---")
    fred_df_test = fetch_fred_data(
        mock_config["api_keys"]["fred"],
        test_start_date,
        test_end_date,
        mock_config["data_fetching"]["fred_series_map"]
    )
    if fred_df_test is not None: logger.info(f"FRED 測試數據 (尾部):\n{fred_df_test.tail()}")

    logger.info(f"\n--- 測試 fetch_yahoo_data ---")
    yahoo_df_test = fetch_yahoo_data(
        test_start_date,
        test_end_date,
        mock_config["data_fetching"]["yahoo_tickers_map"]
    )
    if yahoo_df_test is not None: logger.info(f"Yahoo 測試數據 (尾部):\n{yahoo_df_test.tail()}")

    logger.info(f"\n--- 測試 get_move_index ---")
    move_index_test = get_move_index(test_start_date, test_end_date, mock_config)
    if move_index_test is not None: logger.info(f"MOVE 指數測試數據 (尾部):\n{move_index_test.tail()}")

    logger.info(f"\n--- 測試 get_vix_index ---")
    vix_index_test = get_vix_index(test_start_date, test_end_date, mock_config)
    if vix_index_test is not None: logger.info(f"VIX 指數測試數據 (尾部):\n{vix_index_test.tail()}")

    # nyfed_data 的測試維持原樣，因為它依賴外部 URL
    logger.info(f"\n--- 測試 fetch_nyfed_data (使用設定檔中的部分 URL) ---")
    nyfed_series_test = fetch_nyfed_data(
        mock_config["data_fetching"]["ny_fed_positions_urls"],
        mock_config["data_fetching"]["sbp_cols_config"]
    )
    if nyfed_series_test is not None: logger.info(f"NY Fed 持有量測試數據 (尾部):\n{nyfed_series_test.tail()}")

    logger.info("data_fetcher.py 測試執行完畢。")
