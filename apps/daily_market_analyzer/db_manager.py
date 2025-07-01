# -*- coding: utf-8 -*-
"""
DuckDB 資料庫管理模組 for Daily Market Analyzer。
負責處理所有與 DuckDB 的互動，例如建立資料表、寫入數據等。
"""
import duckdb
import pandas as pd
import os
from datetime import datetime, timedelta

class DBManager:
    """
    DuckDB 資料庫管理器。

    提供方法來建立資料庫連線、建立資料表以及高效地寫入 (UPSERT) DataFrame 數據。
    此版本適用於 Daily Market Analyzer，處理包含 'interval' 欄位的數據，並提供查詢功能。
    """
    def __init__(self, db_path: str):
        """
        初始化 DBManager。

        Args:
            db_path (str): DuckDB 資料庫檔案的路徑。
        """
        self.db_path = db_path
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
            print(f"INFO: 已建立資料庫目錄: {db_dir}")
        print(f"INFO: DBManager (Daily Market Analyzer) 初始化完畢，資料庫路徑: {self.db_path}")

    def create_ohlcv_table(self, table_name: str = "market_ohlcv_analyzer"):
        """
        建立市場 OHLCV（開高低收量）數據表，如果該表尚不存在。
        包含 'interval' 和 'ticker' 欄位。
        主鍵為 (ticker, datetime, interval) 以確保唯一性。
        """
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
            print(f"INFO: 資料表 '{table_name}' 已在資料庫 '{self.db_path}' 中準備就緒 (包含 interval 欄位)。")
        except Exception as e:
            print(f"錯誤: 建立資料表 '{table_name}' 失敗: {e}")
            raise

    def upsert_data(self, df: pd.DataFrame, table_name: str):
        """
        使用 DuckDB 的 `INSERT OR REPLACE INTO` 功能高效地將 DataFrame 數據寫入指定資料表。
        此版本預期 DataFrame 已包含 'ticker' 和 'interval' 欄位。
        """
        if df.empty:
            # 嘗試從 DataFrame 中獲取 ticker 和 interval 信息，如果失敗則使用預設值
            current_ticker = "未知Ticker"
            if 'ticker' in df.columns and not df.empty:
                current_ticker = df['ticker'].iloc[0]
            elif hasattr(df, 'name') and df.name: # 向下相容舊的 df.name 方式
                 current_ticker = df.name

            print(f"INFO: 傳入的 DataFrame ({current_ticker}) 為空，無需寫入資料表 '{table_name}'。")
            return

        df_to_insert = df.copy()

        if isinstance(df_to_insert.index, pd.DatetimeIndex):
            df_to_insert = df_to_insert.reset_index()

        df_to_insert.columns = [col.lower() for col in df_to_insert.columns] # 確保列名小寫

        # 優先處理 'index' 列（如果它是 datetime 的來源）
        if 'index' in df_to_insert.columns and 'datetime' not in df_to_insert.columns:
            # 檢查 'index' 列是否是日期類型，如果是，則重命名為 'datetime'
            # 這裡假設如果 'index' 是日期時間，它應該被用作 'datetime'
            # 更嚴格的檢查可以判斷 pd.api.types.is_datetime64_any_dtype(df_to_insert['index'])
            df_to_insert.rename(columns={'index': 'datetime'}, inplace=True)
            print(f"資訊 (DBManager): 將來自索引的 'index' 欄位重命名為 'datetime'。")


        # 如果 'datetime' 仍然不存在，但 'date' 存在，則重命名並發出警告
        if 'datetime' not in df_to_insert.columns and 'date' in df_to_insert.columns:
            print(f"警告 (DBManager): DataFrame 中缺少 'datetime' 欄位，但找到了 'date' 欄位。將自動重命名 'date' 為 'datetime'。建議上游模組應直接提供 'datetime' 欄位。")
            df_to_insert.rename(columns={'date': 'datetime'}, inplace=True)

        # 確保 ticker 和 interval 欄位存在 (可能來自 df.name 或已是欄位)
        # 這部分邏輯可以保留，以處理不同來源的 DataFrame
        if 'ticker' not in df_to_insert.columns and hasattr(df, 'name') and df.name:
             df_to_insert['ticker'] = df.name
        # interval 應由 YFinanceClient 添加

        required_cols = ['datetime', 'ticker', 'interval', 'open', 'high', 'low', 'close', 'volume']

        missing_cols = [col for col in required_cols if col not in df_to_insert.columns]
        if missing_cols:
            current_ticker_for_error = df_to_insert['ticker'].iloc[0] if 'ticker' in df_to_insert.columns and not df_to_insert.empty else "未知 Ticker"
            print(f"錯誤: DataFrame ({current_ticker_for_error}) 缺少必要欄位: {', '.join(missing_cols)}。無法寫入資料表 '{table_name}'。")
            df_to_insert.info()
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
            current_ticker_for_error = df_to_insert['ticker'].iloc[0] if 'ticker' in df_to_insert.columns and not df_to_insert.empty else "未知 Ticker"
            print(f"錯誤: DataFrame ({current_ticker_for_error}) 數據類型轉換失敗: {e}")
            df_to_insert.info()
            return

        try:
            with duckdb.connect(self.db_path) as con:
                con.register('df_view_to_insert', df_to_insert)
                columns_str = ", ".join(required_cols)
                upsert_sql = f"INSERT OR REPLACE INTO {table_name} ({columns_str}) SELECT {columns_str} FROM df_view_to_insert"
                con.execute(upsert_sql)
                con.unregister('df_view_to_insert')

            current_ticker = df_to_insert['ticker'].iloc[0]
            current_interval = df_to_insert['interval'].iloc[0]
            print(f"INFO: 成功將 {len(df_to_insert)} 筆來自 '{current_ticker}' (顆粒度: {current_interval}) 的數據寫入/更新至資料表 '{table_name}'。")
        except Exception as e:
            current_ticker_for_error = df_to_insert['ticker'].iloc[0] if 'ticker' in df_to_insert.columns and not df_to_insert.empty else "未知 Ticker"
            print(f"錯誤: 寫入數據到資料表 '{table_name}' 失敗 (Ticker: {current_ticker_for_error}): {e}")
            print(f"DEBUG: 嘗試寫入的 DataFrame ({current_ticker_for_error}) info:")
            df_to_insert.info()

    def query_data_for_day(self, ticker: str, date_str: str, table_name: str = "market_ohlcv_analyzer") -> pd.DataFrame:
        """
        查詢指定 ticker 在特定日期的所有 OHLCV 數據。
        """
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
                result_df['datetime'] = pd.to_datetime(result_df['datetime']) # 確保是 datetime 物件
                if result_df['datetime'].dt.tz is None: # fetchdf 可能返回 naive datetime
                    result_df['datetime'] = result_df['datetime'].dt.tz_localize('UTC')
                else:
                    result_df['datetime'] = result_df['datetime'].dt.tz_convert('UTC')
                result_df = result_df.set_index('datetime')
            return result_df
        except Exception as e:
            print(f"錯誤: 查詢 {ticker} 在 {date_str} 的數據失敗: {e}")
            return pd.DataFrame()

    def query_previous_day_close(self, ticker: str, current_date_str: str, table_name: str = "market_ohlcv_analyzer", max_lookback_days: int = 30) -> float | None:
        """
        查詢指定 ticker 在 current_date_str 前一個「有數據的」交易日的最後一筆收盤價。
        """
        try:
            current_date_obj = datetime.strptime(current_date_str, "%Y-%m-%d").date()

            for i in range(1, max_lookback_days + 1):
                prev_date_to_check = current_date_obj - timedelta(days=i)
                prev_date_to_check_str = prev_date_to_check.strftime("%Y-%m-%d")

                # print(f"DEBUG: query_previous_day_close: Checking {prev_date_to_check_str} for {ticker}")
                daily_data_df = self.query_data_for_day(ticker, prev_date_to_check_str, table_name)

                if not daily_data_df.empty:
                    # 假設我們想要的是日線 (1d) 的收盤價作為前一天的收盤價
                    # 如果有多種 interval，優先選擇 '1d'
                    daily_1d_data = daily_data_df[daily_data_df['interval'] == '1d']
                    if not daily_1d_data.empty:
                        return daily_1d_data['close'].iloc[-1]
                    else: # 如果沒有 '1d'數據，則取當天所有數據的最後一筆（可能是更高頻的數據）
                        return daily_data_df['close'].iloc[-1]

            # print(f"INFO: 在過去 {max_lookback_days} 天內未找到 {ticker} 在 {current_date_str} 之前的收盤數據。")
            return None
        except Exception as e:
            print(f"錯誤: 查詢 {ticker} 在 {current_date_str} 之前的收盤價失敗: {e}")
            return None

if __name__ == '__main__':
    print("--- DBManager (Daily Market Analyzer) 測試 ---")
    test_db_path = "data_workspace/temp/test_analyzer_market_data.duckdb"
    if os.path.exists(test_db_path):
        os.remove(test_db_path)

    db_manager = DBManager(test_db_path)
    table_name = "market_ohlcv_analyzer_test"

    print(f"\n--- 測試 1: 建立 {table_name} 資料表 ---")
    db_manager.create_ohlcv_table(table_name=table_name)

    # 測試 upsert_data (與之前類似，確保能正常運作)
    data1 = {
        'datetime': pd.to_datetime(['2023-01-01 10:00:00', '2023-01-01 10:05:00']).tz_localize('UTC'),
        'ticker': ['TEST_MAIN', 'TEST_MAIN'], 'interval': ['1m', '1m'],
        'open': [100, 101], 'high': [102, 101.5], 'low': [99, 100.5], 'close': [101, 101.2], 'volume': [1000, 1200]
    }
    df_test_main = pd.DataFrame(data1).set_index('datetime')
    db_manager.upsert_data(df_test_main, table_name)
    with duckdb.connect(test_db_path) as con:
        assert con.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0] == 2

    print("\n--- 測試輔助查詢方法 ---")
    multi_day_data = []
    base_date_dt = datetime.strptime("2024-07-20", "%Y-%m-%d")

    for i in range(5): # 5 天的數據
        day_dt = base_date_dt + timedelta(days=i)
        # 日線數據 (每天一筆)
        multi_day_data.append({
            'datetime': day_dt.replace(hour=16, minute=0, second=0, microsecond=0).astimezone(timedelta(hours=0)), # 標準化到 UTC 16:00
            'ticker': 'AAPL', 'interval': '1d',
            'open': 150.0+i, 'high': 152.5+i, 'low': 149.5+i, 'close': 151.0+i, 'volume': 1000000+i*1000
        })
        multi_day_data.append({
            'datetime': day_dt.replace(hour=16, minute=0, second=0, microsecond=0).astimezone(timedelta(hours=0)),
            'ticker': 'MSFT', 'interval': '1d',
            'open': 200.0+i, 'high': 202.5+i, 'low': 199.5+i, 'close': 201.0+i, 'volume': 800000+i*1000
        })

    # 特定一天的分鐘線數據 (AAPL, 2024-07-22)
    day_for_minute_data_dt = base_date_dt + timedelta(days=2) # This is 2024-07-22
    for min_offset in range(0, 60, 15): # 09:00, 09:15, 09:30, 09:45 (假設市場開盤時間)
        ts = day_for_minute_data_dt.replace(hour=9, minute=min_offset, second=0, microsecond=0).astimezone(timedelta(hours=0))
        multi_day_data.append({
            'datetime': ts, 'ticker': 'AAPL', 'interval': '15m',
            'open': 152.0 + min_offset*0.01, 'high': 152.5 + min_offset*0.01,
            'low': 151.5 + min_offset*0.01, 'close': 152.2 + min_offset*0.01, 'volume': 5000+min_offset*10
        })

    df_multi_day_all = pd.DataFrame(multi_day_data)
    df_multi_day_all['datetime'] = pd.to_datetime(df_multi_day_all['datetime'])
    # 確保所有 datetime 都是 UTC aware before set_index
    if df_multi_day_all['datetime'].dt.tz is None:
        df_multi_day_all['datetime'] = df_multi_day_all['datetime'].dt.tz_localize('UTC')
    else:
        df_multi_day_all['datetime'] = df_multi_day_all['datetime'].dt.tz_convert('UTC')
    df_multi_day_all = df_multi_day_all.set_index('datetime')

    db_manager.upsert_data(df_multi_day_all, table_name)

    print("\n--- 測試 query_data_for_day ---")
    aapl_2024_07_22_data = db_manager.query_data_for_day(ticker="AAPL", date_str="2024-07-22", table_name=table_name)
    print(f"AAPL 2024-07-22 data (預期 1筆 '1d' + 4筆 '15m'):\n{aapl_2024_07_22_data}")
    assert len(aapl_2024_07_22_data) == 5
    assert '1d' in aapl_2024_07_22_data['interval'].unique()
    assert '15m' in aapl_2024_07_22_data['interval'].unique()

    msft_2024_07_22_data = db_manager.query_data_for_day(ticker="MSFT", date_str="2024-07-22", table_name=table_name)
    print(f"\nMSFT 2024-07-22 data (預期 1筆 '1d'):\n{msft_2024_07_22_data}")
    assert len(msft_2024_07_22_data) == 1
    assert msft_2024_07_22_data['interval'].iloc[0] == '1d'

    print("\n--- 測試 query_previous_day_close ---")
    # AAPL: 2024-07-22 (i=2) close = 151.0+2 = 153.0
    # MSFT: 2024-07-22 (i=2) close = 201.0+2 = 203.0

    # 查詢 AAPL 2024-07-23 的前一日收盤價 (應為 2024-07-22 的日線收盤價)
    prev_close_aapl = db_manager.query_previous_day_close(ticker="AAPL", current_date_str="2024-07-23", table_name=table_name)
    print(f"AAPL 前一日 (相對於 2024-07-23) 收盤價: {prev_close_aapl}")
    assert prev_close_aapl == 151.0 + 2 # 153.0 (AAPL '1d' close on 2024-07-22)

    # 查詢 MSFT 2024-07-21 (週日) 的前一日收盤價 (應為 2024-07-20 的日線收盤價)
    # 2024-07-20 (i=0) MSFT '1d' close = 201.0
    prev_close_msft_weekend = db_manager.query_previous_day_close(ticker="MSFT", current_date_str="2024-07-21", table_name=table_name)
    print(f"MSFT 前一日 (相對於 2024-07-21) 收盤價: {prev_close_msft_weekend}")
    assert prev_close_msft_weekend == 201.0

    # 查詢一個日期，其前幾天都沒有數據
    prev_close_way_back = db_manager.query_previous_day_close(ticker="AAPL", current_date_str="2024-07-19", table_name=table_name) # 數據從 07-20 開始
    print(f"AAPL 前一日 (相對於 2024-07-19，預期為 None): {prev_close_way_back}")
    assert prev_close_way_back is None

    print("\n--- DBManager 輔助查詢測試完畢 ---")
    # os.remove(test_db_path)
    # print(f"INFO: 已刪除測試資料庫 {test_db_path}")
