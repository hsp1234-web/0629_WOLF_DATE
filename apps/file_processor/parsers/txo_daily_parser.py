# -*- coding: utf-8 -*-
"""
TXODailyParser - 台灣期貨交易所選擇權每日交易行情解析器。

負責解析從台灣期貨交易所下載的選擇權每日交易行情CSV檔案。
這些檔案通常具有特定的欄位名稱和結構，並且可能使用 'big5' 編碼。
"""
import pandas as pd
import os
from .base_parser import BaseParser
# from ..db_manager import DBManager # 避免直接匯入

class TXODailyParser(BaseParser):
    """
    解析台灣期貨交易所 (TAIFEX) 選擇權每日交易行情 CSV 檔案。

    檔案範例特徵：
    - CSV 格式。
    - 通常使用 'big5' 或 'cp950' 編碼。
    - 包含諸如 "交易日期", "契約", "到期月份(週別)", "履約價", "買賣權",
      "開盤價", "最高價", "最低價", "收盤價", "成交量" 等欄位。
    - 欄位名稱可能隨時間略有變化，或有不一致的空格。

    此解析器會將原始數據轉換為一個標準化的 DataFrame，
    欄位名稱統一，數據類型轉換，並準備好寫入資料庫。
    """
    # 指定此解析器處理後的數據應儲存到哪個資料庫表
    target_table_name: str = "taifex_options_daily"

    # 定義目標資料表的 schema 和主鍵，如果 parser 也負責建立資料表的話
    # 這些資訊也會被 save_to_db 方法用來驗證 DataFrame 或協助 DBManager。
    target_table_schema: dict[str, str] = {
        "TradeDate": "DATE",            # 交易日期
        "Symbol": "VARCHAR",            # 標準化商品代碼 (例如 TXO)
        "ExpiryType": "VARCHAR",        # 到期類型 (例如 W1, W2, M)
        "ExpiryDate": "DATE",           # 標準化到期日
        "StrikePrice": "DOUBLE PRECISION",# 履約價
        "OptionType": "VARCHAR",        # 買權 (Call) 或賣權 (Put)
        "Open": "DOUBLE PRECISION",
        "High": "DOUBLE PRECISION",
        "Low": "DOUBLE PRECISION",
        "Close": "DOUBLE PRECISION",
        "Volume": "BIGINT",             # 成交量
        "SettlementPrice": "DOUBLE PRECISION", # 結算價
        "OpenInterest": "BIGINT",       # 未平倉量
        "SourceFileName": "VARCHAR"     # 來源檔案名稱，方便追溯
    }
    target_table_primary_keys: list[str] = [
        "TradeDate", "Symbol", "ExpiryDate", "StrikePrice", "OptionType"
    ]

    # 欄位對應：原始檔案中的欄位名 -> 標準化後的 DataFrame 欄位名
    # 鍵是可能的原始欄位名 (小寫，移除空格)，值是標準化名稱
    # 處理多種可能的原始欄位名稱
    COLUMN_MAPPING = {
        '交易日期': 'TradeDate',
        '到期月份(週別)': 'ExpiryInfo', # 需要進一步處理
        '履約價': 'StrikePrice',
        '買賣權': 'OptionType',
        '開盤價': 'Open',
        '最高價': 'High',
        '最低價': 'Low',
        # '最後成交價': 'Close', # 有時是 "最後成交價"
        '收盤價': 'Close',     # 有時是 "收盤價"
        '結算價': 'SettlementPrice',
        '成交量': 'Volume',
        '未沖銷契約量': 'OpenInterest',
        '契約': 'Symbol', # 例如 "TXO"
        # --- 其他可能的欄位名稱 (小寫化，去除非中文字符和括號) ---
        '到期月份週別': 'ExpiryInfo',
        '未沖銷契約量': 'OpenInterest',
        '最後最佳買價': 'BestBid', # 可能需要，但目前 schema 未包含
        '最後最佳賣價': 'BestAsk', # 可能需要，但目前 schema 未包含
    }


    def _clean_column_names(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        清洗 DataFrame 的欄位名稱：
        1. 移除前後空格。
        2. 轉換為小寫以便於映射 (雖然我們的 mapping key 是中文)。
        3. 根據 COLUMN_MAPPING 重新命名。
        """
        standardized_columns = {}
        original_columns_lower_map = {col.lower().replace(" ", ""): col for col in df.columns}

        for raw_key_chinese, target_col in self.COLUMN_MAPPING.items():
            # 在原始欄位的小寫版本中尋找匹配
            # 這裡的 raw_key_chinese 假設是我們預期的、最常見的中文欄位名
            # 但實際檔案中的欄位名可能會有細微差異 (如空格)
            # 我們需要一種更健壯的方式來匹配

            # 策略：將原始 DataFrame 的欄位名也進行類似的清洗 (移除空格、轉小寫)
            # 然後與 COLUMN_MAPPING 的鍵 (也經過類似清洗) 進行比較

            # 這裡簡化：假設 COLUMN_MAPPING 的鍵是"標準的"原始中文名
            # 而 DataFrame 的欄位名可能帶有空格
            found_original_col = None
            for df_col_original_case in df.columns:
                df_col_cleaned = df_col_original_case.strip() # 移除前後空格
                if df_col_cleaned == raw_key_chinese:
                    found_original_col = df_col_original_case
                    break

            if found_original_col:
                standardized_columns[found_original_col] = target_col
            else:
                # 如果直接匹配不到，可以嘗試更寬鬆的匹配，例如忽略括號內容等
                # print(f"DEBUG: 未直接找到欄位 '{raw_key_chinese}'，將嘗試模糊匹配。")
                pass # 暫時不加模糊匹配，要求 mapping key 與檔案欄位名(除空格外)一致

        # 找出實際被對應上的原始欄位
        df_renamed = df.rename(columns=standardized_columns)

        # 過濾掉不在目標 schema 中的多餘欄位 (基於 COLUMN_MAPPING 的值)
        # target_standard_names = set(self.COLUMN_MAPPING.values())
        # df_filtered = df_renamed[[col for col in df_renamed.columns if col in target_standard_names or col in df.columns.difference(standardized_columns.keys())]]
        # 上述過濾邏輯可能不對，應該是只保留 target_standard_names

        final_columns_to_keep = [col for col in standardized_columns.values() if col in df_renamed.columns]
        return df_renamed[final_columns_to_keep]


    def _parse_expiry_info(self, expiry_info_series: pd.Series, trade_date_series: pd.Series) -> tuple[pd.Series, pd.Series]:
        """
        解析 "到期月份(週別)" 欄位。
        格式可能為 YYYYMM (月選擇權) 或 YYYYMMW1/W2/W4/W5 (週選擇權)。
        返回標準化的到期類型 (M, W1, W2, W4, W5) 和到期日 (Date)。

        Args:
            expiry_info_series (pd.Series): 包含原始到期資訊的 Series。
            trade_date_series (pd.Series): 交易日期 Series，用於輔助推算週選到期日。

        Returns:
            tuple[pd.Series, pd.Series]: (ExpiryType Series, ExpiryDate Series)
        """
        expiry_types = []
        expiry_dates = []

        for idx, raw_expiry in expiry_info_series.items():
            trade_date = trade_date_series[idx]
            s_expiry = str(raw_expiry).strip()
            year = int(s_expiry[:4])
            month = int(s_expiry[4:6])

            expiry_type = "M" # Default to Monthly

            if 'W' in s_expiry.upper():
                week_code = s_expiry.upper().split('W')[-1]
                try:
                    # TAIFEX 週選擇權 W1, W2, W4, W5
                    # W1: 當月第一個週三, W2: 當月第二個週三, W4: 當月第四個週三
                    # W5: 有些月份有第五個週三，但較少見，且TXO通常是W1,W2,W4和月選
                    # 這裡簡化處理，假設 W 後面直接是數字代表第幾週
                    # 更精確的邏輯需要查閱交易所日曆或使用更複雜的庫
                    # TXO 週選擇權是每週三到期

                    # 找到當月的所有週三
                    first_day_of_month = pd.Timestamp(year, month, 1)
                    # days_in_month = first_day_of_month.days_in_month
                    # wednesdays = []
                    # for day_num in range(1, days_in_month + 1):
                    #     current_date = pd.Timestamp(year, month, day_num)
                    #     if current_date.weekday() == 2: # 0=Mon, 1=Tue, 2=Wed
                    #         wednesdays.append(current_date)

                    # 這是簡化邏輯，實際TAIFEX的W1/W2/W4命名與日曆週的第幾個週三有關
                    # 例如 202303W1 -> 2023/03/01 (第一個週三)
                    # 202303W2 -> 2023/03/08 (第二個週三)
                    # 202303W4 -> 2023/03/22 (第四個週三)
                    # 月選 TXO202303 -> 2023/03/15 (第三個週三)

                    # 這裡我們需要一個更可靠的方式從 "YYYYMMWn" 推算出實際到期日
                    # 暫時使用一個非常簡化的佔位邏輯，實際應用中需要替換
                    # 假設 W1 是第一週的週三，W2 是第二週的週三 ...
                    # 月選是第三個週三

                    # 簡化：若為週選，到期日暫時設定為該月 trade_date 之後的某個週三
                    # 這部分邏輯非常複雜，暫時用一個粗略的估計或標記
                    if week_code == "1": expiry_type = "W1"
                    elif week_code == "2": expiry_type = "W2"
                    elif week_code == "4": expiry_type = "W4"
                    elif week_code == "5": expiry_type = "W5" # 較少見
                    else: expiry_type = f"W{week_code}" # 其他未知週別

                    # 粗略估計到期日：當月的第N個週三 (這不完全準確)
                    # 暫時將週選的 ExpiryDate 設定為該月的15號，表示需要修正
                    # 正確做法是查找該 YYYYMMWn 對應的精確到期日
                    expiry_day_approx = (int(week_code) * 7) if week_code.isdigit() and int(week_code) <= 4 else 15
                    expiry_day_approx = min(expiry_day_approx, first_day_of_month.days_in_month)
                    expiry_date_val = pd.Timestamp(year, month, expiry_day_approx)

                except ValueError: # 無法解析週別數字
                     expiry_date_val = pd.Timestamp(year, month, 15) # 預設到月中
            else: # 月選擇權 YYYYMM
                expiry_type = "M"
                # 台指選擇權月合約通常是該月第三個星期三到期
                # 找到該月第三個星期三
                third_wed = None
                count_wed = 0
                for day_num in range(1, pd.Timestamp(year, month, 1).days_in_month + 1):
                    d = pd.Timestamp(year, month, day_num)
                    if d.weekday() == 2: # Wednesday
                        count_wed += 1
                        if count_wed == 3:
                            third_wed = d
                            break
                expiry_date_val = third_wed if third_wed else pd.Timestamp(year, month, 15) # 備用

            expiry_types.append(expiry_type)
            expiry_dates.append(pd.to_datetime(expiry_date_val.date())) # 只要日期部分

        return pd.Series(expiry_types, index=expiry_info_series.index), pd.Series(expiry_dates, index=expiry_info_series.index)


    def parse(self, file_path: str) -> pd.DataFrame | None:
        """
        解析台指選擇權每日行情的 CSV 檔案。
        """
        print(f"INFO ({self.__class__.__name__}): 開始解析檔案 {file_path}...")
        try:
            # 嘗試用 big5 編碼讀取，這是台交所檔案常見編碼
            # 有些檔案可能欄位間有多餘空格，或數字欄位中包含逗號
            # converters 用於預處理特定欄位
            df = pd.read_csv(
                file_path,
                encoding='big5', # 或 'cp950'
                skiprows=[0], # 跳過中文標題行 "期貨交易所個股選擇權每日交易行情" (如果存在)
                              # 或者在 read_csv 後檢查第一行是否為數據
                thousands=',', # 處理數字中的逗號，例如成交量 "1,234"
                na_values=['-', ' ', 'NaN', 'NA', 'N/A', ''], # '-' 常見於無開盤價等情況
                dtype=str # 先全部讀成字串，後續再轉換，避免 pandas 自動推斷類型錯誤
            )

            # 有些檔案第一行可能是說明文字，需要跳過
            # 檢查第一列是否是 "交易日期" 或類似的標頭，如果不是，則認為第一行是數據
            if df.columns[0].strip() != '交易日期' and not df.iloc[0,0].replace('-','').isdigit():
                 df = pd.read_csv(file_path, encoding='big5', skiprows=[0], thousands=',', na_values=['-'], dtype=str)


            # 移除欄位名稱中的不規則空格，並根據 COLUMN_MAPPING 重新命名
            df.columns = [col.strip().replace(' ', '') for col in df.columns] # 先做一次通用清洗
            df_renamed = self._clean_column_names(df.copy()) # 使用 copy 避免 SettingWithCopyWarning

            # --- 必要欄位檢查 ---
            required_std_cols = ['TradeDate', 'ExpiryInfo', 'StrikePrice', 'OptionType', 'Symbol']
            missing_cols = [col for col in required_std_cols if col not in df_renamed.columns]
            if missing_cols:
                print(f"錯誤 ({self.__class__.__name__}): 檔案 {file_path} 缺少必要的標準化後欄位: {missing_cols}。原始欄位: {list(df.columns)}")
                return None

            # --- 數據類型轉換與清洗 ---
            # 交易日期
            df_renamed['TradeDate'] = pd.to_datetime(df_renamed['TradeDate'].str.replace('/', '-'), errors='coerce')

            # 處理到期月份(週別) -> ExpiryType, ExpiryDate
            expiry_type_series, expiry_date_series = self._parse_expiry_info(df_renamed['ExpiryInfo'], df_renamed['TradeDate'])
            df_renamed['ExpiryType'] = expiry_type_series
            df_renamed['ExpiryDate'] = expiry_date_series

            # 買賣權轉換: "買權" -> "Call", "賣權" -> "Put"
            option_type_map = {'買權': 'Call', '賣權': 'Put'}
            df_renamed['OptionType'] = df_renamed['OptionType'].map(option_type_map).fillna(df_renamed['OptionType'])


            # 數值欄位轉換
            numeric_cols = ['StrikePrice', 'Open', 'High', 'Low', 'Close', 'SettlementPrice', 'Volume', 'OpenInterest']
            for col in numeric_cols:
                if col in df_renamed.columns:
                    # 先替換可能的非數值字元 (例如空的 '-' 被讀為字串)
                    df_renamed[col] = df_renamed[col].astype(str).str.replace(' ', '').replace('-', 'NaN')
                    df_renamed[col] = pd.to_numeric(df_renamed[col], errors='coerce').fillna(0) # coerce 會將無法轉換的設為 NaT/NaN
                    if col in ['Volume', 'OpenInterest']:
                        df_renamed[col] = df_renamed[col].astype('Int64') # 使用可空整數類型
                    else:
                        df_renamed[col] = df_renamed[col].astype('float64')
                else:
                    # 如果目標 schema 中有此欄位，但原始數據沒有，則補上空值或0
                    if col in self.target_table_schema:
                         df_renamed[col] = 0 if col in ['Volume', 'OpenInterest'] else 0.0
                         if col in ['Volume', 'OpenInterest']: df_renamed[col] = df_renamed[col].astype('Int64')


            # Symbol 標準化 (例如，如果檔案中是 "TXO"，確保一致)
            # 假設 Symbol 欄位已經是 "TXO" 或類似值
            if 'Symbol' in df_renamed.columns:
                df_renamed['Symbol'] = df_renamed['Symbol'].str.strip().str.upper()
            else: # 如果原始數據沒有 Symbol 欄位，但檔案名或內容暗示是 TXO
                # 這裡可以根據檔案名或其他上下文賦予一個預設值
                if "txo" in file_path.lower() or "option" in file_path.lower(): # 簡易判斷
                    df_renamed['Symbol'] = "TXO" # 預設為台指選擇權
                else:
                    df_renamed['Symbol'] = "UNKNOWN"


            # 加入來源檔案名稱
            df_renamed['SourceFileName'] = os.path.basename(file_path)

            # --- 篩選並排序欄位以符合 target_table_schema ---
            final_df = pd.DataFrame()
            for target_col_name in self.target_table_schema.keys():
                if target_col_name in df_renamed.columns:
                    final_df[target_col_name] = df_renamed[target_col_name]
                else:
                    # 如果目標 schema 中的欄位不存在於處理後的 df，則補上空值
                    # (理論上，如果 COLUMN_MAPPING 和處理邏輯完整，這裡不應該發生)
                    print(f"警告 ({self.__class__.__name__}): 目標欄位 '{target_col_name}' 在處理後 DataFrame 中缺失，將補空值。")
                    if self.target_table_schema[target_col_name] in ["BIGINT", "INTEGER"]:
                        final_df[target_col_name] = pd.Series([pd.NA] * len(df_renamed), dtype='Int64')
                    elif self.target_table_schema[target_col_name] in ["DOUBLE PRECISION", "FLOAT"]:
                        final_df[target_col_name] = pd.Series([pd.NA] * len(df_renamed), dtype='float64')
                    elif self.target_table_schema[target_col_name] == "DATE":
                         final_df[target_col_name] = pd.Series([pd.NaT] * len(df_renamed), dtype='datetime64[ns]')
                    else:
                        final_df[target_col_name] = pd.Series([pd.NA] * len(df_renamed), dtype='object')


            # 移除 TradeDate 為 NaT 的行 (通常是解析錯誤或空行導致)
            final_df.dropna(subset=['TradeDate'], inplace=True)

            if final_df.empty:
                print(f"INFO ({self.__class__.__name__}): 解析檔案 {file_path} 後得到空的 DataFrame (可能所有行都無效)。")
                return None

            print(f"INFO ({self.__class__.__name__}): 成功解析檔案 {file_path}，共 {len(final_df)} 筆有效數據。")
            # final_df.info() # Debug
            return final_df

        except FileNotFoundError:
            print(f"錯誤 ({self.__class__.__name__}): 檔案不存在 {file_path}")
            return None
        except pd.errors.EmptyDataError:
            print(f"錯誤 ({self.__class__.__name__}): 檔案為空或無有效數據 {file_path}")
            return None
        except UnicodeDecodeError:
            print(f"錯誤 ({self.__class__.__name__}): 檔案 {file_path} 編碼錯誤，嘗試使用 Big5 解碼失敗。請確認檔案編碼。")
            return None
        except Exception as e:
            print(f"錯誤 ({self.__class__.__name__}): 解析檔案 {file_path} 時發生未預期錯誤: {e}")
            import traceback
            traceback.print_exc()
            return None

    def save_to_db(self, df: pd.DataFrame, db_manager) -> bool:
        """
        將解析後的選擇權 DataFrame 數據儲存到資料庫。
        在儲存前，會確保目標資料表存在。
        """
        if df is None or df.empty:
            print(f"INFO ({self.__class__.__name__}): 沒有數據可以儲存到資料表 '{self.target_table_name}'。")
            return True # 視為成功，因為沒有錯誤發生

        # 步驟 1: 確保目標資料表已根據 schema 建立 (可選，取決於 DBManager 設計)
        # 假設 db_manager 有一個 create_table_from_schema 方法
        try:
            db_manager.create_table_from_schema(
                self.target_table_name,
                self.target_table_schema,
                self.target_table_primary_keys
            )
            print(f"INFO ({self.__class__.__name__}): 已確認/建立資料表 '{self.target_table_name}'。")
        except Exception as e:
            print(f"錯誤 ({self.__class__.__name__}): 準備資料表 '{self.target_table_name}' 失敗: {e}")
            return False

        # 步驟 2: 使用 DBManager 的 upsert_data 方法儲存數據
        try:
            # 確保 DataFrame 中的欄位順序和名稱與 target_table_schema 一致
            # (parse 方法應該已經處理了這個)
            # df_to_save = df[list(self.target_table_schema.keys())] # 嚴格匹配 schema 順序

            # 直接傳遞 df，DBManager 的 upsert_data 應能處理欄位對應
            success = db_manager.upsert_data(df, self.target_table_name, pk_columns=self.target_table_primary_keys)
            if success:
                print(f"INFO ({self.__class__.__name__}): 成功將 {len(df)} 筆數據儲存到資料表 '{self.target_table_name}'。")
            else:
                print(f"錯誤 ({self.__class__.__name__}): 儲存數據到資料表 '{self.target_table_name}' 失敗 (DBManager 返回 False)。")
            return success
        except Exception as e:
            print(f"錯誤 ({self.__class__.__name__}): 儲存數據到資料表 '{self.target_table_name}' 時發生異常: {e}")
            import traceback
            traceback.print_exc()
            return False


if __name__ == '__main__':
    # --- 簡易測試代碼 ---
    print(f"--- 開始測試 {TXODailyParser.__name__} ---")

    # 建立一個模擬的 DBManager
    class MockFileProcessorDBManager:
        def __init__(self, db_path=None):
            self.db = {} # 用字典模擬資料庫
            self.db_path = db_path if db_path else "mock_db.duckdb"
            print(f"MockFileProcessorDBManager initialized for {self.db_path}")

        def create_table_from_schema(self, table_name, schema, primary_keys=None):
            if table_name not in self.db:
                self.db[table_name] = {'schema': schema, 'pk': primary_keys, 'data': []}
                print(f"MockDB: Table '{table_name}' created with schema: {schema}, PK: {primary_keys}")
            else:
                print(f"MockDB: Table '{table_name}' already exists.")

        def upsert_data(self, df, table_name, pk_columns=None):
            if table_name not in self.db:
                print(f"MockDB Error: Table '{table_name}' does not exist.")
                return False

            # 簡易的 upsert 邏輯 (基於主鍵替換)
            # 這裡只做簡單的 append，實際 DuckDB 會處理
            num_rows = len(df)
            self.db[table_name]['data'].extend(df.to_dict('records'))
            print(f"MockDB: Upserted {num_rows} rows into '{table_name}'. Total rows: {len(self.db[table_name]['data'])}")
            return True

    # 建立一個假的 CSV 檔案 (模擬台交所選擇權日報表)
    # 注意：欄位名稱和順序可能與真實檔案有差異，需依實際情況調整 COLUMN_MAPPING
    sample_csv_content = """交易日期,契約,到期月份(週別),履約價,買賣權,開盤價,最高價,最低價,收盤價,結算價,成交量,未沖銷契約量,最後最佳買價,最後最佳賣價,歷史最高價,歷史最低價,是否豁免代為沖銷,標的證券價格,價差對單式委託比率,價差對單式成交比率
2023/10/18,TXO,202310W3,16700,買權,200,220,180,210,210,1000,5000,209,211,,,,,,
2023/10/18,TXO,202310W3,16700,賣權,50,60,40,45,45,800,4000,44,46,,,,,,
2023/10/18,TXO,202311,17000,買權,-,155,-,150,150,1200,6000,149,151,,,,,,
2023/10/19,TXO,202310W4,16800,買權,100,110,90,105,105,900,4500,104,106,,,,,,
""" # W3 週選到期日是 2023/10/18 (當天)
      # W4 週選到期日是 2023/10/25
      # 月選 202311 到期日是 2023/11/15 (第三個週三)

    # 更多測試數據，包含一些特殊情況
    sample_csv_content_edge_cases = """交易日期,契約,到期月份(週別),履約價,買賣權,開盤價,最高價,最低價,收盤價,結算價,成交量,未沖銷契約量
2023/11/01,TXO,202311W1,17000,買權,10.5,12,8.0,9.5,9.5,"1,234",500
2023/11/01,TXO,202311,17000,賣權,-,-,-,-,80.0,0,300
2023/11/02,TXO,202312W5,17500,買權,5,5,5,5,5,10,20
""" # W5 是一個邊緣情況，成交量有逗號，有些價格是 '-' (無交易)

    test_file_path = "temp_txo_daily_test.csv"
    with open(test_file_path, "w", encoding="big5") as f: # 存為 big5
        f.write(sample_csv_content_edge_cases)

    parser = TXODailyParser()
    mock_db_manager = MockFileProcessorDBManager()

    print(f"\n--- 測試解析檔案: {test_file_path} ---")
    parsed_df = parser.parse(test_file_path)

    if parsed_df is not None:
        print(f"\n--- 解析後的 DataFrame (共 {len(parsed_df)} 筆): ---")
        print(parsed_df.head())
        parsed_df.info()

        print(f"\n--- 測試儲存到資料庫 (目標表: {parser.target_table_name}) ---")
        save_success = parser.save_to_db(parsed_df, mock_db_manager)
        print(f"儲存操作是否成功: {save_success}")

        if save_success:
            print("\n--- Mock DB 內容: ---")
            # print(mock_db_manager.db[parser.target_table_name]['data'])
            # 轉換為 DataFrame 打印較易讀
            if mock_db_manager.db[parser.target_table_name]['data']:
                 print(pd.DataFrame(mock_db_manager.db[parser.target_table_name]['data']))
            else:
                print("Mock DB 中沒有數據。")
    else:
        print(f"解析檔案 {test_file_path} 失敗，無法進行後續測試。")

    # 清理測試檔案
    if os.path.exists(test_file_path):
        os.remove(test_file_path)
        print(f"\n已刪除測試檔案: {test_file_path}")

    print(f"\n--- {TXODailyParser.__name__} 測試完畢 ---")
