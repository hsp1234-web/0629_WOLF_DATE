# -*- coding: utf-8 -*-
"""
DuckDB 資料庫管理模組 for Daily Market Analyzer (v12.0)。
"""
import duckdb
import pandas as pd
import os
from datetime import datetime, timedelta

class DBManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
        print(f"INFO: DBManager (Daily Market Analyzer v12.0) 初始化完畢，資料庫路徑: {self.db_path}")

    def create_ohlcv_table(self, table_name: str = "market_ohlcv_analyzer"):
        create_sql = f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            datetime TIMESTAMPTZ NOT NULL,
            ticker VARCHAR NOT NULL,
            interval VARCHAR NOT NULL,
            open DOUBLE PRECISION NOT NULL,
            high DOUBLE PRECISION NOT NULL,
            low DOUBLE PRECISION NOT NULL,
            close DOUBLE PRECISION NOT NULL,
            volume BIGINT,
            PRIMARY KEY (ticker, datetime, interval)
        );
        """
        try:
            with duckdb.connect(self.db_path) as con:
                con.execute(create_sql)
            print(f"INFO: 資料表 '{table_name}' 已在資料庫 '{self.db_path}' 中準備就緒.")
        except Exception as e:
            print(f"錯誤: 建立資料表 '{table_name}' 失敗: {e}")
            raise

    def upsert_data(self, df: pd.DataFrame, table_name: str):
        if df.empty:
            # Extract ticker for logging if possible, even for an empty df
            current_ticker = "未知Ticker"
            if 'ticker' in df.columns and not df.empty: # Should not happen if df.empty is true
                current_ticker = df['ticker'].iloc[0]
            elif hasattr(df, 'name') and df.name:
                 current_ticker = df.name
            print(f"INFO:傳入的 DataFrame ({current_ticker}) 為空，無需寫入資料表 '{table_name}'。")
            return

        df_to_insert = df.copy()
        if isinstance(df_to_insert.index, pd.DatetimeIndex):
            df_to_insert = df_to_insert.reset_index()

        df_to_insert.columns = [col.lower() for col in df_to_insert.columns]
        if 'index' in df_to_insert.columns and 'datetime' not in df_to_insert.columns:
            df_to_insert.rename(columns={'index': 'datetime'}, inplace=True)

        required_cols = ['datetime', 'ticker', 'interval', 'open', 'high', 'low', 'close', 'volume']
        missing_cols = [col for col in required_cols if col not in df_to_insert.columns]

        current_ticker_for_error = "未知Ticker"
        if 'ticker' in df_to_insert.columns and not df_to_insert.empty :
            current_ticker_for_error = df_to_insert['ticker'].iloc[0]

        if missing_cols:
            print(f"錯誤: DataFrame ({current_ticker_for_error}) 缺少必要欄位: {', '.join(missing_cols)}。無法寫入。")
            return

        df_to_insert = df_to_insert[required_cols]

        try:
            if not pd.api.types.is_datetime64_any_dtype(df_to_insert['datetime']):
                df_to_insert['datetime'] = pd.to_datetime(df_to_insert['datetime'])
            if df_to_insert['datetime'].dt.tz is None:
                df_to_insert['datetime'] = df_to_insert['datetime'].dt.tz_localize('UTC')
            else:
                df_to_insert['datetime'] = df_to_insert['datetime'].dt.tz_convert('UTC')

            for col in ['open', 'high', 'low', 'close']:
                df_to_insert[col] = pd.to_numeric(df_to_insert[col], errors='raise')
            df_to_insert['volume'] = df_to_insert['volume'].astype('int64')
            df_to_insert['ticker'] = df_to_insert['ticker'].astype(str)
            df_to_insert['interval'] = df_to_insert['interval'].astype(str)
        except Exception as e:
            print(f"錯誤: DataFrame ({current_ticker_for_error}) 數據類型轉換失敗: {e}")
            return

        try:
            with duckdb.connect(self.db_path) as con:
                con.register('df_view_to_insert', df_to_insert)
                columns_str = ", ".join(required_cols)
                upsert_sql = f"INSERT OR REPLACE INTO {table_name} ({columns_str}) SELECT {columns_str} FROM df_view_to_insert"
                con.execute(upsert_sql)
                con.unregister('df_view_to_insert')
            print(f"INFO: 成功將 {len(df_to_insert)} 筆來自 '{current_ticker_for_error}' 的數據寫入/更新至 '{table_name}'。")
        except Exception as e:
            print(f"錯誤: 寫入數據到 '{table_name}' 失敗 (Ticker: {current_ticker_for_error}): {e}")

    def query_data_for_day(self, ticker: str, date_str: str, table_name: str = "market_ohlcv_analyzer") -> pd.DataFrame:
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d")
            start_of_day = f"{date_str} 00:00:00"
            start_of_next_day = (target_date + timedelta(days=1)).strftime("%Y-%m-%d 00:00:00")

            query = f"""
            SELECT * FROM {table_name}
            WHERE ticker = ? AND datetime >= CAST(? AS TIMESTAMPTZ) AND datetime < CAST(? AS TIMESTAMPTZ)
            ORDER BY datetime ASC
            """
            with duckdb.connect(self.db_path) as con:
                result_df = con.execute(query, [ticker, start_of_day, start_of_next_day]).fetchdf()

            if not result_df.empty and 'datetime' in result_df.columns:
                result_df['datetime'] = pd.to_datetime(result_df['datetime'])
                if result_df['datetime'].dt.tz is None:
                    result_df['datetime'] = result_df['datetime'].dt.tz_localize('UTC')
                else:
                    result_df['datetime'] = result_df['datetime'].dt.tz_convert('UTC')
                result_df = result_df.set_index('datetime')
            return result_df
        except Exception as e:
            print(f"錯誤: 查詢 {ticker} 在 {date_str} 的數據失敗: {e}")
            return pd.DataFrame()

    def query_previous_day_close(self, ticker: str, current_date_str: str, table_name: str = "market_ohlcv_analyzer", max_lookback_days: int = 30) -> float | None:
        try:
            current_date_obj = datetime.strptime(current_date_str, "%Y-%m-%d").date()
            for i in range(1, max_lookback_days + 1):
                prev_date_to_check = current_date_obj - timedelta(days=i)
                prev_date_to_check_str = prev_date_to_check.strftime("%Y-%m-%d")
                daily_data_df = self.query_data_for_day(ticker, prev_date_to_check_str, table_name)

                if not daily_data_df.empty:
                    # Prefer '1d' interval if available for previous day's close
                    daily_1d_data = daily_data_df[daily_data_df['interval'] == '1d']
                    if not daily_1d_data.empty:
                        return daily_1d_data['close'].iloc[-1]
                    return daily_data_df['close'].iloc[-1] # Fallback to last record of any interval
            return None
        except Exception as e:
            print(f"錯誤: 查詢 {ticker} 在 {current_date_str} 之前的收盤價失敗: {e}")
            return None

if __name__ == '__main__':
    print("--- DBManager (Daily Market Analyzer v12.0) 測試 ---")
    test_db_path = "data_workspace/temp/test_analyzer_v12_market_data.duckdb"
    if os.path.exists(test_db_path):
        os.remove(test_db_path)

    db_manager = DBManager(test_db_path)
    table_name = "market_ohlcv_analyzer_test_v12"

    print(f"\n--- 測試 1: 建立 {table_name} 資料表 ---")
    db_manager.create_ohlcv_table(table_name=table_name)

    # 準備測試數據
    data_for_db = []
    # Day 1: AAPL (1d and 5m), MSFT (1d)
    day1_dt = datetime.strptime("2024-07-01", "%Y-%m-%d")
    data_for_db.append({'datetime': day1_dt.replace(hour=16, minute=0, tzinfo=timedelta(0)), 'ticker': 'AAPL', 'interval': '1d', 'open': 150, 'high': 152, 'low': 149, 'close': 151, 'volume': 1000})
    data_for_db.append({'datetime': day1_dt.replace(hour=15, minute=55, tzinfo=timedelta(0)), 'ticker': 'AAPL', 'interval': '5m', 'open': 150.8, 'high': 151, 'low': 150.7, 'close': 150.9, 'volume': 100}) # Earlier than 1d record
    data_for_db.append({'datetime': day1_dt.replace(hour=16, minute=5, tzinfo=timedelta(0)), 'ticker': 'AAPL', 'interval': '5m', 'open': 150.9, 'high': 151.1, 'low': 150.8, 'close': 151.0, 'volume': 120}) # Later than 1d record (for testing iloc[-1])
    data_for_db.append({'datetime': day1_dt.replace(hour=16, minute=0, tzinfo=timedelta(0)), 'ticker': 'MSFT', 'interval': '1d', 'open': 200, 'high': 202, 'low': 199, 'close': 201, 'volume': 2000})

    # Day 2: AAPL (only 5m), MSFT (1d and 5m)
    day2_dt = datetime.strptime("2024-07-02", "%Y-%m-%d")
    data_for_db.append({'datetime': day2_dt.replace(hour=15, minute=55, tzinfo=timedelta(0)), 'ticker': 'AAPL', 'interval': '5m', 'open': 151.8, 'high': 152, 'low': 151.7, 'close': 151.9, 'volume': 150})
    data_for_db.append({'datetime': day2_dt.replace(hour=16, minute=0, tzinfo=timedelta(0)), 'ticker': 'MSFT', 'interval': '1d', 'open': 201, 'high': 203, 'low': 200, 'close': 202, 'volume': 2200})
    data_for_db.append({'datetime': day2_dt.replace(hour=16, minute=5, tzinfo=timedelta(0)), 'ticker': 'MSFT', 'interval': '5m', 'open': 202.1, 'high': 202.5, 'low': 202.0, 'close': 202.3, 'volume': 220})

    # Day 3: AAPL (1d only)
    day3_dt = datetime.strptime("2024-07-03", "%Y-%m-%d")
    data_for_db.append({'datetime': day3_dt.replace(hour=16, minute=0, tzinfo=timedelta(0)), 'ticker': 'AAPL', 'interval': '1d', 'open': 152, 'high': 153, 'low': 151.5, 'close': 152.5, 'volume': 1100})


    df_to_load = pd.DataFrame(data_for_db)
    df_to_load['datetime'] = pd.to_datetime(df_to_load['datetime'])
    df_to_load = df_to_load.set_index('datetime')

    print(f"\n--- 測試 upsert_data with mixed intervals ---")
    db_manager.upsert_data(df_to_load, table_name)

    with duckdb.connect(test_db_path) as con:
        count = con.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        print(f"Total rows in {table_name}: {count}")
        assert count == len(data_for_db)

    print("\n--- 測試 query_data_for_day ---")
    aapl_day1_data = db_manager.query_data_for_day('AAPL', '2024-07-01', table_name)
    print(f"AAPL 2024-07-01 data (expected 3 rows, 1d and 5m):\n{aapl_day1_data}")
    assert len(aapl_day1_data) == 3
    assert '1d' in aapl_day1_data['interval'].values
    assert '5m' in aapl_day1_data['interval'].values

    msft_day2_data = db_manager.query_data_for_day('MSFT', '2024-07-02', table_name)
    print(f"MSFT 2024-07-02 data (expected 2 rows, 1d and 5m):\n{msft_day2_data}")
    assert len(msft_day2_data) == 2

    print("\n--- 測試 query_previous_day_close ---")
    # Test 1: Prev day has '1d' data for AAPL (close should be 151.0 from 2024-07-01 1d)
    prev_close1 = db_manager.query_previous_day_close('AAPL', '2024-07-02', table_name)
    print(f"AAPL prev close for 2024-07-02: {prev_close1} (expected 151.0)")
    assert prev_close1 == 151.0

    # Test 2: Prev day for AAPL (2024-07-02) only has '5m' data, close should be 151.9
    prev_close2 = db_manager.query_previous_day_close('AAPL', '2024-07-03', table_name)
    print(f"AAPL prev close for 2024-07-03: {prev_close2} (expected 151.9)")
    assert prev_close2 == 151.9

    # Test 3: Prev day for MSFT (2024-07-01) has '1d' (close 201.0) and a later '5m' (close 202.3, but 1d is preferred)
    # query_previous_day_close should prefer '1d' if available.
    # MSFT on 2024-07-02, prev day is 2024-07-01. On 2024-07-01, MSFT has 1d data with close 201.
    prev_close3 = db_manager.query_previous_day_close('MSFT', '2024-07-02', table_name)
    print(f"MSFT prev close for 2024-07-02: {prev_close3} (expected 201.0 from 1d)")
    assert prev_close3 == 201.0

    # Test 4: current_date_str is the first day of data, so no previous day.
    prev_close4 = db_manager.query_previous_day_close('AAPL', '2024-07-01', table_name)
    print(f"AAPL prev close for 2024-07-01: {prev_close4} (expected None)")
    assert prev_close4 is None

    # Test 5: Ticker with no data at all
    prev_close5 = db_manager.query_previous_day_close('GOOG', '2024-07-02', table_name)
    print(f"GOOG prev close for 2024-07-02: {prev_close5} (expected None)")
    assert prev_close5 is None

    print("\n--- DBManager (Daily Market Analyzer v12.0) 測試完畢 ---")
    # os.remove(test_db_path) # Optional: clean up test db
    # print(f"INFO: 已刪除測試資料庫 {test_db_path}")
