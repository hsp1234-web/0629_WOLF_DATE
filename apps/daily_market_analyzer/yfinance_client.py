# -*- coding: utf-8 -*-
"""
YFinanceClient for Daily Market Analyzer (v12.0)
"""
import yfinance as yf
import pandas as pd
import time
import os
from datetime import datetime, timedelta

class YFinanceClient:
    def __init__(self, cache_dir="data_workspace/cache/daily_market_analyzer"):
        self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)
        self.FALLBACK_INTERVALS = ['1m', '5m', '15m', '30m', '1h', '1d', '1wk', '1mo']
        print(f"INFO: YFinanceClient (Daily Market Analyzer v12.0) 初始化完畢，快取目錄: {self.cache_dir}")
        print(f"INFO: 區間降級鏈: {self.FALLBACK_INTERVALS}")

    def _get_chunk_size_for_interval(self, interval: str) -> int:
        if interval == '1m': return 6
        elif interval in ['2m', '5m', '15m', '30m']: return 55
        elif interval in ['60m', '90m', '1h']: return 700
        elif interval in ['1d', '5d', '1wk']: return 365 * 2
        elif interval in ['1mo', '3mo']: return 365 * 5
        print(f"警告: 未知的 interval '{interval}' 在 _get_chunk_size_for_interval，預設 chunk_size_days 為 30。")
        return 30

    def _split_date_range_into_chunks(self, start_date_str: str, end_date_str: str, chunk_size_days: int) -> list[tuple[str, str]]:
        chunks = []
        try:
            current_start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date() # Use .date()
            final_end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()     # Use .date()
        except ValueError as e:
            print(f"錯誤: 日期格式錯誤 ({start_date_str}, {end_date_str})。 {e}")
            return []

        if chunk_size_days <= 0:
            print(f"錯誤: chunk_size_days ({chunk_size_days}) 必須為正數。")
            return []

        while current_start_date <= final_end_date:
            chunk_actual_end_date = current_start_date + timedelta(days=chunk_size_days - 1)
            if chunk_actual_end_date > final_end_date:
                chunk_actual_end_date = final_end_date

            # yfinance end_date is exclusive
            yfinance_exclusive_end_date = chunk_actual_end_date + timedelta(days=1)

            chunks.append((
                current_start_date.strftime("%Y-%m-%d"),
                yfinance_exclusive_end_date.strftime("%Y-%m-%d")
            ))
            current_start_date = chunk_actual_end_date + timedelta(days=1)
        return chunks

    def fetch_single_chunk(self, ticker: str, chunk_start_date_str: str, chunk_end_date_str: str, interval: str) -> tuple[pd.DataFrame | None, str]:
        status_message = ""
        try:
            stock = yf.Ticker(ticker)
            data = stock.history(start=chunk_start_date_str, end=chunk_end_date_str,
                                 interval=interval, auto_adjust=True, prepost=False, raise_errors=False) # raise_errors=False to catch yfinance errors

            if data is None or data.empty:
                # Check for specific yfinance errors if available (though raise_errors=False might suppress them)
                # For now, assume any empty DataFrame or None means no data for that chunk/interval combination.
                status_message = f"yf returned no data for {ticker} ({interval}) in chunk {chunk_start_date_str} to <{chunk_end_date_str}."
                return None, status_message

            data.columns = [col.lower() for col in data.columns]
            if 'volume' not in data.columns: data['volume'] = 0
            data['volume'] = data['volume'].fillna(0).astype('int64')

            if data.index.tz is None: data.index = data.index.tz_localize('UTC')
            else: data.index = data.index.tz_convert('UTC')

            data['interval'] = interval # Add interval column to the df itself
            data['ticker'] = ticker     # Add ticker column
            status_message = f"Successfully fetched {len(data)} rows for chunk {chunk_start_date_str} to <{chunk_end_date_str}."
            return data, status_message
        except Exception as e: # Catch any other exceptions
            status_message = f"Exception fetching chunk for {ticker} ({interval}) {chunk_start_date_str}-<{chunk_end_date_str}: {type(e).__name__} - {str(e)}"
            print(f"ERROR: {status_message}")
            return None, status_message

    def hydrate_data_range(self, ticker: str, start_date_str: str, end_date_str: str) -> tuple[pd.DataFrame | None, dict]:
        print(f"===== hydrate_data_range v12.0: Ticker={ticker}, Range=[{start_date_str} to {end_date_str}] =====")

        execution_log = {}
        try:
            s_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            e_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            if s_date > e_date:
                print(f"ERROR: Start date {start_date_str} is after end date {end_date_str}.")
                return None, {"error": "Invalid date range"}
        except ValueError:
            print(f"ERROR: Invalid date format for {start_date_str} or {end_date_str}. Use YYYY-MM-DD.")
            return None, {"error": "Invalid date format"}

        requested_date_strs = []
        current_iter_date = s_date
        while current_iter_date <= e_date:
            requested_date_strs.append(current_iter_date.strftime("%Y-%m-%d"))
            execution_log.setdefault(requested_date_strs[-1], {})[ticker] = {
                "status": "pending", "interval": None, "count": 0, "message": "Awaiting processing"
            }
            current_iter_date += timedelta(days=1)

        final_df_for_ticker = None
        thirty_days_ago_date = (datetime.now() - timedelta(days=30)).date()

        for interval in self.FALLBACK_INTERVALS:
            print(f"\nINFO: Trying interval '{interval}' for {ticker} [{start_date_str} to {end_date_str}]...")
            chunk_size_days = self._get_chunk_size_for_interval(interval)
            date_chunks = self._split_date_range_into_chunks(start_date_str, end_date_str, chunk_size_days)

            if not date_chunks: continue

            current_interval_chunk_dfs = []
            all_chunks_for_interval_processed_successfully_or_empty = True # True if all chunks either got data or yf returned empty (no error)

            for chunk_start_s, chunk_end_s in date_chunks:
                chunk_actual_start_dt = datetime.strptime(chunk_start_s, "%Y-%m-%d").date()
                chunk_actual_end_dt = datetime.strptime(chunk_end_s, "%Y-%m-%d").date() - timedelta(days=1)

                log_message_prefix = f"Interval '{interval}', chunk {chunk_start_s} to {chunk_actual_end_dt.strftime('%Y-%m-%d')}: "

                if interval == '1m' and chunk_actual_start_dt < thirty_days_ago_date:
                    msg = f"Skipping '1m' for {ticker}; chunk start {chunk_start_s} is outside 30-day window."
                    print(f"INFO: {log_message_prefix}{msg}")
                    all_chunks_for_interval_processed_successfully_or_empty = False
                    iter_date_in_skipped_chunk = chunk_actual_start_dt
                    while iter_date_in_skipped_chunk <= chunk_actual_end_dt:
                        log_date_key = iter_date_in_skipped_chunk.strftime("%Y-%m-%d")
                        if log_date_key in execution_log:
                            execution_log[log_date_key][ticker].update({
                                "status": "skipped_1m_due_to_30day_limit", "interval": "1m", "count": 0, "message": msg})
                        iter_date_in_skipped_chunk += timedelta(days=1)
                    break # Fail this interval, try next.

                chunk_df, fetch_status_msg = self.fetch_single_chunk(ticker, chunk_start_s, chunk_end_s, interval)

                iter_date_in_chunk = chunk_actual_start_dt
                while iter_date_in_chunk <= chunk_actual_end_dt:
                    log_date_key = iter_date_in_chunk.strftime("%Y-%m-%d")
                    if log_date_key in requested_date_strs:
                        if chunk_df is not None and not chunk_df.empty:
                            daily_rows_in_chunk = chunk_df[chunk_df.index.date == iter_date_in_chunk]
                            count_for_day = len(daily_rows_in_chunk)
                            execution_log[log_date_key][ticker].update({
                                "status": "success", "interval": interval, "count": count_for_day,
                                "message": f"Data for {log_date_key} ({interval}): {count_for_day} rows. Chunk fetch: {fetch_status_msg}"
                            })
                        else: # chunk_df is None or empty from fetch_single_chunk
                            # If status was pending or already a less specific failure, update it.
                            if execution_log[log_date_key][ticker]['status'] not in ["success", "skipped_1m_due_to_30day_limit"]:
                                execution_log[log_date_key][ticker].update({
                                    "status": "failed_chunk_no_data", "interval": interval, "count": 0,
                                    "message": log_message_prefix + fetch_status_msg
                                })
                            all_chunks_for_interval_processed_successfully_or_empty = False # If any part of interval has no data, it's not fully successful for this interval
                    iter_date_in_chunk += timedelta(days=1)

                if chunk_df is not None and not chunk_df.empty:
                    current_interval_chunk_dfs.append(chunk_df)
                elif "Error fetching chunk" in fetch_status_msg: # Hard error from yfinance
                     all_chunks_for_interval_processed_successfully_or_empty = False
                     break # Stop this interval if a hard error occurred on a chunk
                # If chunk_df is None/empty but no hard error, continue to next chunk for this interval

            if all_chunks_for_interval_processed_successfully_or_empty and current_interval_chunk_dfs:
                final_df_for_ticker = pd.concat(current_interval_chunk_dfs, ignore_index=False)
                # Update log status to 'success' for all dates covered by this successful interval
                for log_date_key in requested_date_strs:
                    date_obj_for_final_count = datetime.strptime(log_date_key, "%Y-%m-%d").date()
                    daily_data_for_log = final_df_for_ticker[final_df_for_ticker.index.date == date_obj_for_final_count]
                    if not daily_data_for_log.empty:
                         execution_log[log_date_key][ticker].update({
                            "status": "success", "interval": interval, "count": len(daily_data_for_log),
                            "message": f"Successfully hydrated with {interval}, {len(daily_data_for_log)} rows for the day."
                        })
                    # If a day had no data even in the successful interval, its log should reflect that
                    elif execution_log[log_date_key][ticker]['status'] != 'success': # Don't overwrite if already success
                         execution_log[log_date_key][ticker].update({
                            "status": "no_data_in_successful_interval", "interval": interval, "count": 0,
                            "message": f"No data for {log_date_key} although interval {interval} was processed for other days."
                        })

                print(f"SUCCESS: Interval '{interval}' fully hydrated for {ticker}. Total rows: {len(final_df_for_ticker)}")
                return final_df_for_ticker, execution_log

            if not all_chunks_for_interval_processed_successfully_or_empty:
                 print(f"INFO: Interval '{interval}' failed or was skipped for some chunks for {ticker}. Trying next coarser interval.")
            elif not current_interval_chunk_dfs:
                print(f"INFO: Interval '{interval}' yielded no data for {ticker} across all chunks. Trying next coarser interval.")
                for log_date_key in requested_date_strs:
                    if execution_log[log_date_key][ticker]['status'] not in ['success', 'skipped_1m_due_to_30day_limit']:
                        execution_log[log_date_key][ticker].update({
                            "status": "no_data_for_interval", "interval": interval, "count": 0,
                            "message": f"No data found with {interval} for {log_date_key} after all chunks."})
            time.sleep(0.5)

        print(f"ERROR: All fallback intervals failed for {ticker} in range {start_date_str}-{end_date_str}.")
        for log_date_key in requested_date_strs:
            if execution_log[log_date_key][ticker]['status'] not in ['success', 'skipped_1m_due_to_30day_limit']:
                 execution_log[log_date_key][ticker].update({
                    "status": "failed_all_intervals", "interval": None, "count": 0,
                    "message": "All intervals failed or yielded no complete data for this day."})
        return None, execution_log

if __name__ == '__main__':
    print("--- YFinanceClient (Daily Market Analyzer v12.0) 測試 ---")
    client = YFinanceClient()

    test_ticker = "AAPL"
    # Test case 1: Recent date range
    start_date_1 = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d") # Within 30 days
    end_date_1 = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

    print(f"\n--- Test Case 1: {test_ticker}, Range [{start_date_1} to {end_date_1}] (Recent) ---")
    df1, log1 = client.hydrate_data_range(test_ticker, start_date_1, end_date_1)
    if df1 is not None: print(f"DF1 Result: {len(df1)} rows, Interval(s): {df1['interval'].unique() if not df1.empty else 'N/A'}")
    print("Execution Log1:")
    for date_key, tickers_log in sorted(log1.items()): print(f"  {date_key}: {tickers_log.get(test_ticker)}")

    # Test case 2: Older date range, 1m should be skipped for chunks starting too early
    start_date_2 = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d")
    end_date_2 = (datetime.now() - timedelta(days=37)).strftime("%Y-%m-%d")
    print(f"\n--- Test Case 2: {test_ticker}, Range [{start_date_2} to {end_date_2}] (Older, 1m skip expected) ---")
    df2, log2 = client.hydrate_data_range(test_ticker, start_date_2, end_date_2)
    if df2 is not None: print(f"DF2 Result: {len(df2)} rows, Interval(s): {df2['interval'].unique() if not df2.empty else 'N/A'}")
    print("Execution Log2:")
    for date_key, tickers_log in sorted(log2.items()): print(f"  {date_key}: {tickers_log.get(test_ticker)}")

    # Test case 3: Date range straddling the 30-day limit for 1m
    start_date_3 = (datetime.now() - timedelta(days=32)).strftime("%Y-%m-%d") # Starts outside 30-day window
    end_date_3 = (datetime.now() - timedelta(days=28)).strftime("%Y-%m-%d")   # Ends inside 30-day window
    print(f"\n--- Test Case 3: {test_ticker}, Range [{start_date_3} to {end_date_3}] (Straddling 30-day limit) ---")
    df3, log3 = client.hydrate_data_range(test_ticker, start_date_3, end_date_3)
    if df3 is not None: print(f"DF3 Result: {len(df3)} rows, Interval(s): {df3['interval'].unique() if not df3.empty else 'N/A'}")
    print("Execution Log3:")
    for date_key, tickers_log in sorted(log3.items()): print(f"  {date_key}: {tickers_log.get(test_ticker)}")

    print("\n--- YFinanceClient (Daily Market Analyzer v12.0) 測試完畢 ---")
