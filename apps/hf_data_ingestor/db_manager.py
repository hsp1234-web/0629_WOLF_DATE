# -*- coding: utf-8 -*-
"""
DuckDB 資料庫管理模組。
負責處理所有與 DuckDB 的互動，例如建立資料表、寫入數據等。
"""
import duckdb
import pandas as pd
import os

class DBManager:
    """
    DuckDB 資料庫管理器。

    提供方法來建立資料庫連線、建立資料表以及高效地寫入 (UPSERT) DataFrame 數據。
    """
    def __init__(self, db_path: str):
        """
        初始化 DBManager。

        Args:
            db_path (str): DuckDB 資料庫檔案的路徑。
                           如果資料庫檔案不存在，將會被建立。
                           如果路徑中包含目錄，請確保目錄已存在。
        """
        self.db_path = db_path
        # 確保資料庫檔案所在的目錄存在
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
            print(f"INFO: 已建立資料庫目錄: {db_dir}")
        print(f"INFO: DBManager 初始化完畢，資料庫路徑: {self.db_path}")

    def create_futures_table(self, table_name: str = "futures_ohlcv"):
        """
        建立期貨 OHLCV（開高低收量）數據表，如果該表尚不存在。

        該表結構設計用於儲存來自 yfinance 等來源的時間序列市場數據。
        欄位包括：
        - datetime (TIMESTAMPTZ): 帶時區的時間戳，作為主鍵的一部分。
        - ticker (VARCHAR(32)): 股票/期貨代碼，作為主鍵的一部分。
        - open (DOUBLE PRECISION): 開盤價。
        - high (DOUBLE PRECISION): 最高價。
        - low (DOUBLE PRECISION): 最低價。
        - close (DOUBLE PRECISION): 收盤價。
        - volume (BIGINT): 成交量。

        Args:
            table_name (str): 要建立的資料表名稱。預設為 "futures_ohlcv"。
        """
        # 參考研究文件中的 Schema 設計 [cite: 1250]
        # TIMESTAMPTZ 用於儲存帶時區的時間戳，DuckDB 會將其標準化為 UTC。
        create_sql = f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            datetime TIMESTAMPTZ NOT NULL,
            ticker VARCHAR(32) NOT NULL,
            open DOUBLE PRECISION NOT NULL,
            high DOUBLE PRECISION NOT NULL,
            low DOUBLE PRECISION NOT NULL,
            close DOUBLE PRECISION NOT NULL,
            volume BIGINT,
            PRIMARY KEY (ticker, datetime)
        );
        """
        try:
            with duckdb.connect(self.db_path) as con:
                con.execute(create_sql)
            print(f"INFO: 資料表 '{table_name}' 已在資料庫 '{self.db_path}' 中準備就緒。")
        except Exception as e:
            print(f"錯誤: 建立資料表 '{table_name}' 失敗: {e}")
            raise

    def upsert_data(self, df: pd.DataFrame, table_name: str):
        """
        使用 DuckDB 的 `INSERT OR REPLACE INTO` (類似 UPSERT) 功能高效地將 DataFrame 數據寫入指定資料表。

        此方法假設 DataFrame 的 Index 是 DatetimeIndex，並且 DataFrame 有一個 `name` 屬性
        代表 `ticker` (例如，由 YFinanceClient.fetch_data 返回的 DataFrame)。
        數據寫入前會進行轉換以符合資料表 schema。

        Args:
            df (pd.DataFrame): 包含待寫入數據的 Pandas DataFrame。
                               預期欄位：Open, High, Low, Close, Volume (大小寫不敏感)。
                               Index 應為時間戳。
                               需要有一個 `df.name` 屬性來指定 `ticker`。
            table_name (str): 目標資料表的名稱。
        """
        if not hasattr(df, 'name') or df.name is None:
            print(f"錯誤: DataFrame 缺少 'name' 屬性作為 ticker。無法寫入資料表 '{table_name}'。")
            return

        if df.empty:
            print(f"INFO: 傳入的 DataFrame ({df.name}) 為空，無需寫入資料表 '{table_name}'。")
            return

        # 準備 DataFrame 以符合資料表結構
        df_to_insert = df.copy()
        df_to_insert.index.name = 'datetime' # 將 index 名稱設為 'datetime'
        df_to_insert = df_to_insert.reset_index() # 將 index 轉為欄位

        # 新增 ticker 欄位
        df_to_insert['ticker'] = df.name

        # 標準化欄位名稱 (小寫) 並選擇所需欄位
        df_to_insert.columns = [col.lower() for col in df_to_insert.columns]

        required_cols = ['datetime', 'ticker', 'open', 'high', 'low', 'close', 'volume']
        # 檢查必要欄位是否存在
        missing_cols = [col for col in required_cols if col not in df_to_insert.columns]
        if missing_cols:
            print(f"錯誤: DataFrame ({df.name}) 缺少必要欄位: {', '.join(missing_cols)}。無法寫入資料表 '{table_name}'。")
            return

        df_to_insert = df_to_insert[required_cols]

        # 確保 datetime 是 pandas 的 datetime64[ns, UTC] 類型，DuckDB 可以正確處理
        if not pd.api.types.is_datetime64_any_dtype(df_to_insert['datetime']):
            df_to_insert['datetime'] = pd.to_datetime(df_to_insert['datetime'])
        if df_to_insert['datetime'].dt.tz is None:
            df_to_insert['datetime'] = df_to_insert['datetime'].dt.tz_localize('UTC')
        else:
            df_to_insert['datetime'] = df_to_insert['datetime'].dt.tz_convert('UTC')

        # 確保數值類型正確
        for col in ['open', 'high', 'low', 'close']:
            df_to_insert[col] = pd.to_numeric(df_to_insert[col], errors='raise')
        df_to_insert['volume'] = df_to_insert['volume'].astype('int64')


        try:
            with duckdb.connect(self.db_path) as con:
                # DuckDB 的 Python API 可以直接從 Pandas DataFrame 註冊並插入。
                # 使用 INSERT OR REPLACE (如果 PRIMARY KEY 已存在，則更新)
                # DuckDB 0.7.0+ 版本支援 INSERT OR REPLACE INTO
                # 對於舊版本，可能需要先 DELETE 再 INSERT，或者使用暫存表和 MERGE INTO (更複雜)
                # 這裡假設使用的是支援 INSERT OR REPLACE 的版本

                # 為了安全起見，我們明確指定欄位順序
                # 這也使我們能夠處理 DataFrame 中可能存在的額外欄位
                columns_str = ", ".join(required_cols)

                # 使用 f-string 構建查詢時要非常小心 SQL 注入。
                # 在此情境下，table_name 和 columns_str 是受控的，但仍需謹慎。
                # 更安全的方式是使用 DuckDB 的 `register` 和 `execute` 組合，
                # 但 `INSERT OR REPLACE INTO table SELECT * FROM df_view` 語法更簡潔。

                # 註冊 DataFrame 為一個暫時的 view
                con.register('df_view_to_insert', df_to_insert)

                # 執行 UPSERT 操作
                # 注意：DuckDB 的 INSERT OR REPLACE 是 SQLite 的語法糖。
                # 它實際上是 DELETE matching rows then INSERT new rows.
                # 如果需要更細緻的衝突處理 (例如 ON CONFLICT DO UPDATE SET...)，
                # DuckDB 支援 MERGE INTO 語句 (更標準的 SQL)。
                # 對於這個應用場景，INSERT OR REPLACE 應該足夠。
                upsert_sql = f"INSERT OR REPLACE INTO {table_name} ({columns_str}) SELECT {columns_str} FROM df_view_to_insert"

                con.execute(upsert_sql)

                # 移除暫時的 view (可選，連線關閉時會自動清理)
                con.unregister('df_view_to_insert')

            print(f"INFO: 成功將 {len(df_to_insert)} 筆來自 '{df.name}' 的數據寫入/更新至資料表 '{table_name}'。")
        except Exception as e:
            print(f"錯誤: 寫入數據到資料表 '{table_name}' 失敗 (Ticker: {df.name}): {e}")
            print(f"DEBUG: 嘗試寫入的 DataFrame ({df.name}) info:")
            df_to_insert.info()
            raise

if __name__ == '__main__':
    # 簡易測試代碼
    print("--- DBManager 測試 ---")

    # 測試用的資料庫路徑 (建議放在專案的臨時目錄下)
    test_db_path = "data_workspace/temp/test_market_data.duckdb"
    # 清理舊的測試資料庫檔案
    if os.path.exists(test_db_path):
        os.remove(test_db_path)

    db_manager = DBManager(test_db_path)

    # 測試 1: 建立資料表
    print("\n--- 測試 1: 建立 futures_ohlcv 資料表 ---")
    db_manager.create_futures_table() # 使用預設表名

    # 準備測試數據
    data1 = {
        'Open': [150.0, 151.0, 150.5],
        'High': [152.0, 151.5, 151.0],
        'Low': [149.0, 150.0, 150.0],
        'Close': [151.5, 150.5, 150.8],
        'Volume': [100000, 120000, 110000]
    }
    # 創建一個 UTC 的 DatetimeIndex
    index1 = pd.to_datetime(['2023-01-01 10:00:00', '2023-01-01 10:05:00', '2023-01-01 10:10:00']).tz_localize('UTC')
    df_test1 = pd.DataFrame(data1, index=index1)
    df_test1.name = "TEST_TICKER_A" # 設定 ticker 名稱

    # 測試 2: 插入新數據
    print("\n--- 測試 2: 插入 TEST_TICKER_A 的數據 ---")
    db_manager.upsert_data(df_test1, "futures_ohlcv")

    # 驗證插入
    with duckdb.connect(test_db_path) as con:
        result1 = con.execute("SELECT COUNT(*) FROM futures_ohlcv WHERE ticker = 'TEST_TICKER_A'").fetchone()
        print(f"DBManager 測試: futures_ohlcv 表中 TEST_TICKER_A 的數據筆數: {result1[0] if result1 else '查詢失敗'}")
        assert result1[0] == 3

        result_data = con.execute("SELECT * FROM futures_ohlcv WHERE ticker = 'TEST_TICKER_A' ORDER BY datetime").df()
        print("DBManager 測試: TEST_TICKER_A 的數據內容:")
        print(result_data)


    # 準備更新和新插入的數據
    data2 = {
        'Open': [150.8, 152.0], # 更新 10:10:00 的數據，新增 10:15:00
        'High': [151.2, 152.5],
        'Low': [150.1, 151.8],
        'Close': [151.0, 152.2], # 收盤價變化
        'Volume': [105000, 130000]
    }
    index2 = pd.to_datetime(['2023-01-01 10:10:00', '2023-01-01 10:15:00']).tz_localize('UTC')
    df_test2 = pd.DataFrame(data2, index=index2)
    df_test2.name = "TEST_TICKER_A"

    # 測試 3: Upsert (更新已存在 + 插入新的)
    print("\n--- 測試 3: Upsert TEST_TICKER_A 的數據 (更新+插入) ---")
    db_manager.upsert_data(df_test2, "futures_ohlcv")

    # 驗證 Upsert
    with duckdb.connect(test_db_path) as con:
        result2 = con.execute("SELECT COUNT(*) FROM futures_ohlcv WHERE ticker = 'TEST_TICKER_A'").fetchone()
        print(f"DBManager 測試: Upsert 後 TEST_TICKER_A 的數據筆數: {result2[0] if result2 else '查詢失敗'}")
        assert result2[0] == 4 # 3 原始 + 1 新 - 1 更新 = 3 筆 (因為 10:10 被取代) -> 應該是 3 (原始) - 1 (被取代) + 2 (新) = 4

        # 檢查更新的數據
        updated_row = con.execute("SELECT close FROM futures_ohlcv WHERE ticker = 'TEST_TICKER_A' AND datetime = '2023-01-01 10:10:00+00'").fetchone()
        print(f"DBManager 測試: 10:10:00 更新後的收盤價: {updated_row[0] if updated_row else '查詢失敗'}")
        assert updated_row[0] == 151.0 # 驗證收盤價已更新

        result_data_after_upsert = con.execute("SELECT * FROM futures_ohlcv WHERE ticker = 'TEST_TICKER_A' ORDER BY datetime").df()
        print("DBManager 測試: Upsert 後 TEST_TICKER_A 的數據內容:")
        print(result_data_after_upsert)

    # 測試 4: 插入不同 ticker 的數據
    data3 = {
        'Open': [200.0], 'High': [201.0], 'Low': [199.0], 'Close': [200.5], 'Volume': [50000]
    }
    index3 = pd.to_datetime(['2023-01-01 09:00:00']).tz_localize('UTC')
    df_test3 = pd.DataFrame(data3, index=index3)
    df_test3.name = "TEST_TICKER_B"
    print("\n--- 測試 4: 插入 TEST_TICKER_B 的數據 ---")
    db_manager.upsert_data(df_test3, "futures_ohlcv")

    with duckdb.connect(test_db_path) as con:
        result3 = con.execute("SELECT COUNT(*) FROM futures_ohlcv WHERE ticker = 'TEST_TICKER_B'").fetchone()
        print(f"DBManager 測試: TEST_TICKER_B 的數據筆數: {result3[0] if result3 else '查詢失敗'}")
        assert result3[0] == 1

        total_count = con.execute("SELECT COUNT(*) FROM futures_ohlcv").fetchone()
        print(f"DBManager 測試: 資料表中總筆數: {total_count[0] if total_count else '查詢失敗'}")
        assert total_count[0] == 5 # 4 (A) + 1 (B)

    # 測試 5: 插入空 DataFrame
    print("\n--- 測試 5: 插入空 DataFrame ---")
    df_empty = pd.DataFrame()
    df_empty.name = "EMPTY_TICKER"
    db_manager.upsert_data(df_empty, "futures_ohlcv")
    # 應僅打印訊息，不拋出錯誤，資料庫數據不變

    # 測試 6: DataFrame 缺少 name 屬性
    print("\n--- 測試 6: DataFrame 缺少 name 屬性 ---")
    df_no_name = pd.DataFrame(data1, index=index1)
    # del df_no_name.name # Pandas DataFrame 沒有直接的 del .name
    # 為了測試，我們傳一個沒有 .name 的物件
    try:
        class DummyDF:
            def __init__(self, df):
                self.df = df
                self.empty = df.empty
                self.index = df.index
                self.columns = df.columns
                # 故意不設定 name
            def copy(self): return self.df.copy()

        db_manager.upsert_data(DummyDF(df_test1), "futures_ohlcv")
    except AttributeError as e: # 預期不會拋出 AttributeError，而是打印錯誤訊息並返回
        print(f"DBManager 測試: 捕獲到預期的行為 (缺少 name 屬性時): {e}")

    # 測試 7: 建立一個不同名稱的表
    print("\n--- 測試 7: 建立並操作不同名稱的資料表 other_market_data ---")
    db_manager.create_futures_table(table_name="other_market_data")
    db_manager.upsert_data(df_test1, "other_market_data")
    with duckdb.connect(test_db_path) as con:
        result_other = con.execute("SELECT COUNT(*) FROM other_market_data WHERE ticker = 'TEST_TICKER_A'").fetchone()
        print(f"DBManager 測試: other_market_data 表中 TEST_TICKER_A 的數據筆數: {result_other[0] if result_other else '查詢失敗'}")
        assert result_other[0] == 3

    print("\n--- DBManager 測試完畢 ---")
    # 可以在此處保留測試資料庫檔案供檢查，或刪除它
    # os.remove(test_db_path)
    # print(f"INFO: 已刪除測試資料庫 {test_db_path}")
