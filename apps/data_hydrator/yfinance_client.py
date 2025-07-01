# -*- coding: utf-8 -*-
"""
YFinanceClient for Data Hydrator
================================

負責從 yfinance 下載指定時間範圍內的歷史數據，核心功能包括：
1.  **時間分塊 (Chunking)**：將長的時間範圍切成 yfinance API 允許的小塊。
2.  **迭代降級 (Fallback)**：從最精細的數據顆粒度開始嘗試，如果失敗則自動嘗試更粗的顆粒度。
3.  **數據標準化**：統一欄位名、處理缺失的 volume、確保時區為 UTC。

設計思路：
- `hydrate_data_range` 是主要的外部接口，它協調整個數據回填過程。
- `_get_chunk_size_for_interval` 和 `_split_date_range_into_chunks` 是時間分塊的輔助方法。
- `fetch_single_chunk` 負責抓取單個時間區塊的特定顆粒度數據。
- 降級邏輯在 `hydrate_data_range` 中實現，遍歷 `FALLBACK_INTERVALS`。
"""
import yfinance as yf
import pandas as pd
import time
import os
from datetime import datetime, timedelta

class YFinanceClient:
    """
    一個用於從 yfinance API 抓取長時間範圍歷史數據的客戶端，
    內建時間分塊和迭代降級策略。
    """
    def __init__(self, cache_dir="data_workspace/cache/yfinance_hydrator"):
        """
        初始化 YFinanceClient。

        Args:
            cache_dir (str): 用於儲存快取檔案的目錄路徑 (目前版本暫未實現快取)。
        """
        self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)
        # 定義區間降級鏈，從最細到最粗
        self.FALLBACK_INTERVALS = ['1m', '5m', '15m', '30m', '1h', '1d', '1wk', '1mo']
        # yfinance 的 interval 參數說明:
        # 分鐘線: 1m, 2m, 5m, 15m, 30m, 60m, 90m
        #   - 1m: Max 7 days back
        #   - 2m, 5m, 15m, 30m: Max 60 days back
        #   - 60m, 90m: Max 730 days back (yfinance treats as '1h')
        # 小時線: 1h (same as 60m)
        # 日線及以上: 1d, 5d, 1wk, 1mo, 3mo
        #   - 日線以上通常可以拉取較長歷史

        print(f"INFO: YFinanceClient (Data Hydrator) 初始化完畢，快取目錄: {self.cache_dir}")
        print(f"INFO: 區間降級鏈: {self.FALLBACK_INTERVALS}")

    def _get_chunk_size_for_interval(self, interval: str) -> int:
        """
        根據 yfinance API 的限制，為指定的數據間隔返回建議的單次請求最大天數。
        這些值是基於 yfinance 的常見限制，並稍微保守一些以避免邊界問題。
        """
        if interval == '1m':
            return 6 # yfinance 通常限制 1m 為 7 天
        elif interval in ['2m', '5m', '15m', '30m']:
            return 55 # yfinance 通常限制這些為 60 天
        elif interval in ['60m', '90m', '1h']: # 60m, 90m 在 yfinance 中通常按 1h 處理
            return 700 # yfinance 通常限制 1h 為 730 天
        elif interval in ['1d', '5d', '1wk']:
            return 365 * 2 # 日線或週線可以拉取較長數據，例如2年
        elif interval in ['1mo', '3mo']:
            return 365 * 5 # 月線可以拉取更長數據，例如5年
        else:
            print(f"警告: 未知的 interval '{interval}'，預設 chunk_size_days 為 30。")
            return 30

    def _split_date_range_into_chunks(self, start_date_str: str, end_date_str: str, chunk_size_days: int) -> list[tuple[str, str]]:
        """
        將給定的日期範圍（YYYY-MM-DD 格式字串）根據 chunk_size_days 切分成多個日期區塊。
        每個區塊以 (chunk_start_date_str, chunk_end_date_str) 的形式返回。
        注意：yfinance 的 end date 是不包含的，所以 chunk_end_date 會是實際結束日期的後一天。
        """
        chunks = []
        try:
            current_start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
            final_end_date = datetime.strptime(end_date_str, "%Y-%m-%d")
        except ValueError as e:
            print(f"錯誤: 日期格式錯誤 ({start_date_str}, {end_date_str})。請使用 YYYY-MM-DD 格式。 {e}")
            return []

        while current_start_date <= final_end_date:
            # 計算這個 chunk 的結束日期 (yfinance 的 end 是 exclusive)
            # 所以 chunk_end_date 是我們希望包含的最後一天的 *後一天*
            chunk_actual_end_date = current_start_date + timedelta(days=chunk_size_days - 1)

            # 如果計算出的 chunk 結束日期超過了總的結束日期，則使用總的結束日期
            if chunk_actual_end_date > final_end_date:
                chunk_actual_end_date = final_end_date

            # yfinance 的 end 參數是不包含的，所以要加一天
            yfinance_end_date = chunk_actual_end_date + timedelta(days=1)

            chunks.append((
                current_start_date.strftime("%Y-%m-%d"),
                yfinance_end_date.strftime("%Y-%m-%d")
            ))
            # 下一個 chunk 的開始日期是當前 chunk 實際結束日期的後一天
            current_start_date = chunk_actual_end_date + timedelta(days=1)

        return chunks

    def fetch_single_chunk(self, ticker: str, chunk_start_date_str: str, chunk_end_date_str: str, interval: str) -> pd.DataFrame | None:
        """
        抓取單個時間區塊 (chunk_start_date_str 到 chunk_end_date_str Exclusive) 的特定顆粒度數據。

        Args:
            ticker (str): 股票代碼。
            chunk_start_date_str (str): 區塊開始日期 (YYYY-MM-DD)。
            chunk_end_date_str (str): 區塊結束日期 (YYYY-MM-DD, yfinance history() 的 end 參數, 不包含此日期)。
            interval (str): 數據顆粒度。

        Returns:
            pd.DataFrame | None: 包含市場數據的 DataFrame，若失敗則返回 None。
        """
        print(f"INFO: fetch_single_chunk: Ticker={ticker}, Interval={interval}, Start={chunk_start_date_str}, End(Exclusive)={chunk_end_date_str}")
        try:
            stock = yf.Ticker(ticker)
            # auto_adjust=True: 自動調整OHLC，移除 'Adjusted Close' 和 'Dividends', 'Stock Splits'
            # prepost=False: 通常對於歷史回填，我們不需要盤前盤後數據，除非特定需求
            data = stock.history(start=chunk_start_date_str,
                                 end=chunk_end_date_str,
                                 interval=interval,
                                 auto_adjust=True,
                                 prepost=False)

            if data is None or data.empty:
                print(f"警告: fetch_single_chunk: {ticker} 在 {chunk_start_date_str} 到 {chunk_end_date_str} (間隔: {interval}) 無數據返回。")
                return None

            # 數據標準化
            data.columns = [col.lower() for col in data.columns]
            if 'volume' not in data.columns:
                data['volume'] = 0
            data['volume'] = data['volume'].fillna(0).astype('int64')

            if data.index.tz is None:
                data.index = data.index.tz_localize('UTC')
            else:
                data.index = data.index.tz_convert('UTC')

            # 為合併後的數據添加 interval 資訊
            data['interval'] = interval
            data['ticker'] = ticker # 也加入 ticker 資訊，方便合併多個 ticker 的數據

            print(f"INFO: fetch_single_chunk: 成功獲取 {len(data)} 筆數據。")
            return data

        except Exception as e:
            print(f"錯誤: fetch_single_chunk: 抓取 {ticker} ({interval}, {chunk_start_date_str}-{chunk_end_date_str}) 失敗: {e}")
            return None

    def hydrate_data_range(self, ticker: str, start_date_str: str, end_date_str: str) -> pd.DataFrame | None:
        """
        核心方法：全自動回填指定股票在給定時間範圍內的歷史數據。
        它會從最精細的顆粒度開始嘗試，使用時間分塊和迭代降級策略。

        Args:
            ticker (str): 股票代碼。
            start_date_str (str): 開始日期 (YYYY-MM-DD)。
            end_date_str (str): 結束日期 (YYYY-MM-DD)。

        Returns:
            pd.DataFrame | None: 一個包含所有成功抓取數據的、合併後的 DataFrame，
                                 並帶有 'interval' 和 'ticker' 欄位。若完全失敗則返回 None。
        """
        print(f"===== 開始數據回填任務: Ticker={ticker}, Range=[{start_date_str} to {end_date_str}] =====")

        # 嘗試從最精細的顆粒度開始
        for interval in self.FALLBACK_INTERVALS:
            print(f"\nINFO: hydrate_data_range: 正在嘗試使用顆粒度 '{interval}' 回填 {ticker} 從 {start_date_str} 到 {end_date_str}...")

            chunk_size_days = self._get_chunk_size_for_interval(interval)
            if chunk_size_days <= 0: # 防呆
                print(f"警告: hydrate_data_range: 顆粒度 '{interval}' 的 chunk_size_days ({chunk_size_days}) 無效，跳過此顆粒度。")
                continue

            date_chunks = self._split_date_range_into_chunks(start_date_str, end_date_str, chunk_size_days)
            if not date_chunks:
                print(f"警告: hydrate_data_range: 無法為顆粒度 '{interval}' 生成有效的日期區塊，跳過此顆粒度。")
                continue

            print(f"INFO: hydrate_data_range: 顆粒度 '{interval}'，共切分為 {len(date_chunks)} 個時間區塊。")

            current_interval_all_data = []
            all_chunks_successful_for_this_interval = True

            for i, (chunk_start, chunk_end) in enumerate(date_chunks):
                print(f"INFO: hydrate_data_range: 正在處理區塊 {i+1}/{len(date_chunks)} ({chunk_start} to {chunk_end} exclusive) for interval '{interval}'...")
                # 注意：fetch_single_chunk 內部會處理 yfinance 的 end date 排他性
                chunk_df = self.fetch_single_chunk(ticker, chunk_start, chunk_end, interval)

                if chunk_df is not None and not chunk_df.empty:
                    current_interval_all_data.append(chunk_df)
                else:
                    print(f"警告: hydrate_data_range: 顆粒度 '{interval}'，區塊 {chunk_start}-{chunk_end} 數據抓取失敗或為空。此顆粒度嘗試終止。")
                    all_chunks_successful_for_this_interval = False
                    break # 跳出此 interval 的 chunks 循環，嘗試下一個更粗的 interval

            if all_chunks_successful_for_this_interval and current_interval_all_data:
                final_df = pd.concat(current_interval_all_data, ignore_index=False) # 保留 DatetimeIndex
                # 再次確保 ticker 和 interval 欄位存在 (儘管 fetch_single_chunk 已加入)
                final_df['ticker'] = ticker
                final_df['interval'] = interval
                print(f"成功: hydrate_data_range: 已使用顆粒度 '{interval}' 完成 {ticker} 在 {start_date_str} 到 {end_date_str} 的所有數據回填。共 {len(final_df)} 筆。")
                print(f"===== 數據回填任務結束 (成功): Ticker={ticker} =====")
                return final_df
            elif not current_interval_all_data and all_chunks_successful_for_this_interval:
                 print(f"INFO: hydrate_data_range: 顆粒度 '{interval}' 所有區塊均未返回數據，但未發生錯誤。嘗試下一個顆粒度。")
            else:
                print(f"INFO: hydrate_data_range: 顆粒度 '{interval}' 未能成功回填所有區塊。嘗試下一個更粗的顆粒度。")

            time.sleep(1) # 在嘗試不同 interval 之間稍作停頓

        print(f"錯誤: hydrate_data_range: 所有降級顆粒度 {self.FALLBACK_INTERVALS} 均無法為 {ticker} 在 {start_date_str} 到 {end_date_str} 範圍內回填任何數據。")
        print(f"===== 數據回填任務結束 (失敗): Ticker={ticker} =====")
        return None

if __name__ == '__main__':
    print("--- YFinanceClient (Data Hydrator) 測試 ---")
    client = YFinanceClient()

    # 測試日期範圍和股票代碼
    test_ticker = "AAPL" # 或者用一個你知道數據較少的股票測試降級
    # test_ticker = "^VIX" # VIX 通常沒有分鐘線數據
    # test_ticker = "FAKEBADTICKER"

    # 測試案例 1: 短時間範圍，1m 數據應該存在 (例如最近幾天)
    # end_date_dt = datetime.now()
    # start_date_dt = end_date_dt - timedelta(days=3)
    # test_start_date = start_date_dt.strftime("%Y-%m-%d")
    # test_end_date = end_date_dt.strftime("%Y-%m-%d")

    # 為了可重複測試，使用固定日期
    test_start_date = "2024-07-01"
    test_end_date = "2024-07-03" # 抓取 7/1, 7/2, 7/3 三天的數據

    print(f"\n--- 測試案例 1: {test_ticker}, Range: [{test_start_date} to {test_end_date}] ---")
    hydrated_data = client.hydrate_data_range(test_ticker, test_start_date, test_end_date)

    if hydrated_data is not None and not hydrated_data.empty:
        print(f"\n--- {test_ticker} 數據回填結果 ---")
        print(f"成功獲取 {len(hydrated_data)} 筆數據。")
        print(f"使用的顆粒度: {hydrated_data['interval'].unique()}")
        print("數據預覽 (前5筆):")
        print(hydrated_data.head())
        print("數據預覽 (後5筆):")
        print(hydrated_data.tail())
        print("數據資訊:")
        hydrated_data.info()
    else:
        print(f"未能為 {test_ticker} 在指定範圍內回填數據。")

    # 測試案例 2: 更長的時間範圍，可能會觸發多次分塊
    # test_start_date_long = "2024-06-01"
    # test_end_date_long = "2024-07-10"
    # print(f"\n--- 測試案例 2: {test_ticker}, Long Range: [{test_start_date_long} to {test_end_date_long}] ---")
    # hydrated_data_long = client.hydrate_data_range(test_ticker, test_start_date_long, test_end_date_long)
    # if hydrated_data_long is not None and not hydrated_data_long.empty:
    # print(f"長範圍測試成功獲取 {len(hydrated_data_long)} 筆數據，顆粒度: {hydrated_data_long['interval'].unique()}")
    # else:
    # print(f"長範圍測試未能為 {test_ticker} 回填數據。")

    # 測試案例 3: 無效股票代碼
    # print(f"\n--- 測試案例 3: FAKEBADTICKER ---")
    # hydrated_data_fake = client.hydrate_data_range("FAKEBADTICKER", "2024-01-01", "2024-01-05")
    # if hydrated_data_fake is None:
    # print("無效股票代碼測試成功，未返回數據 (符合預期)。")
    # else:
    # print(f"錯誤：無效股票代碼不應返回數據，卻得到 {len(hydrated_data_fake)} 筆。")

    # 測試案例 4: ^VIX (通常1m, 5m等會失敗，最終可能用1d)
    # test_start_vix = "2024-07-01"
    # test_end_vix = "2024-07-08" # 一週數據
    # print(f"\n--- 測試案例 4: ^VIX, Range: [{test_start_vix} to {test_end_vix}] ---")
    # vix_data = client.hydrate_data_range("^VIX", test_start_vix, test_end_vix)
    # if vix_data is not None and not vix_data.empty:
    # print(f"VIX 測試成功獲取 {len(vix_data)} 筆數據，顆粒度: {vix_data['interval'].unique()}")
    # print(vix_data.head())
    # else:
    # print(f"VIX 測試未能回填數據。")

    print("\n--- YFinanceClient (Data Hydrator) 測試完畢 ---")
