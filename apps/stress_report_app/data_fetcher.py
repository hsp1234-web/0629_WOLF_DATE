# -*- coding: utf-8 -*-
"""
數據獲取模組 (data_fetcher.py) - v3.1 (重構為多個獨立函式)

功能：
- get_fred_base_data: 從 FRED 獲取基礎經濟數據。
- get_yahoo_other_data: 從 Yahoo Finance 獲取次要市場數據。
- get_move_index: 獲取 MOVE 指數 (主要從 Yahoo, FRED 作為備援)。
- get_vix_index: 獲取 VIX 指數 (主要從 FRED, Yahoo 作為備援)。
- fetch_nyfed_data: 獲取並處理 NY Fed 的一級交易商持倉數據。
"""

import pandas as pd
import numpy as np
import yfinance as yf
from fredapi import Fred
import requests
import io
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any

# 由於新的 run.py 使用了絕對路徑導入，這裡也調整
# logger 將由 src.utils.logger 傳入或在各函式中按需獲取
# from src.utils.logger import setup_logger # 假設 logger 會在呼叫者那裡設定或按需設定
import logging # 保留標準 logging 以便在函式內部獲取 logger

# logger = setup_logger(__name__) # 理想情況下，logger 實例應從外部傳入或統一管理

def get_fred_data_series(
    series_id: str,
    start_date_dt: datetime,
    end_date_dt: datetime,
    api_key: str,
    logger_instance: Optional[logging.Logger] = None
) -> Optional[pd.Series]:
    """
    (輔助函式) 從 FRED 獲取單個經濟時間序列數據。
    """
    current_logger = logger_instance if logger_instance else logging.getLogger(__name__)
    current_logger.debug(f"輔助函式：嘗試從 FRED 獲取序列 '{series_id}' (日期範圍: {start_date_dt.strftime('%Y-%m-%d')} 至 {end_date_dt.strftime('%Y-%m-%d')})")
    try:
        fred = Fred(api_key=api_key)
        s = fred.get_series(series_id) # 先獲取完整序列

        if s.empty or s.isna().all():
            current_logger.warning(f"FRED 序列 '{series_id}' 返回數據為空或全為 NaN (在應用日期範圍之前)。")
            return None

        s.index = pd.to_datetime(s.index).tz_localize(None) # 標準化索引
        s = s.sort_index()

        # 日期篩選
        s_filtered = s[(s.index >= start_date_dt) & (s.index <= end_date_dt)]

        if s_filtered.empty:
            current_logger.warning(f"FRED 序列 '{series_id}' 在日期範圍 {start_date_dt.strftime('%Y-%m-%d')} 到 {end_date_dt.strftime('%Y-%m-%d')} 內無數據。")
            return None

        current_logger.debug(f"FRED 序列 '{series_id}' 獲取並篩選後成功，共 {len(s_filtered)} 筆數據。")
        return s_filtered
    except requests.exceptions.RequestException as re:
        current_logger.error(f"FRED 序列 '{series_id}' 獲取時發生網路錯誤: {re}")
        return None
    except ValueError as ve:
        current_logger.error(f"FRED 序列 '{series_id}' 獲取時發生錯誤 (可能是無效的序列 ID 或 API Key 問題): {ve}")
        return None
    except Exception as e:
        current_logger.error(f"獲取 FRED 序列 '{series_id}' 時發生未預期錯誤: {e}", exc_info=True)
        return None

def get_yahoo_finance_series(
    ticker_symbol: str,
    start_date_dt: datetime,
    end_date_dt: datetime,
    column: str = 'Close',
    logger_instance: Optional[logging.Logger] = None
) -> Optional[pd.Series]:
    """
    (輔助函式) 從 Yahoo Finance 獲取單個 Ticker 的指定欄位數據。
    """
    current_logger = logger_instance if logger_instance else logging.getLogger(__name__)
    start_date_str = start_date_dt.strftime('%Y-%m-%d')
    end_date_str = end_date_dt.strftime('%Y-%m-%d')
    current_logger.debug(f"輔助函式：嘗試從 Yahoo Finance 獲取 Ticker '{ticker_symbol}' 的 '{column}' 欄位 (從 {start_date_str} 至 {end_date_str})")
    try:
        ticker = yf.Ticker(ticker_symbol)
        end_date_for_yf = (end_date_dt + timedelta(days=1)).strftime('%Y-%m-%d')
        hist = ticker.history(start=start_date_str, end=end_date_for_yf, auto_adjust=True)

        if hist.empty or column not in hist.columns:
            current_logger.warning(f"Yahoo Finance Ticker '{ticker_symbol}' 返回數據為空或缺少 '{column}' 欄位。")
            return None

        series_data = hist[column].copy()
        series_data.index = pd.to_datetime(series_data.index.date) # 標準化索引
        series_data = series_data.sort_index()
        current_logger.debug(f"Yahoo Finance Ticker '{ticker_symbol}' 的 '{column}' 欄位獲取成功，共 {len(series_data)} 筆原始數據。")
        return series_data
    except Exception as e:
        current_logger.error(f"獲取 Yahoo Finance Ticker '{ticker_symbol}' 時發生錯誤: {e}", exc_info=True)
        return None

# --- 重構後的獨立函式 ---

def get_fred_base_data(
    api_key: str,
    start_date_dt: datetime,
    end_date_dt: datetime,
    config: Dict[str, Any],
    logger_instance: Optional[logging.Logger] = None
) -> pd.DataFrame:
    """
    從 FRED 獲取基礎經濟數據（SOFR, DGS10, DGS2, RRP, DTB3 等），
    並進行頻率處理與對齊到業務日。
    參考 `一級交易pro.py` (Cell 4) 邏輯。
    """
    current_logger = logger_instance if logger_instance else logging.getLogger(__name__)
    series_map = config.get('data_fetching', {}).get('fred_series_map', {})
    current_logger.info(f"開始從 FRED 獲取 {len(series_map)} 個基礎經濟序列 (從 {start_date_dt.strftime('%Y-%m-%d')} 到 {end_date_dt.strftime('%Y-%m-%d')})...")

    fred_data_temp = {}
    daily_index_for_fred = pd.date_range(start=start_date_dt, end=end_date_dt, freq='D')
    business_day_index = pd.date_range(start=start_date_dt, end=end_date_dt, freq='B')

    for target_name, series_id in series_map.items():
        current_logger.info(f"  - 正在抓取 FRED {target_name} ({series_id})...")
        series_data = get_fred_data_series(series_id, start_date_dt, end_date_dt, api_key, current_logger)

        if series_data is not None and not series_data.empty:
            original_count = len(series_data.dropna())
            s_aligned_daily = series_data.reindex(daily_index_for_fred) # 先對齊到日曆日

            # 處理頻率：週頻或月頻數據向前填充，日頻數據也填充以確保完整性
            if len(series_data.index) < 3 and original_count > 0 : # 數據點過少但有值
                 current_logger.debug(f"    序列 '{target_name}' 的數據點 ({original_count}) 過少，直接進行每日向前填充。")
                 s_aligned_daily = s_aligned_daily.ffill()
            elif original_count > 0: # 數據點足夠多
                freq_str = pd.infer_freq(series_data.dropna().index) # 在dropna後推斷頻率
                if freq_str and ('W' in freq_str.upper() or 'M' in freq_str.upper()):
                    current_logger.debug(f"    序列 '{target_name}' 是 {freq_str} 頻率，將重新索引到每日並向前填充。")
                    s_aligned_daily = s_aligned_daily.ffill()
                else: # 日頻或無法推斷，直接對齊每日並填充
                    current_logger.debug(f"    序列 '{target_name}' 是日頻或無法推斷頻率，對齊每日並向前填充。")
                    s_aligned_daily = s_aligned_daily.ffill() # 保證所有日曆日都有值，便於後續對齊業務日

            aligned_count = s_aligned_daily.count()
            current_logger.info(f"    FRED {target_name} ({series_id}) 獲取成功 (原始 {original_count} -> 每日填充後 {aligned_count} 點)。")
            fred_data_temp[target_name] = s_aligned_daily
        else:
            current_logger.warning(f"    FRED 序列 '{target_name}' ({series_id}) 獲取失敗或無數據。將以 NaN 填充。")
            fred_data_temp[target_name] = pd.Series(dtype='float64', index=daily_index_for_fred)

    if not fred_data_temp:
        current_logger.warning("未能從 FRED 抓取任何基礎數據。返回空 DataFrame。")
        return pd.DataFrame(index=business_day_index)

    combined_df = pd.DataFrame(fred_data_temp)
    if combined_df.empty:
        current_logger.warning("FRED 基礎數據合併後為空 DataFrame。")
        return pd.DataFrame(index=business_day_index)

    # 最後統一對齊到業務日索引，並再次向前填充以處理週末/假日導入的 NaN
    fred_final_df = combined_df.reindex(business_day_index).ffill()

    # 檢查是否所有列都完全是 NaN
    if fred_final_df.isna().all().all():
        current_logger.warning("FRED 基礎數據對齊到業務日後全為 NaN。")
        return pd.DataFrame(index=business_day_index)

    current_logger.info(f"FRED 基礎數據已獲取並對齊至業務日索引 ({len(fred_final_df)} 行)。")
    return fred_final_df

def get_yahoo_other_data(
    start_date_dt: datetime,
    end_date_dt: datetime,
    config: Dict[str, Any],
    logger_instance: Optional[logging.Logger] = None
) -> pd.DataFrame:
    """
    從 Yahoo Finance 獲取次要市場數據（例如 TLT）。
    並對齊到業務日。
    參考 `一級交易pro.py` (Cell 5) 邏輯。
    """
    current_logger = logger_instance if logger_instance else logging.getLogger(__name__)
    tickers_map = config.get('data_fetching', {}).get('yahoo_tickers_map', {})
    # 過濾掉 MOVE 和 VIX，因為它們由專用函式處理
    other_tickers_map = {
        name: ticker
        for name, ticker in tickers_map.items()
        if ticker not in ['^MOVE', '^VIX'] # 假設 ^MOVE 和 ^VIX 是它們在 map 中的 ticker 符號
    }

    current_logger.info(f"開始從 Yahoo Finance 獲取 {len(other_tickers_map)} 個次要 Ticker (從 {start_date_dt.strftime('%Y-%m-%d')} 到 {end_date_dt.strftime('%Y-%m-%d')})...")

    business_day_index = pd.date_range(start=start_date_dt, end=end_date_dt, freq='B')
    final_yahoo_df = pd.DataFrame(index=business_day_index)

    if not other_tickers_map:
        current_logger.info("Yahoo Finance 的 tickers_map (排除 MOVE/VIX 後) 為空，無需獲取其他數據。")
        return final_yahoo_df

    for target_col_name, ticker_symbol in other_tickers_map.items():
        current_logger.info(f"  - 正在請求 Yahoo Finance {ticker_symbol} (目標欄位: {target_col_name})...")
        # 假設我們總是獲取 'Close' 價格作為示例
        series_data = get_yahoo_finance_series(ticker_symbol, start_date_dt, end_date_dt, 'Close', current_logger)

        if series_data is not None and not series_data.empty:
            aligned_series = series_data.reindex(business_day_index, method='ffill')
            final_yahoo_df[target_col_name] = aligned_series
            current_logger.info(f"    成功抓取並對齊 {len(series_data)} 筆 {ticker_symbol} 數據到 {target_col_name}。")
        else:
            current_logger.warning(f"    未能成功抓取或處理 Yahoo Finance {ticker_symbol} 的數據。將以 NaN 填充 {target_col_name}。")
            final_yahoo_df[target_col_name] = np.nan

    current_logger.info(f"Yahoo Finance 次要數據獲取完成。最終 DataFrame 維度: {final_yahoo_df.shape}")
    return final_yahoo_df

def get_move_index(
    api_key: str,  # FRED API Key for fallback
    start_date_dt: datetime,
    end_date_dt: datetime,
    config: Dict[str, Any],
    logger_instance: Optional[logging.Logger] = None
) -> Optional[pd.Series]:
    """
    獲取 MOVE 指數。主要從 Yahoo Finance 抓取 (^MOVE)，FRED (MOVEIX) 作為備援。
    參考 `一級交易pro.py` (Cell 5 for Yahoo, Cell 4 for FRED)。
    """
    current_logger = logger_instance if logger_instance else logging.getLogger(__name__)
    data_fetching_config = config.get('data_fetching', {})

    # 主要來源：Yahoo Finance
    # 從 yahoo_tickers_map 找到 MOVE 對應的 ticker，預設為 "^MOVE"
    yahoo_move_ticker_name = None
    yahoo_move_ticker_symbol = "^MOVE" # Default
    for name, symbol in data_fetching_config.get('yahoo_tickers_map', {}).items():
        if symbol.upper() == '^MOVE':
            yahoo_move_ticker_name = name
            yahoo_move_ticker_symbol = symbol
            break
    if not yahoo_move_ticker_name: # 如果map裡沒有定義，但我們仍想嘗試默認的^MOVE
        yahoo_move_ticker_name = "MOVE_Yahoo" # 給一個默認的列名

    current_logger.info(f"嘗試從 Yahoo Finance 獲取 MOVE 指數 (Ticker: {yahoo_move_ticker_symbol})...")
    move_series_yahoo = get_yahoo_finance_series(
        ticker_symbol=yahoo_move_ticker_symbol,
        start_date_dt=start_date_dt,
        end_date_dt=end_date_dt,
        column='Close',
        logger_instance=current_logger
    )
    if move_series_yahoo is not None and not move_series_yahoo.empty:
        current_logger.info(f"成功從 Yahoo Finance ({yahoo_move_ticker_symbol}) 獲取 MOVE 指數數據。")
        return move_series_yahoo.rename(yahoo_move_ticker_name or "MOVE_Index")

    # 備援來源：FRED
    fred_move_id = data_fetching_config.get('fred_move_ticker', 'MOVEIX') # 預設為 'MOVEIX'
    current_logger.warning(f"從 Yahoo Finance ({yahoo_move_ticker_symbol}) 獲取 MOVE 指數失敗。啟用備援：嘗試從 FRED (ID: {fred_move_id})...")
    if api_key and fred_move_id:
        move_series_fred = get_fred_data_series(
            series_id=fred_move_id,
            start_date_dt=start_date_dt,
            end_date_dt=end_date_dt,
            api_key=api_key,
            logger_instance=current_logger
        )
        if move_series_fred is not None and not move_series_fred.empty:
            current_logger.info(f"成功從 FRED (ID: {fred_move_id}) 獲取 MOVE 指數數據作為備援。")
            return move_series_fred.rename("MOVE_Index_FRED") # 標註來源
    else:
        current_logger.warning(f"FRED API Key 或 FRED MOVE Ticker ('{fred_move_id}') 未設定或無效，無法使用 FRED 作為 MOVE 指數的備援。")

    current_logger.error(f"無法從 Yahoo Finance ({yahoo_move_ticker_symbol}) 或 FRED ({fred_move_id}) 獲取 MOVE 指數數據。")
    return None

def get_vix_index(
    api_key: str,
    start_date_dt: datetime,
    end_date_dt: datetime,
    config: Dict[str, Any],
    logger_instance: Optional[logging.Logger] = None
) -> Optional[pd.Series]:
    """
    獲取 VIX 指數。主要從 FRED 抓取 (VIXCLS)，Yahoo Finance (^VIX) 作為備援。
    參考 `一級交易pro.py` (Cell 4 for FRED, Cell 5 for Yahoo)。
    """
    current_logger = logger_instance if logger_instance else logging.getLogger(__name__)
    data_fetching_config = config.get('data_fetching', {})

    # 主要來源：FRED
    # 嘗試從 fred_series_map 獲取 VIX 對應的 FRED ID，預設為 'VIXCLS'
    fred_vix_id = 'VIXCLS' # Default
    for name, fred_id in data_fetching_config.get('fred_series_map', {}).items():
        if name.upper() == 'VIX_FRED' or name.upper() == 'VIXCLS': # 假設設定檔中可能有 VIX_FRED
            fred_vix_id = fred_id
            break

    current_logger.info(f"嘗試從 FRED 獲取 VIX 指數 (ID: {fred_vix_id})...")
    if api_key:
        vix_series_fred = get_fred_data_series(
            series_id=fred_vix_id,
            start_date_dt=start_date_dt,
            end_date_dt=end_date_dt,
            api_key=api_key,
            logger_instance=current_logger
        )
        if vix_series_fred is not None and not vix_series_fred.empty:
            current_logger.info(f"成功從 FRED (ID: {fred_vix_id}) 獲取 VIX 指數數據。")
            return vix_series_fred.rename("VIX_Index")
    else:
        current_logger.warning(f"FRED API Key 未提供，無法從 FRED (ID: {fred_vix_id}) 獲取 VIX 指數。")

    # 備援來源：Yahoo Finance
    # 從 yahoo_tickers_map 找到 VIX 對應的 ticker，預設為 "^VIX"
    yahoo_vix_ticker_name = None
    yahoo_vix_ticker_symbol = "^VIX" # Default
    for name, symbol in data_fetching_config.get('yahoo_tickers_map', {}).items():
        if symbol.upper() == '^VIX':
            yahoo_vix_ticker_name = name
            yahoo_vix_ticker_symbol = symbol
            break
    if not yahoo_vix_ticker_name:
        yahoo_vix_ticker_name = "VIX_Yahoo"

    current_logger.warning(f"從 FRED ({fred_vix_id}) 獲取 VIX 指數失敗或 API Key 缺失。啟用備援：嘗試從 Yahoo Finance ({yahoo_vix_ticker_symbol})...")
    vix_series_yahoo = get_yahoo_finance_series(
        ticker_symbol=yahoo_vix_ticker_symbol,
        start_date_dt=start_date_dt,
        end_date_dt=end_date_dt,
        column='Close',
        logger_instance=current_logger
    )
    if vix_series_yahoo is not None and not vix_series_yahoo.empty:
        current_logger.info(f"成功從 Yahoo Finance ({yahoo_vix_ticker_symbol}) 獲取 VIX 指數數據作為備援。")
        return vix_series_yahoo.rename(yahoo_vix_ticker_name or "VIX_Index")

    current_logger.error(f"無法從 FRED ({fred_vix_id}) 或 Yahoo Finance ({yahoo_vix_ticker_symbol}) 獲取 VIX 指數數據。")
    return None

def fetch_nyfed_data(config: Dict[str, Any], logger_instance: Optional[logging.Logger] = None) -> Optional[pd.Series]:
    # TODO: 遷移或重用上次的正確邏輯 (參考 Cell 6)
    # 確保從 config['data_fetching']['ny_fed_positions_urls'] 和 config['data_fetching']['sbp_cols_config'] 獲取配置
    current_logger = logger_instance if logger_instance else logging.getLogger(__name__)
    ny_fed_positions_urls = config.get('data_fetching', {}).get('ny_fed_positions_urls', [])
    sbp_cols_config = config.get('data_fetching', {}).get('sbp_cols_config', {})

    current_logger.info(f"開始從 {len(ny_fed_positions_urls)} 個 URL 獲取 NY Fed 持有量數據 (使用 '一級交易pro.py' Cell 6 邏輯)。")
    all_positions_data = []
    processed_files_count = 0
    failed_files_info = []

    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    })

    if not ny_fed_positions_urls:
        current_logger.warning("NY Fed 持有量數據 URL 列表為空。返回空 Series。")
        return pd.Series(dtype='float64', name='Total_Gross_Positions_Millions')

    for i, url in enumerate(ny_fed_positions_urls):
        file_source_name = url.split('/')[-3] if len(url.split('/')) > 2 else f"File_{i+1}"
        current_logger.info(f"處理文件 {i + 1}/{len(ny_fed_positions_urls)} ({file_source_name}): {url}")

        try:
            current_logger.debug(f"文件 {file_source_name}: 正在下載...")
            response_excel = session.get(url, timeout=120)
            response_excel.raise_for_status()
            excel_content = io.BytesIO(response_excel.content)
            current_logger.info(f"文件 {file_source_name}: 下載成功。")

            current_logger.debug(f"文件 {file_source_name}: 正在解析 (嘗試自動檢測表頭)...")
            header_row_detected = None
            date_col_name_parsed = None
            data_positions_long = None
            possible_headers = [3, 4, 0]

            for h_val in possible_headers:
                try:
                    df_peek = pd.read_excel(excel_content, header=h_val, nrows=5, engine='openpyxl')
                    excel_content.seek(0)
                    cols_lower = [str(c).lower() for c in df_peek.columns]
                    ts_col_cand = next((col for col in df_peek.columns if str(col).lower() in ['time series', 'series name']), None)
                    val_col_cand = next((col for col in df_peek.columns if str(col).lower() in ['value (millions)', 'value']), None)
                    date_col_cand_for_idx = None
                    if len(df_peek.columns) > 0:
                        first_col_name = df_peek.columns[0]
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
                        current_logger.info(f"文件 {file_source_name}: 檢測到有效表頭在第 {header_row_detected + 1} 行, 日期列: '{date_col_name_parsed}'.")
                        excel_content.seek(0)
                        break
                except Exception:
                    excel_content.seek(0)
                    continue

            if data_positions_long is None:
                current_logger.warning(f"文件 {file_source_name}: 無法自動檢測有效的表頭行或日期/數值列。跳過此文件。")
                failed_files_info.append({'file': file_source_name, 'url': url, 'reason': '解析失敗 (無法檢測表頭/關鍵列)'})
                continue

            current_logger.debug(f"文件 {file_source_name}: 正在清理長格式數據...")
            if not isinstance(data_positions_long.index, pd.DatetimeIndex):
                 data_positions_long.index = pd.to_datetime(data_positions_long.index, errors='coerce')
                 data_positions_long.dropna(subset=[data_positions_long.index.name], inplace=True)
            data_positions_long.index = data_positions_long.index.normalize()

            actual_ts_col_final = next((col for col in data_positions_long.columns if str(col).lower() in ['time series', 'series name']), None)
            actual_val_col_final = next((col for col in data_positions_long.columns if str(col).lower() in ['value (millions)', 'value']), None)

            if not actual_ts_col_final or not actual_val_col_final:
                current_logger.warning(f"文件 {file_source_name}: 清理後仍缺少 'Time Series' 或 'Value' 欄位。跳過。")
                failed_files_info.append({'file': file_source_name, 'url': url, 'reason': "缺少 'Time Series' 或 'Value' 欄位"})
                continue

            data_positions_long[actual_val_col_final] = pd.to_numeric(data_positions_long[actual_val_col_final], errors='coerce')
            initial_rows_count = len(data_positions_long)
            data_positions_long.dropna(subset=[actual_val_col_final, actual_ts_col_final], inplace=True)
            current_logger.debug(f"文件 {file_source_name}: 清理完成 (移除 {initial_rows_count - len(data_positions_long)} 行無效數據)。")

            if data_positions_long.empty:
                current_logger.warning(f"文件 {file_source_name}: 清理後無有效數據。跳過。")
                failed_files_info.append({'file': file_source_name, 'url': url, 'reason': '清理後無數據'})
                continue

            current_logger.debug(f"文件 {file_source_name}: 正在轉換為寬格式...")
            try:
                data_positions_long.reset_index(inplace=True)
                data_positions_wide = pd.pivot_table(
                    data_positions_long,
                    index=date_col_name_parsed,
                    columns=actual_ts_col_final,
                    values=actual_val_col_final,
                    aggfunc='mean'
                )
                current_logger.info(f"文件 {file_source_name}: 轉換寬格式成功 ({len(data_positions_wide)} 行 x {len(data_positions_wide.columns)} 欄)。")
            except Exception as e_pivot:
                current_logger.error(f"文件 {file_source_name}: 轉換寬格式失敗: {e_pivot}。跳過。", exc_info=True)
                failed_files_info.append({'file': file_source_name, 'url': url, 'reason': f'Pivot失敗: {e_pivot}'})
                continue

            target_cols_list_for_sum = []
            source_type_id = "未知"
            if 'SBN' in url.upper():
                 source_type_id = "SBN"
                 target_cols_list_for_sum = [c for c in data_positions_wide.columns if isinstance(c, str) and c.startswith('PDPOSGSC-')]
            else:
                for config_key_from_param, cols_in_config in sbp_cols_config.items():
                    if config_key_from_param.upper() in url.upper():
                        source_type_id = config_key_from_param
                        target_cols_list_for_sum = cols_in_config
                        break

            if not target_cols_list_for_sum:
                 current_logger.warning(f"文件 {file_source_name}: 未找到用於加總的目標欄位規則 ({source_type_id})。跳過加總。")
                 failed_files_info.append({'file': file_source_name, 'url': url, 'reason': f'無加總規則 ({source_type_id})'})
                 continue

            actual_cols_in_df_to_sum = [c for c in target_cols_list_for_sum if c in data_positions_wide.columns]
            if not actual_cols_in_df_to_sum:
                 current_logger.warning(f"文件 {file_source_name}: 配置的目標欄位 ({source_type_id}) 在數據中均未找到。跳過加總。")
                 failed_files_info.append({'file': file_source_name, 'url': url, 'reason': f'目標欄位未找到 ({source_type_id})'})
                 continue
            if len(actual_cols_in_df_to_sum) < len(target_cols_list_for_sum):
                 missing_cols_list = set(target_cols_list_for_sum) - set(actual_cols_in_df_to_sum)
                 current_logger.warning(f"文件 {file_source_name}: 部分目標欄位 ({source_type_id}) 未找到: {missing_cols_list}")

            current_logger.info(f"文件 {file_source_name}: 正在加總 {len(actual_cols_in_df_to_sum)} 個欄位 ({source_type_id}, 單位: 百萬美元)...")
            try:
                for col_name in actual_cols_in_df_to_sum:
                    data_positions_wide[col_name] = pd.to_numeric(data_positions_wide[col_name], errors='coerce')
                daily_total_millions_series = data_positions_wide[actual_cols_in_df_to_sum].sum(axis=1, skipna=True)
                daily_total_millions_series = daily_total_millions_series.dropna()
                daily_total_millions_series = daily_total_millions_series[daily_total_millions_series != 0]
                if not daily_total_millions_series.empty:
                     all_positions_data.append(daily_total_millions_series)
                     processed_files_count += 1
                     current_logger.info(f"文件 {file_source_name}: 成功加總並清理，獲得 {len(daily_total_millions_series)} 筆數據。")
                else:
                     current_logger.warning(f"文件 {file_source_name}: 加總後未能計算出有效的非零數據。")
                     failed_files_info.append({'file': file_source_name, 'url': url, 'reason': '加總後無有效數據'})
            except Exception as e_sum:
                current_logger.error(f"文件 {file_source_name}: 加總欄位時出錯: {e_sum}。跳過。", exc_info=True)
                failed_files_info.append({'file': file_source_name, 'url': url, 'reason': f'加總失敗: {e_sum}'})
                continue
        except requests.exceptions.RequestException as e_req:
            current_logger.error(f"文件 {file_source_name}: 下載失敗: {e_req}。跳過。", exc_info=True)
            failed_files_info.append({'file': file_source_name, 'url': url, 'reason': f'下載失敗: {e_req}'})
        except pd.errors.EmptyDataError:
            current_logger.warning(f"文件 {file_source_name}: Excel 文件為空或無數據可讀。跳過。")
            failed_files_info.append({'file': file_source_name, 'url': url, 'reason': 'Excel文件為空'})
        except ValueError as e_val:
            current_logger.warning(f"文件 {file_source_name}: 處理時發生數值或格式錯誤: {e_val}。跳過。")
            failed_files_info.append({'file': file_source_name, 'url': url, 'reason': f'數值/格式錯誤: {e_val}'})
        except Exception as e_file:
            current_logger.error(f"文件 {file_source_name}: 處理時發生未預期錯誤: {e_file}。跳過。", exc_info=True)
            failed_files_info.append({'file': file_source_name, 'url': url, 'reason': f'未知處理錯誤: {e_file}'})

    current_logger.info(f"NY Fed 文件處理循環結束。成功處理 {processed_files_count}/{len(ny_fed_positions_urls)} 個文件。")
    if failed_files_info:
        current_logger.warning(f"以下 NY Fed 文件處理失敗或被跳過:")
        for item in failed_files_info:
            current_logger.warning(f"  - 文件: {item['file']}, URL: {item['url']}, 原因: {item['reason']}")

    if not all_positions_data:
        current_logger.warning("未能從任何 NY Fed 文件中成功提取持有量數據。返回空 Series。")
        return pd.Series(dtype='float64', name='Total_Gross_Positions_Millions')
    try:
        current_logger.info("正在合併所有成功處理的 NY Fed 文件數據...")
        combined_positions_series = pd.concat(all_positions_data)
        combined_positions_series = combined_positions_series.sort_index()
        final_nyfed_series_result = combined_positions_series.groupby(level=0).last()
        final_nyfed_series_result.name = 'Total_Gross_Positions_Millions'
        final_nyfed_series_result = final_nyfed_series_result.dropna()
        final_nyfed_series_result = final_nyfed_series_result[final_nyfed_series_result != 0]

        if final_nyfed_series_result.empty:
            current_logger.warning("合併所有 NY Fed 文件數據後，最終序列為空或全為零值。")
            return pd.Series(dtype='float64', name='Total_Gross_Positions_Millions')
        else:
            current_logger.info(f"NY Fed 持有量數據合併完成。最終序列包含 {len(final_nyfed_series_result)} 筆有效數據 "
                                f"(從 {final_nyfed_series_result.index.min().strftime('%Y-%m-%d')} 到 {final_nyfed_series_result.index.max().strftime('%Y-%m-%d')})。")
            return final_nyfed_series_result
    except Exception as e_concat:
        current_logger.error(f"合併 NY Fed 持有量數據時出錯: {e_concat}", exc_info=True)
        return pd.Series(dtype='float64', name='Total_Gross_Positions_Millions')

# 可以在此處添加 __main__ 區塊用於單獨測試此模組的函式
# if __name__ == '__main__':
#     # 設置測試用的 logger
#     from src.utils.logger import setup_logger
#     test_logger = setup_logger("data_fetcher_test", level=logging.DEBUG)
#     # 準備 mock_config 和其他參數
#     # ...
#     # 呼叫並測試每個函式
#     # ...
