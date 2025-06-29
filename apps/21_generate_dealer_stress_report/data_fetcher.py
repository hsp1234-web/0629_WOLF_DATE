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


# fetch_nyfed_data 保持不變 (與前一版本相同)
def fetch_nyfed_data(urls_config: List[str], sbp_cols_config: Dict[str, List[str]]) -> Optional[pd.Series]:
    """
    從 NY Fed 網站下載並處理一級交易商的公債持有量數據。
    (此函式邏輯與先前版本相同，此處為保持完整性而複製)
    """
    logger.info(f"主函式：從 NY Fed 獲取並處理持有量數據，共 {len(urls_config)} 個文件...")
    all_positions_data = []
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    })

    for i, url in enumerate(urls_config):
        file_source_name = url.split('/')[-3] if len(url.split('/')) > 2 else f"File_{i+1}"
        logger.info(f"  處理 NY Fed 文件 {i+1}/{len(urls_config)} ({file_source_name})...")
        try:
            logger.debug(f"    正在下載: {url}")
            response_excel = session.get(url, timeout=120)
            response_excel.raise_for_status()
            excel_content = io.BytesIO(response_excel.content)
            logger.debug("    下載完成.")

            logger.debug("    正在解析 (嘗試自動檢測表頭)...")
            header_row = None; date_col_name = None; data_positions_long = None
            possible_headers = [3, 4, 0] # 根據觀察，表頭通常在這些行
            for h_idx, h in enumerate(possible_headers):
                try:
                    # 嘗試讀取少量行來判斷格式
                    df_peek = pd.read_excel(excel_content, header=h, nrows=5, engine='openpyxl')
                    excel_content.seek(0) # 重置指針以便後續完整讀取或再次嘗試

                    cols_lower = [str(c).lower() for c in df_peek.columns]
                    # 關鍵列名變體
                    ts_col_variants = ['time series', 'series name']
                    val_col_variants = ['value (millions)', 'value']
                    date_col_variants = ['effective date', 'as of date', df_peek.columns[0] if len(df_peek.columns) > 0 else None]

                    actual_ts_col_name = next((col for col_variant in ts_col_variants if col_variant in cols_lower for col in df_peek.columns if str(col).lower() == col_variant), None)
                    actual_val_col_name = next((col for col_variant in val_col_variants if col_variant in cols_lower for col in df_peek.columns if str(col).lower() == col_variant), None)
                    actual_date_col_name = next((col for col_variant in date_col_variants if col_variant and (str(col_variant).lower() in cols_lower or col_variant == df_peek.columns[0]) for col in df_peek.columns if str(col).lower() == str(col_variant).lower() or col == col_variant), None)

                    if actual_ts_col_name and actual_val_col_name and actual_date_col_name:
                        header_row = h
                        date_col_name = actual_date_col_name # 使用檢測到的日期列名
                        data_positions_long = pd.read_excel(excel_content, header=header_row,
                                                            index_col=date_col_name, parse_dates=True,
                                                            engine='openpyxl')
                        logger.debug(f"    檢測到有效表頭在第 {header_row+1} 行, 日期列: '{date_col_name}' (嘗試 {h_idx+1}/{len(possible_headers)})")
                        break # 找到有效的表頭
                except Exception as peek_err:
                    logger.debug(f"    嘗試 header={h} (嘗試 {h_idx+1}) 解析失敗: {peek_err}")
                    excel_content.seek(0) # 出錯也要重置指針
                    continue

            if data_positions_long is None:
                logger.warning(f"    文件 {file_source_name}: 無法自動檢測有效的表頭行或關鍵列。跳過此文件。")
                continue

            logger.debug("    正在清理長格式數據...")
            # 確保索引是 DatetimeIndex 並標準化 (已在讀取時 parse_dates=True, index_col=date_col_name)
            # date_col_name 是從 df_peek.columns[] 中獲取的，它本身就是有效的列名
            # data_positions_long 的索引就是 parse_dates 的結果

            if not isinstance(data_positions_long.index, pd.DatetimeIndex):
                # 這種情況理論上不應發生，因為 parse_dates=True
                logger.warning(f"    文件 {file_source_name}: 索引不是 DatetimeIndex，嘗試強制轉換。")
                data_positions_long.index = pd.to_datetime(data_positions_long.index, errors='coerce')

            # 移除日期索引轉換失敗的行 (變成 NaT)
            data_positions_long = data_positions_long[data_positions_long.index.notna()]

            if data_positions_long.empty:
                logger.warning(f"    文件 {file_source_name}: 日期轉換或索引處理後無數據。跳過。")
                continue

            data_positions_long.index = data_positions_long.index.normalize() # 標準化為午夜
            # 在這裡，data_positions_long.index.name 應該等於 date_col_name (被用作 index_col 的那個原始列名)
            # 如果原始Excel的日期列沒有名字，則 data_positions_long.index.name 可能為 None

            # 再次確認 Time Series 和 Value 列名 (因為 read_excel 後列名可能變化)
            cols_lower_full = [str(c).lower() for c in data_positions_long.columns]
            actual_ts_col = next((col for col_variant in ts_col_variants if col_variant in cols_lower_full for col in data_positions_long.columns if str(col).lower() == col_variant), None)
            actual_val_col = next((col for col_variant in val_col_variants if col_variant in cols_lower_full for col in data_positions_long.columns if str(col).lower() == col_variant), None)

            if not actual_ts_col or not actual_val_col:
                logger.warning(f"    文件 {file_source_name}: 清理後缺少 '{ts_col_variants[0]}' 或 '{val_col_variants[0]}' 欄位。跳過。")
                continue

            data_positions_long[actual_val_col] = pd.to_numeric(data_positions_long[actual_val_col], errors='coerce')
            data_positions_long.dropna(subset=[actual_val_col, actual_ts_col], inplace=True) # 移除數值無效或 Time Series 為空的行
            logger.debug("    長格式數據清理完成.")
            if data_positions_long.empty:
                logger.warning(f"    文件 {file_source_name}: 清理後無有效數據。跳過。")
                continue

            logger.debug("    正在轉換為寬格式...")
            # 重置索引以將日期變為列，方便後續 pivot
            data_positions_long.reset_index(inplace=True)
            # date_col_actual 現在是重置索引後的第一列，即原來的索引名
            date_col_actual_for_pivot = data_positions_long.columns[0]

            # 處理可能的重複項 (同一天同一序列可能有多行) - 取平均值
            data_positions_long = data_positions_long.groupby(
                [date_col_actual_for_pivot, actual_ts_col]
            )[actual_val_col].mean().reset_index()

            data_positions_wide = pd.pivot_table(
                data_positions_long,
                index=date_col_actual_for_pivot, # 使用重置索引後的日期列名
                columns=actual_ts_col,
                values=actual_val_col,
                aggfunc='mean' # 理論上已處理重複，但保留以防萬一
            )
            logger.debug(f"    寬格式轉換成功 ({len(data_positions_wide)} 行 x {len(data_positions_wide.columns)} 欄)。")

            if not data_positions_wide.empty:
                target_cols_to_sum = []; source_type_name = "未知"
                # 根據 URL 中的關鍵字判斷文件類型並獲取加總欄位列表
                if 'SBN' in url.upper(): # Standard SBN file
                     source_type_name = "SBN"
                     target_cols_to_sum = [c for c in data_positions_wide.columns if isinstance(c, str) and c.upper().startswith('PDPOSGSC-')]
                elif 'SBP2013' in url.upper():
                     source_type_name = "SBP2013"; target_cols_to_sum = sbp_cols_config.get('sbp2013_cols_to_sum', [])
                elif 'SBP2001' in url.upper():
                     source_type_name = "SBP2001"; target_cols_to_sum = sbp_cols_config.get('sbp2001_cols_to_sum', [])

                if not target_cols_to_sum:
                     logger.warning(f"    文件 {file_source_name} ({source_type_name}): 未找到用於加總的目標欄位規則或配置。跳過加總。")
                     continue

                actual_cols_present_for_sum = [c for c in target_cols_to_sum if c in data_positions_wide.columns]
                if not actual_cols_present_for_sum:
                     logger.warning(f"    文件 {file_source_name} ({source_type_name}): 預期加總的目標欄位均未在文件中找到。跳過加總。")
                     continue
                if len(actual_cols_present_for_sum) < len(target_cols_to_sum):
                    missing_cols_for_sum = set(target_cols_to_sum) - set(actual_cols_present_for_sum)
                    logger.warning(f"    文件 {file_source_name} ({source_type_name}): 部分目標欄位未找到: {missing_cols_for_sum}")

                logger.debug(f"    正在加總 {len(actual_cols_present_for_sum)} 個欄位 ({source_type_name}, 單位: 百萬美元)...")
                # 確保參與加總的列是數值類型
                for col_to_convert in actual_cols_present_for_sum:
                    data_positions_wide[col_to_convert] = pd.to_numeric(data_positions_wide[col_to_convert], errors='coerce')

                daily_total_millions = data_positions_wide[actual_cols_present_for_sum].sum(axis=1, skipna=True)
                daily_total_millions = daily_total_millions.dropna() # 移除加總結果為 NaN 的行
                daily_total_millions = daily_total_millions[daily_total_millions != 0] # 移除加總結果為 0 的行

                if not daily_total_millions.empty:
                     all_positions_data.append(daily_total_millions)
                     logger.info(f"    文件 {file_source_name} ({source_type_name}) 成功處理並加總，獲得 {len(daily_total_millions)} 筆有效數據。")
                else:
                     logger.warning(f"    文件 {file_source_name} ({source_type_name}): 加總後未能計算出有效的非零數據。")
        except requests.exceptions.RequestException as e_req_ny:
            logger.error(f"    文件 {file_source_name}: 下載失敗: {e_req_ny}")
        except Exception as e_file_ny:
            logger.error(f"    文件 {file_source_name}: 處理時發生未預期錯誤: {e_file_ny}", exc_info=True)

    if not all_positions_data:
        logger.warning("未能成功處理任何 NY Fed 持有量數據文件。")
        return None # 或返回空的 Series

    try:
        combined_positions = pd.concat(all_positions_data)
        # 處理索引可能不是 DatetimeIndex 的情況 (雖然前面已盡力轉換)
        if not isinstance(combined_positions.index, pd.DatetimeIndex):
            combined_positions.index = pd.to_datetime(combined_positions.index, errors='coerce')
            combined_positions = combined_positions.dropna(axis=0, subset=[combined_positions.index.name]) # 移除轉換失敗的

        combined_positions = combined_positions.sort_index()
        # 處理重疊日期，保留最後（通常是最新）的值
        final_nyfed_series = combined_positions.groupby(level=0).last()
        final_nyfed_series.name = 'Total_Gross_Positions_Millions' # 命名 Series

        # 再次確保沒有 NaN 或 0
        final_nyfed_series = final_nyfed_series.dropna()
        final_nyfed_series = final_nyfed_series[final_nyfed_series != 0]

        if not final_nyfed_series.empty:
            logger.info(f"NY Fed 持有量數據合併完成，最終序列包含 {len(final_nyfed_series)} 筆數據 (從 {final_nyfed_series.index.min().date()} 到 {final_nyfed_series.index.max().date()})。")
            return final_nyfed_series
        else:
            logger.warning("NY Fed 數據合併後，最終序列為空或全為零值。")
            return None
    except Exception as e_concat_ny:
        logger.error(f"合併 NY Fed 持有量數據時出錯: {e_concat_ny}", exc_info=True)
        return None


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
