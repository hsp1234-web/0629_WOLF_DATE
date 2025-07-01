# -*- coding: utf-8 -*-
"""
DuckDB 資料庫管理模組 (專用於 file_processor)。
負責處理所有與 DuckDB 的互動，例如建立資料表、寫入數據等。
雖然與 hf_data_ingestor 中的 db_manager 相似，但為了應用程式的獨立性而分開維護。
未來若功能完全重疊且穩定，可考慮抽象化或共用。
"""
import duckdb
import pandas as pd
import os

class DBManager:
    """
    DuckDB 資料庫管理器 (file_processor 版本)。

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
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
            print(f"INFO (FileProcessorDB): 已建立資料庫目錄: {db_dir}")
        print(f"INFO (FileProcessorDB): DBManager 初始化完畢，資料庫路徑: {self.db_path}")

    def execute_query(self, query: str, params=None):
        """
        執行任意 SQL 查詢。

        Args:
            query (str): 要執行的 SQL 查詢語句。
            params (list | tuple, optional): 查詢參數。 Defaults to None.

        Returns:
            list | None: 若是 SELECT 查詢，返回結果列表；否則返回 None。
        """
        try:
            with duckdb.connect(self.db_path) as con:
                if params:
                    return con.execute(query, params).fetchall()
                return con.execute(query).fetchall()
        except Exception as e:
            print(f"錯誤 (FileProcessorDB): 執行查詢 '{query[:100]}...' 失敗: {e}")
            raise

    def create_table_from_schema(self, table_name: str, schema: dict, primary_keys: list[str] = None):
        """
        根據提供的 schema 動態建立資料表 (如果不存在)。

        Args:
            table_name (str): 要建立的資料表名稱。
            schema (dict): 一個字典，鍵是欄位名，值是 DuckDB 的資料類型 (例如 "VARCHAR", "DOUBLE PRECISION", "TIMESTAMPTZ")。
            primary_keys (list[str], optional): 主鍵欄位列表。如果提供，則會設定複合主鍵。
        """
        if not schema:
            print(f"錯誤 (FileProcessorDB): 無法為 '{table_name}' 建立資料表，因 schema 為空。")
            return

        columns_definitions = []
        for col_name, col_type in schema.items():
            columns_definitions.append(f"{col_name} {col_type}")

        create_sql = f"CREATE TABLE IF NOT EXISTS {table_name} ({', '.join(columns_definitions)}"

        if primary_keys and isinstance(primary_keys, list) and len(primary_keys) > 0:
            pk_constraint = f", PRIMARY KEY ({', '.join(primary_keys)})"
            create_sql += pk_constraint

        create_sql += ");"

        try:
            with duckdb.connect(self.db_path) as con:
                con.execute(create_sql)
            print(f"INFO (FileProcessorDB): 資料表 '{table_name}' 已在資料庫 '{self.db_path}' 中準備就緒。Schema: {schema}, PK: {primary_keys}")
        except Exception as e:
            print(f"錯誤 (FileProcessorDB): 建立資料表 '{table_name}' 失敗: {e}\nSQL: {create_sql}")
            raise

    def upsert_data(self, df: pd.DataFrame, table_name: str, pk_columns: list[str] = None):
        """
        使用 DuckDB 的 `INSERT OR REPLACE INTO` (類似 UPSERT) 功能高效地將 DataFrame 數據寫入指定資料表。
        如果提供了 pk_columns，則會基於這些主鍵進行取代。若未提供，則資料表需要有預設的主鍵設定。

        Args:
            df (pd.DataFrame): 包含待寫入數據的 Pandas DataFrame。
                               欄位名稱應與資料表 schema 匹配 (大小寫敏感，取決於 DuckDB 設定，但建議一致)。
            table_name (str): 目標資料表的名稱。
            pk_columns (list[str], optional): 用於 UPSERT 操作的主鍵欄位列表。
                                              如果資料表本身已定義主鍵，此參數可以省略或應與之匹配。
                                              如果提供，`INSERT OR REPLACE` 會依賴這些欄位判斷衝突。
                                              注意：DuckDB 的 `INSERT OR REPLACE` 作用於資料表定義的 PRIMARY KEY。
                                              如果 DataFrame 的欄位和順序與資料表完全一致，可以直接用 `SELECT *`。
                                              否則，需要明確指定欄位。

        Returns:
            bool: 操作是否成功。
        """
        if df.empty:
            print(f"INFO (FileProcessorDB): 傳入的 DataFrame 為空，無需寫入資料表 '{table_name}'。")
            return True # 視為成功，因為沒有數據可寫入

        # 確保 DataFrame 中的欄位名稱與資料庫表格的欄位名稱一致 (通常建議小寫化處理)
        # 這裡假設解析器已經處理好欄位名稱和類型

        # 獲取 DataFrame 的欄位列表作為插入時的欄位順序
        df_columns = list(df.columns)
        columns_str = ", ".join(f'"{c}"' for c in df_columns) # DuckDB 對大小寫敏感的欄位名需要引號

        try:
            with duckdb.connect(self.db_path) as con:
                # 註冊 DataFrame 為一個暫時的 view
                # 使用 unique view name to avoid collision in concurrent uses (if any)
                view_name = f"df_view_to_insert_{os.urandom(4).hex()}"
                con.register(view_name, df)

                # 構建 UPSERT SQL
                # INSERT OR REPLACE 依賴於資料表定義的 PRIMARY KEY。
                # 如果 pk_columns 提供，它應該與資料表的主鍵定義相符。
                # 這裡我們假設資料表已正確建立，並且其主鍵能被 INSERT OR REPLACE 利用。
                # 如果資料表沒有主鍵，INSERT OR REPLACE 的行為類似於 INSERT。

                # 為了確保插入的欄位與資料表欄位對應，我們明確列出欄位
                upsert_sql = f"INSERT OR REPLACE INTO \"{table_name}\" ({columns_str}) SELECT {columns_str} FROM {view_name}"

                con.execute(upsert_sql)
                con.unregister(view_name)

            print(f"INFO (FileProcessorDB): 成功將 {len(df)} 筆數據寫入/更新至資料表 '{table_name}'。")
            return True
        except Exception as e:
            print(f"錯誤 (FileProcessorDB): 寫入數據到資料表 '{table_name}' 失敗: {e}")
            print(f"DEBUG (FileProcessorDB): 嘗試寫入的 DataFrame 前5行:\n{df.head()}")
            df.info()
            # raise # 根據需求決定是否要重新拋出異常
            return False

if __name__ == '__main__':
    # 簡易測試代碼
    print("--- FileProcessor DBManager 測試 ---")

    test_db_file_path = "data_workspace/temp/test_file_processor_db.duckdb"
    if os.path.exists(test_db_file_path):
        os.remove(test_db_file_path)
    if os.path.exists(test_db_file_path + ".wal"):
        os.remove(test_db_file_path + ".wal")

    db_manager = DBManager(test_db_file_path)

    # 測試 1: 建立資料表 (例如，一個簡化的選擇權資料表)
    print("\n--- 測試 1: 建立 options_daily 資料表 ---")
    options_schema = {
        "TradeDate": "DATE",
        "ContractMonth": "VARCHAR",
        "StrikePrice": "DOUBLE PRECISION",
        "OptionType": "VARCHAR", # Call 或 Put
        "Open": "DOUBLE PRECISION",
        "High": "DOUBLE PRECISION",
        "Low": "DOUBLE PRECISION",
        "Close": "DOUBLE PRECISION",
        "Volume": "BIGINT"
    }
    options_pk = ["TradeDate", "ContractMonth", "StrikePrice", "OptionType"]
    db_manager.create_table_from_schema("options_daily", options_schema, options_pk)

    # 準備測試數據
    data1 = {
        "TradeDate": pd.to_datetime(['2023-10-01', '2023-10-01', '2023-10-01']),
        "ContractMonth": ['202312', '202312', '202401'],
        "StrikePrice": [17000.0, 17000.0, 17500.0],
        "OptionType": ['Call', 'Put', 'Call'],
        "Open": [150.0, 50.0, 120.0],
        "High": [160.0, 55.0, 130.0],
        "Low": [140.0, 45.0, 110.0],
        "Close": [155.0, 48.0, 125.0],
        "Volume": [1000, 500, 700]
    }
    df_options1 = pd.DataFrame(data1)
    # DuckDB 的 DATE 類型可以直接接受 pandas 的 datetime64 物件

    # 測試 2: 插入新數據
    print("\n--- 測試 2: 插入選擇權數據 ---")
    success1 = db_manager.upsert_data(df_options1, "options_daily", pk_columns=options_pk)
    assert success1

    # 驗證插入
    try:
        with duckdb.connect(test_db_file_path, read_only=True) as con:
            result1_count = con.execute("SELECT COUNT(*) FROM options_daily").fetchone()
            print(f"DBManager 測試: options_daily 表中數據筆數: {result1_count[0] if result1_count else '查詢失敗'}")
            assert result1_count[0] == 3

            result1_data = con.execute("SELECT * FROM options_daily ORDER BY \"TradeDate\", \"ContractMonth\", \"StrikePrice\", \"OptionType\"").df()
            print("DBManager 測試: options_daily 的數據內容:")
            print(result1_data)
    except Exception as e:
        print(f"DBManager 測試: 驗證插入時出錯: {e}")
        assert False


    # 準備更新和新插入的數據
    data2 = { # 更新第一筆，新增一筆
        "TradeDate": pd.to_datetime(['2023-10-01', '2023-10-02']),
        "ContractMonth": ['202312', '202312'],
        "StrikePrice": [17000.0, 17200.0],
        "OptionType": ['Call', 'Call'], # 第一筆 PK 與 data1 的第一筆相同
        "Open": [152.0, 100.0], # 更新 Open
        "High": [162.0, 105.0],
        "Low": [142.0, 95.0],
        "Close": [158.0, 102.0], # 更新 Close
        "Volume": [1100, 600]
    }
    df_options2 = pd.DataFrame(data2)

    # 測試 3: Upsert (更新已存在 + 插入新的)
    print("\n--- 測試 3: Upsert 選擇權數據 (更新+插入) ---")
    success2 = db_manager.upsert_data(df_options2, "options_daily", pk_columns=options_pk)
    assert success2

    # 驗證 Upsert
    try:
        with duckdb.connect(test_db_file_path, read_only=True) as con:
            result2_count = con.execute("SELECT COUNT(*) FROM options_daily").fetchone()
            print(f"DBManager 測試: Upsert 後 options_daily 的數據筆數: {result2_count[0] if result2_count else '查詢失敗'}")
            # 預期: 3 (原始) - 1 (被取代) + 2 (新) = 4
            assert result2_count[0] == 4

            # 檢查更新的數據
            updated_row = con.execute("SELECT \"Close\" FROM options_daily WHERE \"TradeDate\" = '2023-10-01' AND \"ContractMonth\" = '202312' AND \"StrikePrice\" = 17000.0 AND \"OptionType\" = 'Call'").fetchone()
            print(f"DBManager 測試: 更新後的 2023-10-01/202312/17000/Call 收盤價: {updated_row[0] if updated_row else '查詢失敗'}")
            assert updated_row is not None and updated_row[0] == 158.0 # 驗證收盤價已更新

            result2_data = con.execute("SELECT * FROM options_daily ORDER BY \"TradeDate\", \"ContractMonth\", \"StrikePrice\", \"OptionType\"").df()
            print("DBManager 測試: Upsert 後 options_daily 的數據內容:")
            print(result2_data)
    except Exception as e:
        print(f"DBManager 測試: 驗證 Upsert 時出錯: {e}")
        assert False

    # 測試 4: 插入空 DataFrame
    print("\n--- 測試 4: 插入空 DataFrame ---")
    df_empty = pd.DataFrame(columns=df_options1.columns) # 確保欄位存在以符合 register
    success_empty = db_manager.upsert_data(df_empty, "options_daily")
    assert success_empty # 應返回 True
    # 資料庫數據不應改變
    with duckdb.connect(test_db_file_path, read_only=True) as con:
        count_after_empty = con.execute("SELECT COUNT(*) FROM options_daily").fetchone()
        assert count_after_empty[0] == 4

    print("\n--- FileProcessor DBManager 測試完畢 ---")
    # os.remove(test_db_file_path)
    # print(f"INFO: 已刪除測試資料庫 {test_db_file_path}")
