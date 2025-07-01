# -*- coding: utf-8 -*-
"""
yfinance 客戶端模組。
封裝所有與 yfinance API 互動的邏輯，包括數據抓取、快取及錯誤處理。
"""
import yfinance as yf
import pandas as pd
import time
# from datetime import datetime, timedelta # 在這個版本中不再直接使用
import os

class YFinanceClient:
    """
    一個用於與 yfinance API 互動的客戶端。

    提供方法來抓取市場數據，並實現自適應區間降級策略。
    """
    def __init__(self, cache_dir="data_workspace/cache/yfinance"):
        """
        初始化 YFinanceClient。

        Args:
            cache_dir (str): 用於儲存快取檔案的目錄路徑。
                             (注意: 目前版本的快取邏輯尚未完全整合到自適應方法中)
        """
        self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)
        # 定義區間降級鏈
        self.FALLBACK_INTERVALS = ['1m', '5m', '15m', '30m', '1h', '1d', '1wk', '1mo']
        print(f"INFO: YFinanceClient 初始化完畢，快取目錄: {self.cache_dir}")
        print(f"INFO: 區間降級鏈: {self.FALLBACK_INTERVALS}")


    def _get_period_for_interval(self, interval: str) -> str:
        """
        根據 yfinance API 的限制，為指定的數據間隔返回合適的 `period` 參數。
        此版本根據草案 v10.2 進行調整。
        """
        if "m" in interval: # 分鐘線 (1m, 5m, 15m, 30m)
            # yfinance 限制 1m-29m interval 最多抓 7 天, 30m-90m interval 最多抓 60 天
            # 為簡化並確保數據量，統一分鐘線的 period 為 7d，若需要更長可調整
            return "7d"
        elif "h" in interval: # 小時線 (1h)
            # yfinance 限制 1h interval 最多抓 730 天， "60d" 是一個較為常見的選擇
            return "60d"
        elif "d" in interval or "wk" in interval: # 日線或週線
            return "1y" # 抓取一年數據
        elif "mo" in interval: # 月線
            return "5y" # 抓取五年數據
        return "max" # 其他未知情況，預設抓取所有可用數據

    def fetch_data_adaptively(self, ticker: str, start_interval: str = '1m') -> tuple[pd.DataFrame | None, str | None]:
        """
        自適應區間降級的數據抓取方法。
        會從指定的 start_interval 開始嘗試，如果失敗則自動降級。
        返回一個包含 (數據 DataFrame, 最終成功區間 string) 的元組。
        如果所有嘗試都失敗，則返回 (None, None)。
        """
        try:
            # 找到起始區間在降級鏈中的位置
            start_index = self.FALLBACK_INTERVALS.index(start_interval)
        except ValueError:
            print(f"錯誤：起始區間 '{start_interval}' 不在預定義的降級鏈 {self.FALLBACK_INTERVALS} 中。")
            return None, None

        # 從起始位置開始遍歷降級鏈
        for interval_to_try in self.FALLBACK_INTERVALS[start_index:]:
            period = self._get_period_for_interval(interval_to_try)
            print(f"INFO: 正在嘗試抓取 {ticker} (Period: {period}, Interval: {interval_to_try})...")

            try:
                stock = yf.Ticker(ticker)
                # 抓取歷史數據，auto_adjust=True 會自動調整 OHLC 價格，移除 'Adjusted Close'
                data = stock.history(period=period, interval=interval_to_try, auto_adjust=True)

                # 核心判斷：如果成功獲取到數據
                if data is not None and not data.empty:
                    print(f"成功：在區間 '{interval_to_try}' 為 {ticker} 獲取到 {len(data)} 筆數據。")

                    # 欄位名稱統一小寫
                    data.columns = [col.lower() for col in data.columns]

                    # 確保 'volume' 欄位存在，若不存在則填 0 (例如指數可能沒有成交量)
                    if 'volume' not in data.columns:
                        data['volume'] = 0
                    data['volume'] = data['volume'].fillna(0).astype('int64')

                    # 確保 DatetimeIndex 是 UTC (yfinance 通常返回 UTC，但最好明確指定)
                    if data.index.tz is None:
                         data.index = data.index.tz_localize('UTC')
                    else:
                         data.index = data.index.tz_convert('UTC')

                    # TODO: 在此可以加入寫入快取的邏輯 (例如使用 cache_file_name 和 self.cache_dir)
                    # cache_file_name = f"{ticker.replace('^', '')}_{interval_to_try}.parquet"
                    # cache_file = os.path.join(self.cache_dir, cache_file_name)
                    # data.to_parquet(cache_file)
                    # print(f"INFO: 數據已快取至 {cache_file}")

                    return data, interval_to_try

                # 如果返回空數據，則繼續下一個循環
                print(f"警告：標的 {ticker} 在區間 '{interval_to_try}' (Period: {period}) 未找到數據，嘗試下一個區間...")

            except Exception as e:
                # 捕捉 yfinance 可能拋出的各種錯誤，包括無數據的特定錯誤
                print(f"警告：為 {ticker} 嘗試區間 '{interval_to_try}' (Period: {period}) 時發生錯誤: {e}，嘗試下一個區間...")

            # 在每次嘗試之間可以加入一個微小的延遲，避免過於頻繁的請求
            time.sleep(0.5)

        # 如果所有區間都嘗試完畢仍然失敗
        print(f"錯誤：對於標的 {ticker} (從 {start_interval} 開始)，所有降級區間 {self.FALLBACK_INTERVALS[start_index:]} 均無法獲取數據。")
        return None, None

    def _is_cache_valid(self, cache_file: str, cache_expiry_hours: int = 1) -> bool:
        """
        (輔助方法，目前未在 fetch_data_adaptively 中直接使用，但可供未來快取整合)
        檢查快取檔案是否存在且仍然有效（未過期）。
        """
        if not os.path.exists(cache_file):
            return False
        file_mod_time = os.path.getmtime(cache_file)
        if (time.time() - file_mod_time) / 3600 < cache_expiry_hours:
            return True
        print(f"INFO: 快取檔案 {cache_file} 已過期。")
        return False

    def fetch_data(self, ticker: str, interval: str, retries: int = 3, delay: int = 5) -> pd.DataFrame | None:
        """
        舊的 fetch_data 方法。
        注意：此方法在新架構下可能被廢棄或重構。
        目前的自適應邏輯在 fetch_data_adaptively 中實現。
        這個舊方法暫時保留，但其快取和重試邏輯與 fetch_data_adaptively 中的不同。
        """
        print(f"警告: 正調用舊的 fetch_data 方法處理 {ticker} ({interval})。建議改用 fetch_data_adaptively。")
        # 簡單地調用 yfinance，不包含複雜的快取或自適應邏輯
        try:
            stock_ticker = yf.Ticker(ticker)
            period = self._get_period_for_interval(interval) # 使用更新後的 period 邏輯
            data = stock_ticker.history(period=period, interval=interval, auto_adjust=True)

            if data.empty:
                print(f"警告 (舊方法): {ticker} (間隔: {interval}) 在 yfinance 返回空數據。")
                return None

            data.columns = [col.lower() for col in data.columns]
            if 'volume' not in data.columns:
                data['volume'] = 0
            data['volume'] = data['volume'].fillna(0).astype('int64')
            if data.index.tz is None:
                data.index = data.index.tz_localize('UTC')
            else:
                data.index = data.index.tz_convert('UTC')
            return data
        except Exception as e:
            print(f"錯誤 (舊方法): 抓取 {ticker} ({interval}) 失敗: {e}")
            return None


if __name__ == '__main__':
    # 簡易測試代碼
    print("--- YFinanceClient 測試 (自適應版本) ---")
    # client = YFinanceClient(cache_expiry_hours=0.01) # cache_expiry_hours 不再是主要參數
    client = YFinanceClient()

    # 測試案例 1: 嘗試 '1m'，應該成功 (例如 AAPL 通常有 1m 數據)
    print("\n--- 測試案例 1: AAPL, start_interval='1m' ---")
    aapl_data, aapl_interval = client.fetch_data_adaptively("AAPL", start_interval='1m')
    if aapl_data is not None:
        print(f"成功獲取 AAPL 數據，最終區間: {aapl_interval}，共 {len(aapl_data)} 筆。")
        print(aapl_data.head())
        # print(aapl_data.info())
    else:
        print(f"未能獲取 AAPL 數據。")

    # 測試案例 2: 嘗試 '^VIX'，'1m' 應該會失敗，然後降級
    # 注意: ^VIX 可能在所有分鐘/小時級別都失敗，最終可能成功在 '1d'
    print("\n--- 測試案例 2: ^VIX, start_interval='1m' (預期降級) ---")
    vix_data, vix_interval = client.fetch_data_adaptively("^VIX", start_interval='1m')
    if vix_data is not None:
        print(f"成功獲取 ^VIX 數據，最終區間: {vix_interval}，共 {len(vix_data)} 筆。")
        print(vix_data.head())
    else:
        print(f"未能獲取 ^VIX 數據。")

    # 測試案例 3: 無效標的，所有區間都應失敗
    print("\n--- 測試案例 3: FAKE_TICKER_XYZ, start_interval='1m' (預期全部失敗) ---")
    fake_data, fake_interval = client.fetch_data_adaptively("FAKE_TICKER_XYZ", start_interval='1m')
    if fake_data is None:
        print("成功處理無效標的 FAKE_TICKER_XYZ，未能獲取數據 (符合預期)。")
    else:
        print(f"錯誤：不應為 FAKE_TICKER_XYZ 獲取到數據，但得到了 {fake_interval} 的數據。")

    # 測試案例 4: 指定一個中間的 start_interval，例如 '1h'
    print("\n--- 測試案例 4: MSFT, start_interval='1h' ---")
    msft_data, msft_interval = client.fetch_data_adaptively("MSFT", start_interval='1h')
    if msft_data is not None:
        print(f"成功獲取 MSFT 數據，最終區間: {msft_interval}，共 {len(msft_data)} 筆。")
        print(msft_data.head())
    else:
        print(f"未能獲取 MSFT 數據。")

    # 測試案例 5: 嘗試一個不存在於 FALLBACK_INTERVALS 的 start_interval
    print("\n--- 測試案例 5: TSLA, start_interval='2m' (無效起始區間) ---")
    tsla_data, tsla_interval = client.fetch_data_adaptively("TSLA", start_interval='2m')
    if tsla_data is None and tsla_interval is None:
        print("成功處理無效起始區間 '2m' (符合預期)。")
    else:
        print(f"錯誤：對於無效起始區間 '2m'，不應有返回數據。")

    # 測試舊的 fetch_data 方法 (可選)
    # print("\n--- 測試舊的 fetch_data 方法: GOOG 5m ---")
    # goog_data_old = client.fetch_data("GOOG", "5m")
    # if goog_data_old is not None:
    #     print(f"舊方法成功獲取 GOOG (5m)數據，共 {len(goog_data_old)} 筆。")
    #     print(goog_data_old.head())

    print("\n--- YFinanceClient 測試完畢 ---")
