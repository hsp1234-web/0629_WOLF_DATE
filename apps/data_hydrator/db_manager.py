# -*- coding: utf-8 -*-
"""
DuckDB 資料庫管理模組 for Data Hydrator。
負責處理所有與 DuckDB 的互動，例如建立資料表、寫入數據等。
"""
import duckdb
import pandas as pd
import os

class DBManager:
    """
    DuckDB 資料庫管理器。

    提供方法來建立資料庫連線、建立資料表以及高效地寫入 (UPSERT) DataFrame 數據。
    此版本適用於 Data Hydrator，處理包含 'interval' 欄位的數據。
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
        print(f"INFO: DBManager (Data Hydrator) 初始化完畢，資料庫路徑: {self.db_path}")

    def create_ohlcv_table(self, table_name: str = "market_ohlcv"):
        """
        建立市場 OHLCV（開高低收量）數據表，如果該表尚不存在。
        包含 'interval' 和 'ticker' 欄位。

        欄位包括：
        - datetime (TIMESTAMPTZ): 帶時區的時間戳。
        - ticker (VARCHAR): 股票/期貨代碼。
        - interval (VARCHAR): 數據的時間顆粒度 (e.g., '1m', '1d')。
        - open (DOUBLE PRECISION): 開盤價。
        - high (DOUBLE PRECISION): 最高價。
        - low (DOUBLE PRECISION): 最低價。
        - close (DOUBLE PRECISION): 收盤價。
        - volume (BIGINT): 成交量。
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

        Args:
            df (pd.DataFrame): 包含待寫入數據的 Pandas DataFrame。
                               預期欄位：datetime (index), open, high, low, close, volume,
                                         ticker, interval (後兩者為普通欄位)。
            table_name (str): 目標資料表的名稱。
        """
        if df.empty:
            current_ticker = df['ticker'].iloc[0] if 'ticker' in df.columns and not df.empty else "未知 Ticker"
            print(f"INFO: 傳入的 DataFrame ({current_ticker}) 為空，無需寫入資料表 '{table_name}'。")
            return

        df_to_insert = df.copy()

        # 將 index (datetime) 轉為欄位
        if isinstance(df_to_insert.index, pd.DatetimeIndex):
            df_to_insert = df_to_insert.reset_index()

        # 標準化欄位名稱 (小寫)
        df_to_insert.columns = [col.lower() for col in df_to_insert.columns]

        # 檢查 'datetime' 欄位是否在 reset_index() 後生成，如果原本 index 沒有名字
        if 'index' in df_to_insert.columns and 'datetime' not in df_to_insert.columns:
            df_to_insert.rename(columns={'index': 'datetime'}, inplace=True)

        required_cols = ['datetime', 'ticker', 'interval', 'open', 'high', 'low', 'close', 'volume']

        missing_cols = [col for col in required_cols if col not in df_to_insert.columns]
        if missing_cols:
            current_ticker = df_to_insert['ticker'].iloc[0] if 'ticker' in df_to_insert.columns and not df_to_insert.empty else "未知 Ticker"
            print(f"錯誤: DataFrame ({current_ticker}) 缺少必要欄位: {', '.join(missing_cols)}。無法寫入資料表 '{table_name}'。")
            df_to_insert.info() # 打印更多信息幫助調試
            return

        df_to_insert = df_to_insert[required_cols]

        # 數據類型轉換與驗證
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
            current_ticker = df_to_insert['ticker'].iloc[0] if 'ticker' in df_to_insert.columns and not df_to_insert.empty else "未知 Ticker"
            print(f"錯誤: DataFrame ({current_ticker}) 數據類型轉換失敗: {e}")
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
            current_ticker = df_to_insert['ticker'].iloc[0] if 'ticker' in df_to_insert.columns and not df_to_insert.empty else "未知 Ticker"
            print(f"錯誤: 寫入數據到資料表 '{table_name}' 失敗 (Ticker: {current_ticker}): {e}")
            print(f"DEBUG: 嘗試寫入的 DataFrame ({current_ticker}) info:")
            df_to_insert.info()
            # raise # 在生產中可能不想 raise，而是記錄錯誤並繼續

if __name__ == '__main__':
    print("--- DBManager (Data Hydrator) 測試 ---")
    test_db_path = "data_workspace/temp/test_hydrator_market_data.duckdb"
    if os.path.exists(test_db_path):
        os.remove(test_db_path)

    db_manager = DBManager(test_db_path)
    table_name = "market_ohlcv_test" # 使用測試表名

    print(f"\n--- 測試 1: 建立 {table_name} 資料表 ---")
    db_manager.create_ohlcv_table(table_name=table_name)

    # 準備測試數據 (已包含 ticker 和 interval)
    data1 = {
        'datetime': pd.to_datetime(['2023-01-01 10:00:00', '2023-01-01 10:05:00', '2023-01-01 10:10:00']).tz_localize('UTC'),
        'ticker': ['TEST_A', 'TEST_A', 'TEST_A'],
        'interval': ['1m', '1m', '1m'],
        'open': [150.0, 151.0, 150.5],
        'high': [152.0, 151.5, 151.0],
        'low': [149.0, 150.0, 150.0],
        'close': [151.5, 150.5, 150.8],
        'volume': [100000, 120000, 110000]
    }
    df_test1 = pd.DataFrame(data1).set_index('datetime') # datetime 設為 index 以模擬 YFinanceClient 返回的格式

    print("\n--- 測試 2: 插入 TEST_A (1m) 的數據 ---")
    db_manager.upsert_data(df_test1, table_name)

    with duckdb.connect(test_db_path) as con:
        result1 = con.execute(f"SELECT COUNT(*) FROM {table_name} WHERE ticker = 'TEST_A' AND interval = '1m'").fetchone()
        print(f"DBManager 測試: {table_name} 表中 TEST_A (1m) 的數據筆數: {result1[0] if result1 else '查詢失敗'}")
        assert result1[0] == 3
        result_data = con.execute(f"SELECT * FROM {table_name} WHERE ticker = 'TEST_A' ORDER BY datetime").df()
        print("DBManager 測試: TEST_A (1m) 的數據內容:")
        print(result_data)

    # 準備更新和新插入的數據 (TEST_A, 1m)
    data2 = {
        'datetime': pd.to_datetime(['2023-01-01 10:10:00', '2023-01-01 10:15:00']).tz_localize('UTC'), # 更新 10:10, 新增 10:15
        'ticker': ['TEST_A', 'TEST_A'],
        'interval': ['1m', '1m'],
        'open': [150.8, 152.0],
        'high': [151.2, 152.5],
        'low': [150.1, 151.8],
        'close': [151.0, 152.2], # 10:10 close 變化
        'volume': [105000, 130000]
    }
    df_test2 = pd.DataFrame(data2).set_index('datetime')

    print("\n--- 測試 3: Upsert TEST_A (1m) 的數據 (更新+插入) ---")
    db_manager.upsert_data(df_test2, table_name)

    with duckdb.connect(test_db_path) as con:
        result2 = con.execute(f"SELECT COUNT(*) FROM {table_name} WHERE ticker = 'TEST_A' AND interval = '1m'").fetchone()
        print(f"DBManager 測試: Upsert 後 TEST_A (1m) 的數據筆數: {result2[0] if result2 else '查詢失敗'}")
        assert result2[0] == 4 # 3 (原始) - 1 (被取代) + 2 (新) = 4
        updated_row = con.execute(f"SELECT close FROM {table_name} WHERE ticker = 'TEST_A' AND interval = '1m' AND datetime = '2023-01-01 10:10:00+00'").fetchone()
        print(f"DBManager 測試: 10:10:00 更新後的收盤價: {updated_row[0] if updated_row else '查詢失敗'}")
        assert updated_row[0] == 151.0

    # 準備不同 interval 的數據 (TEST_A, 5m)
    data3 = {
        'datetime': pd.to_datetime(['2023-01-01 10:00:00', '2023-01-01 10:05:00']).tz_localize('UTC'),
        'ticker': ['TEST_A', 'TEST_A'],
        'interval': ['5m', '5m'], # 不同 interval
        'open': [149.0, 150.0],
        'high': [151.0, 150.5],
        'low': [148.0, 149.5],
        'close': [150.5, 150.0],
        'volume': [200000, 220000]
    }
    df_test3 = pd.DataFrame(data3).set_index('datetime')
    print("\n--- 測試 4: 插入 TEST_A (5m) 的數據 ---")
    db_manager.upsert_data(df_test3, table_name)

    with duckdb.connect(test_db_path) as con:
        result3 = con.execute(f"SELECT COUNT(*) FROM {table_name} WHERE ticker = 'TEST_A' AND interval = '5m'").fetchone()
        print(f"DBManager 測試: {table_name} 表中 TEST_A (5m) 的數據筆數: {result3[0] if result3 else '查詢失敗'}")
        assert result3[0] == 2
        total_count = con.execute(f"SELECT COUNT(*) FROM {table_name} WHERE ticker = 'TEST_A'").fetchone()
        print(f"DBManager 測試: {table_name} 表中 TEST_A 的總數據筆數: {total_count[0] if total_count else '查詢失敗'}")
        assert total_count[0] == 6 # 4 (1m) + 2 (5m)

    print("\n--- DBManager (Data Hydrator) 測試完畢 ---")
    # os.remove(test_db_path)
    # print(f"INFO: 已刪除測試資料庫 {test_db_path}")
