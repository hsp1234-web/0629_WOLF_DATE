# -*- coding: utf-8 -*-
"""
解析器抽象基底類別 (ABC)。
所有特定檔案格式的解析器都應繼承此類別，並實作其抽象方法。
"""
import abc
import pandas as pd
# 假設 DBManager 的路徑是 apps.file_processor.db_manager
# 為了避免循環依賴和簡化，這裡不直接 import DBManager，
# 而是期望在 save_to_db 方法中傳入 db_manager 實例。
# from ..db_manager import DBManager # 避免直接匯入以保持 parser 的獨立性

class BaseParser(abc.ABC):
    """
    解析器的抽象基底類別。

    定義了所有解析器必須實現的標準介面，包括：
    - `parse(file_path: str) -> pd.DataFrame | None`: 解析檔案並返回 DataFrame。
    - `save_to_db(df: pd.DataFrame, db_manager) -> bool`: 將 DataFrame 存儲到資料庫。

    子類別通常還需要定義一個 `target_table_name` 屬性，
    指示解析後的數據應儲存到哪個資料庫表中。
    """

    # 子類別應定義此屬性，指定數據要儲存的目標資料庫表名
    target_table_name: str = ""
    # 子類別應定義此屬性，指定資料庫表的主鍵欄位列表，用於 upsert
    # 如果表結構由 parser 負責建立，則此 schema 也應被定義
    # target_table_primary_keys: list[str] = []
    # target_table_schema: dict[str, str] = {}


    @abc.abstractmethod
    def parse(self, file_path: str) -> pd.DataFrame | None:
        """
        解析指定的檔案並將其內容轉換為 Pandas DataFrame。

        Args:
            file_path (str): 待解析檔案的完整路徑。

        Returns:
            pd.DataFrame | None: 包含解析後數據的 DataFrame。
                                 如果解析失敗或檔案不適用於此解析器，則返回 None。
                                 返回的 DataFrame 欄位應標準化，以利後續處理。
        """
        pass

    @abc.abstractmethod
    def save_to_db(self, df: pd.DataFrame, db_manager) -> bool:
        """
        將解析後的 DataFrame 數據儲存到資料庫。

        Args:
            df (pd.DataFrame): 從 `parse` 方法返回的，包含標準化數據的 DataFrame。
            db_manager: 一個 `apps.file_processor.db_manager.DBManager` 的實例，
                        用於執行資料庫操作。

        Returns:
            bool: 如果數據成功儲存到資料庫，則返回 True，否則返回 False。
        """
        pass

    # 可以選擇性地加入一個方法來讓 parser 負責建立它所對應的資料表
    # def ensure_table_exists(self, db_manager) -> bool:
    #     """
    #     確保目標資料表在資料庫中存在。如果不存在，則嘗試根據定義的 schema 建立它。
    #     這是一個可選的輔助方法，具體是否實作取決於設計。
    #     """
    #     if not self.target_table_name:
    #         print(f"錯誤 ({self.__class__.__name__}): 未定義 target_table_name，無法確保資料表存在。")
    #         return False
    #     if not self.target_table_schema:
    #         print(f"錯誤 ({self.__class__.__name__}): 未定義 target_table_schema，無法建立資料表 '{self.target_table_name}'。")
    #         return False

    #     try:
    #         # 這裡假設 db_manager 有一個 create_table_from_schema 方法
    #         db_manager.create_table_from_schema(
    #             self.target_table_name,
    #             self.target_table_schema,
    #             self.target_table_primary_keys
    #         )
    #         return True
    #     except Exception as e:
    #         print(f"錯誤 ({self.__class__.__name__}): 確保/建立資料表 '{self.target_table_name}' 失敗: {e}")
    #         return False


if __name__ == '__main__':
    # 這個基底類別本身不能被實例化，因為它是抽象的。
    # 以下是如何定義一個簡單的子類別範例：

    class MySampleParser(BaseParser):
        target_table_name = "sample_data"
        # target_table_primary_keys = ["id"]
        # target_table_schema = {"id": "INTEGER", "name": "VARCHAR", "value": "DOUBLE"}

        def parse(self, file_path: str) -> pd.DataFrame | None:
            print(f"MySampleParser: 正在解析檔案 {file_path}...")
            # 假設是 CSV 檔案
            try:
                df = pd.read_csv(file_path)
                # ... 進行欄位標準化等操作 ...
                # 例如，確保有 'id', 'name', 'value' 欄位
                if 'ID' in df.columns: # 假設原始欄位是大寫ID
                    df.rename(columns={'ID':'id'}, inplace=True)

                # 檢查必要欄位
                if not all(col in df.columns for col in ['id', 'name', 'value']):
                    print(f"MySampleParser: 檔案 {file_path} 缺少必要欄位。")
                    return None

                return df[['id', 'name', 'value']]
            except Exception as e:
                print(f"MySampleParser: 解析檔案 {file_path} 失敗: {e}")
                return None

        def save_to_db(self, df: pd.DataFrame, db_manager) -> bool:
            print(f"MySampleParser: 正在將數據儲存到資料表 '{self.target_table_name}'...")
            if df is None or df.empty:
                print("MySampleParser: 沒有數據可以儲存。")
                return True # 或 False，取決於如何定義空數據的儲存行為

            # 假設 db_manager 有一個 upsert_data 方法
            # success = db_manager.upsert_data(df, self.target_table_name, pk_columns=self.target_table_primary_keys)
            # 為了測試，這裡只打印訊息
            print(f"MySampleParser: (模擬) {len(df)} 筆數據已儲存到 {self.target_table_name}。")
            # return success
            return True # 模擬成功

    # 測試 MySampleParser (僅為示例，通常解析器會有自己的測試檔案)
    # 建立一個假的 db_manager 和檔案來測試
    class MockDBManager:
        def upsert_data(self, df, table_name, pk_columns=None):
            print(f"MockDBManager: upsert_data called for table '{table_name}' with {len(df)} rows.")
            return True

    # 建立一個假的測試檔案
    sample_file_content = "ID,Name,Value,ExtraCol\n1,Alice,100.5,foo\n2,Bob,200.0,bar"
    sample_file_path = "sample_test_file.csv"
    with open(sample_file_path, "w", encoding="utf-8") as f:
        f.write(sample_file_content)

    parser = MySampleParser()
    mock_db = MockDBManager()

    parsed_data = parser.parse(sample_file_path)
    if parsed_data is not None:
        print("解析後的數據:")
        print(parsed_data)
        parser.save_to_db(parsed_data, mock_db)

    # 清理測試檔案
    import os
    os.remove(sample_file_path)
