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
            # --- 開始標準化 ---
            # 步驟 1: 將索引（通常是日期時間）轉換為列
            if isinstance(data.index, pd.DatetimeIndex):
                data = data.reset_index()

            # 步驟 2: 將所有列名轉為小寫
            data.columns = [col.lower() for col in data.columns]

            # 步驟 3: 統一日期時間列名為 'datetime'
            # yfinance 對於日線或更粗顆粒度數據，列名可能是 'date'
            if 'date' in data.columns and 'datetime' not in data.columns:
                data.rename(columns={'date': 'datetime'}, inplace=True)
            # 如果 'Datetime' (大寫D) 存在且 'datetime' 不存在 (雖然前面已轉小寫，但多一層保護)
            elif 'Datetime' in data.columns and 'datetime' not in data.columns: # 雖然已 lower()，但以防萬一
                 data.rename(columns={'Datetime': 'datetime'}, inplace=True)


            # 步驟 4: 檢查 'datetime' 列是否存在，如果不存在，則是一個問題
            if 'datetime' not in data.columns:
                print(f"錯誤: fetch_single_chunk: 標準化後 DataFrame 中缺少 'datetime' 欄位。股票: {ticker}, 間隔: {interval}。可用欄位: {data.columns.tolist()}")
                # 考慮是否返回 None 或拋出異常，目前返回 None 讓上層處理
                return None

            # 步驟 5: 確保 'datetime' 列是 pandas Timestamp 物件且為 UTC 時區
            # yfinance 通常返回的日期時間已處理好時區 (對於分鐘線通常是交易所時區，日線是 UTC date)
            # 但此處我們要確保它是 UTC Timestamp
            try:
                data['datetime'] = pd.to_datetime(data['datetime'])
                if data['datetime'].dt.tz is None:
                    # 如果是 naive datetime (例如 yfinance 返回的 date 物件被轉為不帶時區的 timestamp)
                    # 假設它是 UTC 日期，或者根據需要設定為特定交易所時區再轉UTC
                    # 為了簡化，這裡直接本地化為 UTC。對於日線數據，時間部分通常是 00:00:00。
                    data['datetime'] = data['datetime'].dt.tz_localize('UTC')
                else:
                    data['datetime'] = data['datetime'].dt.tz_convert('UTC')
            except Exception as e:
                print(f"錯誤: fetch_single_chunk: 轉換 'datetime' 欄位時出錯: {e}. 股票: {ticker}, 間隔: {interval}.")
                return None

            # 步驟 6: 處理 'volume' 欄位
            if 'volume' not in data.columns:
                data['volume'] = 0 # 如果沒有 volume 欄位，則填充為 0
            data['volume'] = data['volume'].fillna(0).astype('int64')

            # 步驟 7: 添加 'interval' 和 'ticker' 資訊欄位
            data['interval'] = interval
            data['ticker'] = ticker

            # 步驟 8: 選擇並排序最終需要的欄位 (與 DBManager 期望的順序和名稱一致)
            # DBManager 期望的欄位: ['datetime', 'ticker', 'interval', 'open', 'high', 'low', 'close', 'volume']
            final_columns = ['datetime', 'ticker', 'interval', 'open', 'high', 'low', 'close', 'volume']
            missing_ohlc_cols = [col for col in ['open', 'high', 'low', 'close'] if col not in data.columns]
            if missing_ohlc_cols:
                print(f"警告: fetch_single_chunk: DataFrame 缺少部分OHLC欄位: {missing_ohlc_cols}。股票: {ticker}, 間隔: {interval}。將嘗試填充為0。")
                for col in missing_ohlc_cols:
                    data[col] = 0.0 # 或 np.nan，但 DBManager 可能期望 float

            try:
                data = data[final_columns]
            except KeyError as e:
                print(f"錯誤: fetch_single_chunk: 選取最終欄位時發生 KeyError: {e}。股票: {ticker}, 間隔: {interval}。可用欄位: {data.columns.tolist()}")
                return None

            print(f"INFO: fetch_single_chunk: 成功獲取並標準化 {len(data)} 筆數據。")
            return data

        except Exception as e:
            # 捕獲更廣泛的異常，包括可能的 yfinance 內部錯誤或網絡問題
            print(f"錯誤: fetch_single_chunk: 抓取或處理 {ticker} ({interval}, {chunk_start_date_str}-{chunk_end_date_str}) 失敗: {type(e).__name__} - {e}")
            return None

    def hydrate_data_range(self, ticker: str, start_date_str: str, end_date_str: str) -> tuple[pd.DataFrame | None, dict]:
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

        execution_log = {} # 初始化執行日誌
        # 生成請求日期範圍內的所有日期字串，用於日誌記錄
        request_date_range_str = [d.strftime("%Y-%m-%d") for d in pd.date_range(start_date_str, end_date_str)]

        # 預先為日誌中的每個日期和 ticker 設置初始狀態 (例如 pending 或 unknown)
        for date_str_in_range in request_date_range_str:
            execution_log.setdefault(date_str_in_range, {})[ticker] = {
                "status": "pending", "interval": None, "count": 0, "message": "Awaiting processing"
            }

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

            current_interval_all_data_dfs = []
            all_chunks_successful_for_this_interval = True

            # 30天限制的日期 (僅與日期部分比較)
            thirty_days_ago_date = (datetime.now() - timedelta(days=30)).date()

            for i, (chunk_start_str, chunk_end_str) in enumerate(date_chunks):
                print(f"INFO: hydrate_data_range: 正在處理區塊 {i+1}/{len(date_chunks)} ({chunk_start_str} to {chunk_end_str} exclusive) for interval '{interval}'...")

                # 【關鍵新增】智能跳過無效請求 - 檢查1m數據是否超過30天窗口
                # chunk_start_date_obj 是 datetime.date 物件
                chunk_start_date_obj = datetime.strptime(chunk_start_str, "%Y-%m-%d").date()
                if interval == '1m' and chunk_start_date_obj < thirty_days_ago_date:
                    print(f"INFO: hydrate_data_range: 區塊起始日期 {chunk_start_str} 的 '1m' 數據請求已超過30天回溯限制，跳過此區塊的 '1m' 嘗試。")
                    # 此處標記此 interval 失敗，因為即使一個 chunk 超限，整個 1m 策略也應被視為對該 chunk 無效
                    # 如果要更細緻，可以只標記這個 chunk 的 1m 失敗，然後繼續用 1m 處理其他 chunk，
                    # 但這會讓日誌和數據合併複雜化。目前策略是：如果一個 chunk 的 1m 超限，則整個 interval 的 1m 嘗試失敗。
                    all_chunks_successful_for_this_interval = False # 標記此 interval 失敗
                    # 更新 execution_log 中此 chunk 覆蓋日期的狀態
                    for day_offset in range((datetime.strptime(chunk_end_str, "%Y-%m-%d") - timedelta(days=1) - datetime.strptime(chunk_start_str, "%Y-%m-%d")).days + 1):
                        log_date_str = (datetime.strptime(chunk_start_str, "%Y-%m-%d") + timedelta(days=day_offset)).strftime("%Y-%m-%d")
                        if log_date_str in execution_log:
                             execution_log[log_date_str][ticker] = {
                                "status": "skipped_1m_due_to_30day_limit", "interval": "1m", "count": 0,
                                "message": f"1m data for {log_date_str} skipped, outside 30-day window."
                            }
                    break # 跳出當前 interval 的 chunks 循環, 嘗試下一個更粗的 interval

                chunk_df = self.fetch_single_chunk(ticker, chunk_start_str, chunk_end_str, interval)

                # 更新執行日誌 (無論成功或失敗)
                # 假設 chunk_df 包含的日期都在 chunk_start_str 和 (chunk_end_str - 1 day) 之間
                # 遍歷此 chunk 覆蓋的每一天來更新日誌
                current_chunk_date_obj = datetime.strptime(chunk_start_str, "%Y-%m-%d")
                actual_chunk_end_date_obj = datetime.strptime(chunk_end_str, "%Y-%m-%d") - timedelta(days=1)

                temp_current_date_for_log = current_chunk_date_obj
                while temp_current_date_for_log <= actual_chunk_end_date_obj:
                    log_date_str = temp_current_date_for_log.strftime("%Y-%m-%d")
                    if log_date_str in execution_log: # 只更新請求範圍內的日期
                        if chunk_df is not None and not chunk_df.empty:
                            # 確保 'datetime' 列是 datetime64[ns, UTC] 類型
                            if 'datetime' not in chunk_df.columns or not pd.api.types.is_datetime64_any_dtype(chunk_df['datetime']):
                                try:
                                    # 嘗試轉換，如果 chunk_df['datetime'] 不存在或無法轉換，會拋異常
                                    if 'datetime' in chunk_df.columns:
                                        chunk_df['datetime'] = pd.to_datetime(chunk_df['datetime'], utc=True)
                                    else: # 如果連 datetime 都沒有（fetch_single_chunk 出問題）
                                        raise ValueError("chunk_df is missing 'datetime' column for log update")
                                except Exception as e_conv:
                                    print(f"錯誤(hydrate_data_range): chunk_df['datetime'] 處理失敗 for {ticker} on {log_date_str}: {e_conv}")
                                    current_log_status = execution_log[log_date_str][ticker].get("status", "unknown_chunk_outcome")
                                    current_log_message = execution_log[log_date_str][ticker].get("message", "")
                                    execution_log[log_date_str][ticker].update({
                                        "status": "failed_datetime_processing_in_log" if current_log_status not in ['success', 'skipped_1m_due_to_30day_limit'] else current_log_status,
                                        "count": 0, # 無法按天計數
                                        "message": current_log_message + f" Datetime processing error in chunk. "
                                    })
                                    temp_current_date_for_log += timedelta(days=1)
                                    continue # 跳過此日期的計數更新

                            # 按日期篩選 DataFrame 中的行
                            # 確保比較的日期也是 date 物件
                            daily_rows_in_chunk = chunk_df[chunk_df['datetime'].dt.date == temp_current_date_for_log.date()]

                            current_log_message = execution_log[log_date_str][ticker].get("message","")
                            if not isinstance(current_log_message, str): current_log_message = str(current_log_message)

                            new_status = "success" # 預設為成功
                            if len(date_chunks) > 1 and not daily_rows_in_chunk.empty : new_status = "success_partial"
                            elif daily_rows_in_chunk.empty : new_status = execution_log[log_date_str][ticker].get("status", "no_data_in_chunk_for_day")


                            execution_log[log_date_str][ticker].update({
                                "status": new_status,
                                "interval": interval,
                                "count": len(daily_rows_in_chunk),
                                "message": current_log_message + \
                                           (f" Fetched {len(daily_rows_in_chunk)} rows for {log_date_str} with {interval} in chunk." if not daily_rows_in_chunk.empty else f" No rows for {log_date_str} in this chunk with {interval}.")
                            })
                        else: # chunk_df is None or empty (抓取此 chunk 失敗)
                             if execution_log[log_date_str][ticker]['status'] not in ['success', 'skipped_1m_due_to_30day_limit']:
                                current_log_message = execution_log[log_date_str][ticker].get("message","")
                                if not isinstance(current_log_message, str): current_log_message = str(current_log_message)
                                execution_log[log_date_str][ticker].update({
                                    "status": "failed_chunk", "interval": interval, "count": 0,
                                    "message": current_log_message + f" Failed to fetch/process chunk covering {log_date_str} with {interval}."
                                })
                    temp_current_date_for_log += timedelta(days=1)

                if chunk_df is not None and not chunk_df.empty:
                    current_interval_all_data_dfs.append(chunk_df)
                else:
                    print(f"警告: hydrate_data_range: 顆粒度 '{interval}'，區塊 {chunk_start_str}-{chunk_end_str} 數據抓取失敗或為空。此顆粒度嘗試終止。")
                    all_chunks_successful_for_this_interval = False
                    break # 跳出此 interval 的 chunks 循環, 嘗試下一個更粗的 interval

            if all_chunks_successful_for_this_interval and current_interval_all_data_dfs:
                # 將所有 chunk 的 DataFrame 合併，注意此時索引可能不唯一，或者不是 datetime
                # fetch_single_chunk 返回的 DataFrame 已經將 datetime 作為列
                final_df = pd.concat(current_interval_all_data_dfs, ignore_index=True)
                # 此處不需要再賦值 final_df['ticker'] 和 final_df['interval']，因為 fetch_single_chunk 已處理

                # 確保 final_df['datetime'] 是 datetime64[ns, UTC]
                if 'datetime' not in final_df.columns or not pd.api.types.is_datetime64_any_dtype(final_df['datetime']):
                    try:
                        if 'datetime' in final_df.columns:
                            final_df['datetime'] = pd.to_datetime(final_df['datetime'], utc=True)
                        else:
                             raise ValueError("final_df is missing 'datetime' column for final log update")
                    except Exception as e_final_conv:
                         print(f"錯誤(hydrate_data_range): final_df['datetime'] 處理失敗 for {ticker}: {e_final_conv}")
                         print(f"警告(hydrate_data_range): 因 final_df datetime 處理失敗，執行日誌可能不完全準確。Ticker: {ticker}")
                         # 即使 datetime 處理失敗，仍然返回已獲取的數據和當前 execution_log
                         return final_df, execution_log

                # 使用 'datetime' 列來遍歷日期並更新 execution_log
                # 確保 final_df['datetime'] 列存在且類型正確後才進行遍歷
                unique_dates_in_final_df = final_df['datetime'].dt.normalize().unique()

                for date_obj_in_final_df in unique_dates_in_final_df:
                    date_str_in_final_df_range = date_obj_in_final_df.strftime('%Y-%m-%d')

                    if date_str_in_final_df_range in execution_log and \
                       ticker in execution_log[date_str_in_final_df_range]:

                        daily_rows_final = final_df[final_df['datetime'].dt.date == date_obj_in_final_df.date()]

                        if not daily_rows_final.empty:
                            # 只有當天確實有數據才更新為最終的 success 狀態
                            execution_log[date_str_in_final_df_range][ticker] = {
                                "status": "success",
                                "interval": interval,
                                "count": len(daily_rows_final),
                                "message": f"Final data for {date_str_in_final_df_range} with {interval} ({len(daily_rows_final)} rows)."
                            }
                        # 如果 daily_rows_final 為空, 但 execution_log 中該日期之前可能已有記錄 (例如來自 chunk 級別的 no_data_in_chunk_for_day)
                        # 這裡的邏輯是，如果 final_df 中某天沒有數據，但它在請求範圍內，其日誌狀態應反映這一點
                        # 但由於我們是從 final_df 的 unique_dates 遍歷，所以 daily_rows_final 不應為空
                        # 此處的 else if 更多是防禦性編碼，或處理更複雜的日誌合併邏輯（如果需要）
                        elif execution_log[date_str_in_final_df_range][ticker].get('status') != 'success':
                            current_message = execution_log[date_str_in_final_df_range][ticker].get("message", "")
                            if not isinstance(current_message, str): current_message = str(current_message)
                            execution_log[date_str_in_final_df_range][ticker]['message'] = current_message + \
                                f" No data for {date_str_in_final_df_range} found in final combined df with {interval} (unexpected, check logic)."

                print(f"成功: hydrate_data_range: 已使用顆粒度 '{interval}' 完成 {ticker} 在 {start_date_str} 到 {end_date_str} 的所有數據回填。共 {len(final_df)} 筆。")
                print(f"===== 數據回填任務結束 (成功): Ticker={ticker} =====")
                return final_df, execution_log
            elif not current_interval_all_data_dfs and all_chunks_successful_for_this_interval: # 所有 chunk 成功但都沒數據
                 print(f"INFO: hydrate_data_range: 顆粒度 '{interval}' 所有區塊均未返回數據(可能該時段無交易)，但未發生API錯誤。嘗試下一個顆粒度。")
                 # 更新日誌，標記這些日期使用此 interval 時無數據
                 for date_str_in_range in request_date_range_str: # 遍歷請求的整個日期範圍
                     # 只有在之前的狀態不是更明確的成功或特定跳過時才更新
                     if execution_log[date_str_in_range][ticker]['status'] not in ['success', 'skipped_1m_due_to_30day_limit']:
                        execution_log[date_str_in_range][ticker].update({ # 使用 update 而不是覆蓋
                            "status": "no_data_for_interval",
                            "interval": interval, # 記錄是哪個 interval 沒數據
                            "count": 0,
                            "message": execution_log[date_str_in_range][ticker].get("message","") + f" No data found for {date_str_in_range} with {interval} after all chunks."
                        })
            else: # all_chunks_successful_for_this_interval is False (即某個 chunk 失敗了)
                print(f"INFO: hydrate_data_range: 顆粒度 '{interval}' 未能成功回填所有區塊。嘗試下一個更粗的顆粒度。")
                # execution_log 應已被 chunk 級別的失敗更新 (例如 failed_chunk, 或 skipped_1m)
                # 無需在此處再次遍歷 request_date_range_str 來更新日誌，因為失敗的 chunk 已處理其覆蓋的日期

            time.sleep(0.5) # 在嘗試不同 interval 之間稍作停頓

        # 如果所有 interval 都嘗試失敗
        print(f"錯誤: hydrate_data_range: 所有降級顆粒度 {self.FALLBACK_INTERVALS} 均無法為 {ticker} 在 {start_date_str} 到 {end_date_str} 範圍內回填任何數據。")
        print(f"===== 數據回填任務結束 (失敗): Ticker={ticker} =====")
        # 更新日誌中所有仍在 pending 的狀態為最終失敗
        for date_str_in_range in request_date_range_str:
            if execution_log[date_str_in_range][ticker]['status'] not in ["success", "skipped_1m_due_to_30day_limit"]:
                 execution_log[date_str_in_range][ticker] = {
                    "status": "failed_all_intervals", "interval": None, "count": 0,
                    "message": f"All intervals failed for {date_str_in_range}."
                }
        return None, execution_log

if __name__ == '__main__':
    print("--- YFinanceClient (Daily Market Analyzer) 測試 ---")
    client = YFinanceClient()

    # 測試日期範圍和股票代碼
    test_ticker_aapl = "AAPL"
    test_ticker_vix = "^VIX" # 通常沒有分鐘線數據
    test_ticker_fake = "FAKEBADTICKERXYZ"

    # 測試案例 1: AAPL，近期數據，應能獲取 1m
    # 將結束日期設為昨天，開始日期為三天前，以確保在30天窗口內
    end_date_dt_recent = datetime.now() - timedelta(days=1)
    start_date_dt_recent = end_date_dt_recent - timedelta(days=2) # 抓取3天數據
    test_start_recent = start_date_dt_recent.strftime("%Y-%m-%d")
    test_end_recent = end_date_dt_recent.strftime("%Y-%m-%d")

    print(f"\n--- 測試案例 1: {test_ticker_aapl}, 近期範圍: [{test_start_recent} to {test_end_recent}] ---")
    hydrated_data_aapl, exec_log_aapl = client.hydrate_data_range(test_ticker_aapl, test_start_recent, test_end_recent)

    if hydrated_data_aapl is not None and not hydrated_data_aapl.empty:
        print(f"INFO: {test_ticker_aapl} 成功獲取 {len(hydrated_data_aapl)} 筆數據。")
        print(f"INFO: 使用的顆粒度: {hydrated_data_aapl['interval'].unique()}")
        # 驗證 execution_log
        print("INFO: Execution Log (AAPL 近期) 預覽:")
        for date_str, ticker_log in exec_log_aapl.items():
            if test_ticker_aapl in ticker_log:
                print(f"  {date_str}: {ticker_log[test_ticker_aapl]}")
                # 基本斷言
                assert 'status' in ticker_log[test_ticker_aapl]
                assert 'interval' in ticker_log[test_ticker_aapl]
                assert 'count' in ticker_log[test_ticker_aapl]
                if ticker_log[test_ticker_aapl]['status'] == 'success':
                    assert ticker_log[test_ticker_aapl]['count'] > 0
                    assert ticker_log[test_ticker_aapl]['interval'] is not None # 應該是 '1m' 或其他有效 interval
    else:
        print(f"WARN: {test_ticker_aapl} 未能回填近期數據。檢查API或日期範圍。")
    print(f"--- {test_ticker_aapl} 近期數據日誌 (部分): ---")
    # print(exec_log_aapl)


    # 測試案例 2: AAPL，遠期數據 (>30天前)，1m 應被跳過
    # 固定一個較早的日期範圍，確保它肯定超過30天
    test_start_old = "2023-01-03" # 週二
    test_end_old = "2023-01-04"   # 週三 (抓兩天數據)
    print(f"\n--- 測試案例 2: {test_ticker_aapl}, 遠期範圍: [{test_start_old} to {test_end_old}] (預期跳過1m) ---")
    hydrated_data_aapl_old, exec_log_aapl_old = client.hydrate_data_range(test_ticker_aapl, test_start_old, test_end_old)

    if hydrated_data_aapl_old is not None and not hydrated_data_aapl_old.empty:
        print(f"INFO: {test_ticker_aapl} (遠期) 成功獲取 {len(hydrated_data_aapl_old)} 筆數據。")
        print(f"INFO: 使用的顆粒度: {hydrated_data_aapl_old['interval'].unique()}") # 應該不是 '1m'
        assert '1m' not in hydrated_data_aapl_old['interval'].unique()
    else:
        print(f"WARN: {test_ticker_aapl} (遠期) 未能回填數據。")

    print("INFO: Execution Log (AAPL 遠期) 預覽:")
    first_day_log_found = False
    for date_str, ticker_log in exec_log_aapl_old.items():
        if test_ticker_aapl in ticker_log:
            print(f"  {date_str}: {ticker_log[test_ticker_aapl]}")
            # 檢查遠期第一天的日誌是否記錄了跳過1m，或者成功獲取了其他 interval
            # 由於 fallback 機制，如果 1m 被跳過，它會嘗試 5m 等。
            # 所以 status 可能是 success (來自 5m)，或者 skipped_1m... 如果 hydrate_data_range 被修改為這樣記錄
            # 當前實現是，如果1m的chunk因超時跳過，整個1m的嘗試會失敗，然後fallback到5m等。
            # 所以我們應該檢查最終成功的interval不是1m。
            if ticker_log[test_ticker_aapl]['status'] == 'success':
                 assert ticker_log[test_ticker_aapl]['interval'] != '1m'
            first_day_log_found = True
    assert first_day_log_found, "Execution log for AAPL (old) seems empty or malformed."
    # print(f"--- {test_ticker_aapl} 遠期數據日誌 (部分): ---")
    # print(exec_log_aapl_old)


    # 測試案例 3: ^VIX (通常1m, 5m等會失敗，最終可能用1d)
    test_start_vix = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d") # 確保在30天內，但VIX仍可能無1m數據
    test_end_vix = (datetime.now() - timedelta(days=8)).strftime("%Y-%m-%d")
    print(f"\n--- 測試案例 3: {test_ticker_vix}, Range: [{test_start_vix} to {test_end_vix}] ---")
    vix_data, vix_exec_log = client.hydrate_data_range(test_ticker_vix, test_start_vix, test_end_vix)
    if vix_data is not None and not vix_data.empty:
        print(f"INFO: {test_ticker_vix} 測試成功獲取 {len(vix_data)} 筆數據，顆粒度: {vix_data['interval'].unique()}")
        # 通常VIX的最高頻率是1d，如果獲取到更高頻率，那也沒問題，但不太可能。
        # 主要檢查 execution_log 是否合理
    else:
        print(f"WARN: {test_ticker_vix} 測試未能回填數據。")
    print("INFO: Execution Log (VIX) 預覽:")
    for date_str, ticker_log in vix_exec_log.items():
         if test_ticker_vix in ticker_log:
            print(f"  {date_str}: {ticker_log[test_ticker_vix]}")
            if ticker_log[test_ticker_vix]['status'] == 'success':
                assert ticker_log[test_ticker_vix]['interval'] is not None

    # 測試案例 4: 無效股票代碼
    print(f"\n--- 測試案例 4: {test_ticker_fake} ---")
    fake_data, fake_exec_log = client.hydrate_data_range(test_ticker_fake, test_start_recent, test_end_recent)
    if fake_data is None or fake_data.empty: # 預期是 None
        print("INFO: 無效股票代碼測試成功，未返回數據 (符合預期)。")
    else:
        print(f"ERROR：無效股票代碼不應返回數據，卻得到 {len(fake_data)} 筆。")
    print("INFO: Execution Log (FAKE) 預覽:")
    for date_str, ticker_log in fake_exec_log.items():
        if test_ticker_fake in ticker_log:
            print(f"  {date_str}: {ticker_log[test_ticker_fake]}")
            assert ticker_log[test_ticker_fake]['status'] == 'failed_all_intervals'
            assert ticker_log[test_ticker_fake]['count'] == 0
            assert ticker_log[test_ticker_fake]['interval'] is None

    # 測試案例 5: 獲取日線數據 (AAPL，遠期)，檢查 'datetime' 列
    test_start_daily = "2023-02-01"
    test_end_daily = "2023-02-03" # 獲取三天日線數據
    print(f"\n--- 測試案例 5: {test_ticker_aapl}, 日線數據檢查: [{test_start_daily} to {test_end_daily}] ---")
    daily_data_df, daily_exec_log = client.hydrate_data_range(test_ticker_aapl, test_start_daily, test_end_daily)

    if daily_data_df is not None and not daily_data_df.empty:
        print(f"INFO: {test_ticker_aapl} (日線測試) 成功獲取 {len(daily_data_df)} 筆數據。")
        print(f"INFO: 使用的顆粒度: {daily_data_df['interval'].unique()}")
        assert '1d' in daily_data_df['interval'].unique() # 應該是 '1d'

        print("INFO: DataFrame (日線測試) 預覽 (前2筆):")
        print(daily_data_df.head(2))
        daily_data_df.info() # 打印詳細信息以供檢查

        # 關鍵驗證：'datetime' 列是否存在且類型正確
        assert 'datetime' in daily_data_df.columns, "DataFrame 中缺少 'datetime' 欄位"
        assert pd.api.types.is_datetime64_any_dtype(daily_data_df['datetime']), "'datetime' 欄位類型不正確"
        assert daily_data_df['datetime'].dt.tz is not None and daily_data_df['datetime'].dt.tz.zone == 'UTC', "'datetime' 欄位時區不正確或非UTC"

        print("INFO: Execution Log (AAPL 日線測試) 預覽:")
        for date_str, ticker_log in daily_exec_log.items():
            if test_ticker_aapl in ticker_log:
                print(f"  {date_str}: {ticker_log[test_ticker_aapl]}")
                if ticker_log[test_ticker_aapl]['status'] == 'success':
                    assert ticker_log[test_ticker_aapl]['interval'] == '1d'
    else:
        print(f"WARN: {test_ticker_aapl} (日線測試) 未能回填數據。")

    print("\n--- YFinanceClient (Daily Market Analyzer) 測試完畢 ---")
