# -*- coding: utf-8 -*-
"""
yfinance 客戶端模組。
封裝所有與 yfinance API 互動的邏輯，包括數據抓取、快取及錯誤處理。
"""
import yfinance as yf
import pandas as pd
import time
from datetime import datetime, timedelta
import os

class YFinanceClient:
    """
    一個用於與 yfinance API 互動的客戶端。

    提供方法來抓取市場數據，並內建快取機制以減少重複的 API 請求
    及基本的錯誤處理與重試邏輯。
    """
    def __init__(self, cache_dir="data_workspace/cache/yfinance", cache_expiry_hours=1):
        """
        初始化 YFinanceClient。

        Args:
            cache_dir (str): 用於儲存快取檔案的目錄路徑。
            cache_expiry_hours (int): 快取檔案的有效時長（小時）。
                                      若快取檔案超過此時長，將重新從 API 抓取。
        """
        self.cache_dir = cache_dir
        self.cache_expiry_hours = cache_expiry_hours
        os.makedirs(self.cache_dir, exist_ok=True)
        print(f"INFO: YFinanceClient 初始化完畢，快取目錄: {self.cache_dir}, 快取有效時長: {cache_expiry_hours} 小時")

    def _get_period_for_interval(self, interval: str) -> str:
        """
        根據 yfinance API 的限制，為指定的數據間隔返回合適的 `period` 參數。

        yfinance 對於不同顆粒度的數據有不同的最大查詢範圍限制：
        - 分鐘線 (e.g., 1m, 5m): 通常最多只能抓取最近 7 天的數據。
                             若需要更長時間範圍，yfinance 限制 period 最大為 "60d" 但 interval 不能低於 "1m"。
                             更精確地說，1-29 分鐘的 interval，period 最大為 "7d"。30分鐘以上到90分鐘的 interval，period 最大為 "60d"。
        - 小時線 (e.g., 1h): 通常最多能抓取最近 730 天 (約 2 年) 的數據，但 "60d" 通常是個安全的選擇。
        - 日線 (e.g., 1d): 可以抓取更長期的歷史數據。

        Args:
            interval (str): 數據的時間間隔 (例如 "1m", "5m", "1h", "1d")。

        Returns:
            str: 對應於 yfinance API 的 `period` 參數。
        """
        if "m" in interval: # 分鐘線
            minutes = int(interval[:-1])
            if minutes < 30:
                return "7d" # yfinance 限制 1m-29m interval 最多抓 7 天
            else: # 30m, 60m (視為 1h), 90m
                return "60d" # yfinance 限制 30m-90m interval 最多抓 60 天
        elif "h" in interval: # 小時線
            return "730d" # yfinance 限制 1h interval 最多抓 730 天
        return "max" # 日線或其他，抓取所有可用數據

    def _is_cache_valid(self, cache_file: str) -> bool:
        """
        檢查快取檔案是否存在且仍然有效（未過期）。

        Args:
            cache_file (str): 快取檔案的路徑。

        Returns:
            bool: 如果快取有效則返回 True，否則 False。
        """
        if not os.path.exists(cache_file):
            return False

        # 檢查檔案修改時間是否在有效期內
        file_mod_time = os.path.getmtime(cache_file)
        if (time.time() - file_mod_time) / 3600 < self.cache_expiry_hours:
            return True

        print(f"INFO: 快取檔案 {cache_file} 已過期。")
        return False

    def fetch_data(self, ticker: str, interval: str, retries: int = 3, delay: int = 5) -> pd.DataFrame | None:
        """
        從 yfinance API 抓取指定股票代碼和時間間隔的市場數據。

        此方法包含以下特性：
        1.  **快取機制**：首先檢查本地快取，若有有效快取則直接讀取，避免重複 API 請求。
        2.  **API 限制處理**：根據 `interval` 自動調整 `period` 以符合 yfinance 的限制。
        3.  **錯誤重試**：若 API 請求失敗，會進行指數退避重試。
        4.  **數據標準化**：欄位名稱統一為小寫。

        Args:
            ticker (str): 股票代碼 (例如 "AAPL", "^TWII")。
            interval (str): 數據的時間間隔 (例如 "1m", "5m", "1h", "1d")。
            retries (int): API 請求失敗時的最大重試次數。
            delay (int): 重試之間的初始延遲秒數（會進行指數退避）。

        Returns:
            pd.DataFrame | None: 包含市場數據的 DataFrame (OHLCV)，
                                 若抓取失敗或無數據則返回 None。
                                 DataFrame 的 Index 為 DatetimeIndex (UTC)。
        """
        cache_file_name = f"{ticker.replace('^', '')}_{interval}.parquet" # 移除 ^ 以避免路徑問題
        cache_file = os.path.join(self.cache_dir, cache_file_name)

        # 1. 檢查快取
        if self._is_cache_valid(cache_file):
            try:
                print(f"INFO: 從快取讀取 {ticker} (間隔: {interval})...")
                data = pd.read_parquet(cache_file)
                # 確保快取數據的 index 是 DatetimeIndex
                if not isinstance(data.index, pd.DatetimeIndex):
                    data.index = pd.to_datetime(data.index)
                # yfinance 返回的數據通常是 UTC
                if data.index.tz is None:
                     data.index = data.index.tz_localize('UTC')
                else:
                     data.index = data.index.tz_convert('UTC')
                print(f"INFO: 成功從快取載入 {len(data)} 筆數據。")
                return data
            except Exception as e:
                print(f"警告: 讀取快取檔案 {cache_file} 失敗: {e}。將嘗試從 API 重新抓取。")

        # 2. API 請求與重試
        period = self._get_period_for_interval(interval)

        for attempt in range(retries):
            try:
                print(f"INFO: 從 API (yfinance) 抓取 {ticker} (Period: {period}, Interval: {interval}, 嘗試 {attempt + 1}/{retries})...")

                stock_ticker = yf.Ticker(ticker)
                # 抓取歷史數據
                data = stock_ticker.history(period=period, interval=interval, auto_adjust=True)

                if data.empty:
                    print(f"警告: 標的 {ticker} (間隔: {interval}) 在 yfinance 返回空數據。原因可能是：(1) 此標的 (如 ^VIX) 不提供此間隔的數據；(2) 該時段無交易；(3) 股票代碼錯誤或已下市。")
                    # 存一個空的 DataFrame 到快取，避免短時間內重複查詢無效標的
                    pd.DataFrame().to_parquet(cache_file)
                    return None

                # 欄位名稱統一小寫
                data.columns = [col.lower() for col in data.columns]

                # 確保 'volume' 欄位存在，若不存在則填 0 (例如指數可能沒有成交量)
                if 'volume' not in data.columns:
                    data['volume'] = 0
                data['volume'] = data['volume'].fillna(0).astype('int64')


                # 確保 DatetimeIndex 是 UTC
                if data.index.tz is None:
                     data.index = data.index.tz_localize('UTC')
                else:
                     data.index = data.index.tz_convert('UTC')

                # 3. 成功後寫入快取
                print(f"INFO: 成功從 API 抓取 {len(data)} 筆 {ticker} 數據，儲存至快取 {cache_file}...")
                data.to_parquet(cache_file)
                return data

            except yf.shared.YFinanceException as yfe:
                print(f"錯誤: yfinance 特定錯誤抓取 {ticker} 失敗 (嘗試 {attempt + 1}/{retries}): {yfe}")
                # 特定錯誤可能不需要重試，例如 404 Not Found
                if "No data found for ticker" in str(yfe) or "No price data found" in str(yfe):
                    print(f"INFO: 標的 {ticker} 可能不存在或無數據，不再重試。")
                    pd.DataFrame().to_parquet(cache_file) # 快取空結果
                    return None
                if attempt < retries - 1:
                    current_delay = delay * (2 ** attempt)
                    print(f"INFO: 等待 {current_delay} 秒後重試...")
                    time.sleep(current_delay)
                else:
                    print(f"錯誤: 所有 {retries} 次重試均告失敗 (yfinance specific)。")
                    return None
            except Exception as e:
                print(f"錯誤: 一般錯誤抓取 {ticker} 失敗 (嘗試 {attempt + 1}/{retries}): {e}")
                if attempt < retries - 1:
                    current_delay = delay * (2 ** attempt)
                    print(f"INFO: 等待 {current_delay} 秒後重試...")
                    time.sleep(current_delay)
                else:
                    print(f"錯誤: 所有 {retries} 次重試均告失敗。")
                    return None
        return None

if __name__ == '__main__':
    # 簡易測試代碼
    print("--- YFinanceClient 測試 ---")
    client = YFinanceClient(cache_expiry_hours=0.01) # 設定短快取以利測試

    # 測試案例 1: 有效標的，分鐘線
    print("\n--- 測試案例 1: AAPL 1m ---")
    aapl_data = client.fetch_data("AAPL", "1m")
    if aapl_data is not None:
        print(f"成功獲取 AAPL (1m)數據，共 {len(aapl_data)} 筆。")
        print(aapl_data.head())
        print(aapl_data.info())

    # 測試案例 2: 有效標的，日線
    print("\n--- 測試案例 2: ^TWII 1d ---")
    twii_data = client.fetch_data("^TWII", "1d")
    if twii_data is not None:
        print(f"成功獲取 ^TWII (1d)數據，共 {len(twii_data)} 筆。")
        print(twii_data.head())

    # 測試案例 3: 無效標的
    print("\n--- 測試案例 3: FAKE_TICKER 1d ---")
    fake_data = client.fetch_data("FAKE_TICKER_XYZ", "1d")
    if fake_data is None:
        print("成功處理無效標的 FAKE_TICKER_XYZ。")

    # 測試案例 4: 讀取快取
    print("\n--- 測試案例 4: AAPL 1m (應從快取讀取) ---")
    aapl_data_cache = client.fetch_data("AAPL", "1m")
    if aapl_data_cache is not None:
        print(f"再次獲取 AAPL (1m)數據，共 {len(aapl_data_cache)} 筆。")

    # 測試案例 5: 特殊標的 (例如加密貨幣，可能需要不同處理)
    print("\n--- 測試案例 5: BTC-USD 1h ---")
    btc_data = client.fetch_data("BTC-USD", "1h")
    if btc_data is not None:
        print(f"成功獲取 BTC-USD (1h)數據，共 {len(btc_data)} 筆。")
        print(btc_data.head())
        print(btc_data.info())

    print("\n--- YFinanceClient 測試完畢 ---")
