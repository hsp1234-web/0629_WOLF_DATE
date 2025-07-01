# -*- coding: utf-8 -*-
"""
交易所檔案處理器執行入口。
掃描指定目錄中的檔案，根據檔案類型選擇合適的解析器進行處理，
並將解析後的數據儲存到資料庫。
"""
import argparse
import sys
import os
import importlib
import glob
from datetime import datetime

def setup_project_path():
    """
    設定專案路徑，確保可以正確匯入其他模組。
    此處為SOP規範的路徑自我校正樣板碼。
    """
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

setup_project_path()

# 延後匯入 DBManager 和 Parsers，因為它們可能依賴 project_path 的設定
# from apps.file_processor.db_manager import DBManager
# from apps.file_processor.parsers import txo_daily_parser # 範例

# --- 常數定義 ---
# 實際應用中，這個對應關係可以更動態，例如從設定檔讀取
#鍵是檔名中包含的關鍵字，值是解析器類別的完整路徑
# (相對於 apps.file_processor.parsers)
PARSER_MAPPING = {
    "OptionsDaily": "txo_daily_parser.TXODailyParser", # 假設台交所選擇權日行情檔案名包含 "OptionsDaily"
    "FutureDaily": "future_daily_parser.FutureDailyParser", # 假設台交所期貨日行情檔案名包含 "FutureDaily"
    # 可以繼續添加其他檔案類型和對應的解析器
    # 例如： "StockDaily": "stock_daily_parser.StockDailyParser",
}

# 處理完畢的檔案存放的子目錄名稱
PROCESSED_DIR_NAME = "processed"
FAILED_DIR_NAME = "failed"


def get_parser_class(parser_name_key: str):
    """
    根據 parser_name_key 從 PARSER_MAPPING 中獲取解析器類別。
    動態匯入解析器模組並返回類別。
    """
    if parser_name_key not in PARSER_MAPPING:
        print(f"警告: 在 PARSER_MAPPING 中找不到鍵 '{parser_name_key}' 的解析器。")
        return None

    parser_module_path_str = PARSER_MAPPING[parser_name_key]
    try:
        module_name, class_name = parser_module_path_str.rsplit('.', 1)
        # 解析器模組相對於 'apps.file_processor.parsers'
        full_module_path = f"apps.file_processor.parsers.{module_name}"

        parser_module = importlib.import_module(full_module_path)
        parser_class = getattr(parser_module, class_name)
        return parser_class
    except ImportError as e:
        print(f"錯誤: 無法匯入解析器模組 '{full_module_path}': {e}")
        return None
    except AttributeError:
        print(f"錯誤: 在模組 '{full_module_path}' 中找不到解析器類別 '{class_name}'。")
        return None
    except Exception as e:
        print(f"錯誤: 動態載入解析器 '{parser_module_path_str}' 時發生未知錯誤: {e}")
        return None


def main():
    """
    主執行函數。
    掃描輸入目錄，為每個檔案選擇合適的解析器，
    執行解析並將結果存入資料庫。
    處理過的檔案會被移動到子目錄。
    """
    parser = argparse.ArgumentParser(description="交易所檔案處理器 - 解析本地檔案並存入資料庫。")
    parser.add_argument("--input-dir", required=True, help="包含待處理檔案的目錄路徑。")
    parser.add_argument("--db-path", default="data_workspace/market_data.duckdb", help="DuckDB 資料庫檔案路徑。預設為 data_workspace/market_data.duckdb。")
    # 可以加入 --move-processed-files, --delete-processed-files 等參數控制處理後檔案的去向
    parser.add_argument("--processed-subdir", default=PROCESSED_DIR_NAME, help=f"成功處理後檔案存放的子目錄名 (相對於 input-dir)。預設: {PROCESSED_DIR_NAME}")
    parser.add_argument("--failed-subdir", default=FAILED_DIR_NAME, help=f"處理失敗檔案存放的子目錄名 (相對於 input-dir)。預設: {FAILED_DIR_NAME}")
    parser.add_argument("--recursive", action="store_true", help="是否遞迴掃描輸入目錄的子目錄。")


    args = parser.parse_args()

    # 動態匯入 DBManager (在 setup_project_path 後)
    try:
        from apps.file_processor.db_manager import DBManager # pylint: disable=import-outside-toplevel
    except ImportError as e:
        print(f"嚴重錯誤: 無法匯入 DBManager: {e}。請確認 apps.file_processor.db_manager.py 存在且路徑正確。")
        sys.exit(1)

    db_mngr = DBManager(args.db_path) # 初始化 DBManager

    input_dir = os.path.abspath(args.input_dir)
    if not os.path.isdir(input_dir):
        print(f"錯誤: 輸入目錄 '{input_dir}' 不存在或不是一個有效的目錄。")
        sys.exit(1)

    processed_path = os.path.join(input_dir, args.processed_subdir)
    failed_path = os.path.join(input_dir, args.failed_subdir)
    os.makedirs(processed_path, exist_ok=True)
    os.makedirs(failed_path, exist_ok=True)

    print(f"INFO: 開始掃描目錄: {input_dir}")
    if args.recursive:
        print("INFO: 將遞迴掃描子目錄。")
        file_paths = glob.glob(os.path.join(input_dir, "**", "*"), recursive=True)
    else:
        file_paths = glob.glob(os.path.join(input_dir, "*"))

    # 過濾掉目錄，只保留檔案
    files_to_process = [f for f in file_paths if os.path.isfile(f)]

    # 過濾掉已經在 processed 或 failed 目錄中的檔案 (避免重複處理或處理自身)
    files_to_process = [
        f for f in files_to_process
        if not (os.path.dirname(f) == processed_path or os.path.dirname(f) == failed_path)
    ]


    if not files_to_process:
        print("INFO: 在指定目錄中沒有找到需要處理的檔案。")
        return

    print(f"INFO: 找到 {len(files_to_process)} 個檔案待處理。")

    for file_path in files_to_process:
        print(f"\n--- 正在處理檔案: {file_path} ---")
        filename = os.path.basename(file_path)
        chosen_parser_class = None

        # 策略：根據檔名關鍵字選擇解析器
        for keyword, parser_path_str in PARSER_MAPPING.items():
            if keyword.lower() in filename.lower(): # 不區分大小寫匹配
                print(f"INFO: 檔案 '{filename}' 匹配關鍵字 '{keyword}'，嘗試使用解析器 '{parser_path_str}'。")
                chosen_parser_class = get_parser_class(keyword)
                if chosen_parser_class:
                    break # 找到第一個匹配的就用

        if not chosen_parser_class:
            print(f"警告: 未能為檔案 '{filename}' 找到合適的解析器。將跳過此檔案。")
            # 可以選擇將其移動到一個 "unknown" 或 "failed" 目錄
            try:
                shutil.move(file_path, os.path.join(failed_path, filename + f".skipped_{datetime.now().strftime('%Y%m%d%H%M%S')}"))
                print(f"INFO: 已將未找到解析器的檔案 '{filename}' 移動到 '{failed_path}'。")
            except Exception as move_err:
                print(f"錯誤: 移動未找到解析器的檔案 '{filename}' 到 '{failed_path}' 失敗: {move_err}")
            continue

        try:
            parser_instance = chosen_parser_class() # 實例化解析器
            print(f"INFO: 使用解析器 '{chosen_parser_class.__name__}' 處理檔案 '{filename}'。")

            # 1. 解析檔案
            parsed_df = parser_instance.parse(file_path)

            if parsed_df is None:
                print(f"警告: 解析器 '{chosen_parser_class.__name__}' 未能從檔案 '{filename}' 中解析出數據。檔案可能為空或格式不符。")
                # 移動到失敗目錄
                shutil.move(file_path, os.path.join(failed_path, filename + f".parse_failed_{datetime.now().strftime('%Y%m%d%H%M%S')}"))
                print(f"INFO: 已將解析失敗的檔案 '{filename}' 移動到 '{failed_path}'。")
                continue

            if parsed_df.empty:
                print(f"INFO: 解析器 '{chosen_parser_class.__name__}' 從檔案 '{filename}' 中解析出空的 DataFrame。無需儲存。")
                # 移動到成功目錄 (因為解析本身是成功的，只是沒有數據)
                shutil.move(file_path, os.path.join(processed_path, filename))
                print(f"INFO: 已將解析出空數據的檔案 '{filename}' 移動到 '{processed_path}'。")
                continue

            print(f"INFO: 成功從 '{filename}' 解析出 {len(parsed_df)} 筆數據。")

            # 2. 儲存到資料庫
            # BaseParser 的 save_to_db 方法需要 db_manager 實例
            # 且需要知道目標表名，這應該由具體的 parser 定義
            # 例如，可以在 parser 內部定義 target_table_name 屬性
            if not hasattr(parser_instance, 'target_table_name') or not parser_instance.target_table_name:
                print(f"錯誤: 解析器 '{chosen_parser_class.__name__}' 未定義 'target_table_name' 屬性。無法儲存數據。")
                shutil.move(file_path, os.path.join(failed_path, filename + f".save_failed_no_table_{datetime.now().strftime('%Y%m%d%H%M%S')}"))
                print(f"INFO: 已將儲存失敗(無目標表)的檔案 '{filename}' 移動到 '{failed_path}'。")
                continue

            save_success = parser_instance.save_to_db(parsed_df, db_mngr)

            if save_success:
                print(f"INFO: 成功將 '{filename}' 的數據儲存到資料庫表 '{parser_instance.target_table_name}'。")
                # 移動到成功目錄
                shutil.move(file_path, os.path.join(processed_path, filename))
                print(f"INFO: 已將成功處理的檔案 '{filename}' 移動到 '{processed_path}'。")
            else:
                print(f"錯誤: 儲存 '{filename}' 的數據到資料庫時發生錯誤。")
                shutil.move(file_path, os.path.join(failed_path, filename + f".save_failed_{datetime.now().strftime('%Y%m%d%H%M%S')}"))
                print(f"INFO: 已將儲存失敗的檔案 '{filename}' 移動到 '{failed_path}'。")

        except Exception as e:
            print(f"錯誤: 處理檔案 '{filename}' 時發生未預期錯誤: {e}")
            import traceback
            traceback.print_exc()
            try:
                # 嘗試移動到失敗目錄
                shutil.move(file_path, os.path.join(failed_path, filename + f".error_{datetime.now().strftime('%Y%m%d%H%M%S')}"))
                print(f"INFO: 已將處理時發生錯誤的檔案 '{filename}' 移動到 '{failed_path}'。")
            except Exception as move_err_on_fail:
                print(f"嚴重錯誤: 移動錯誤檔案 '{filename}' 到 '{failed_path}' 也失敗了: {move_err_on_fail}")
            continue # 繼續處理下一個檔案

    print("\nINFO: 所有檔案處理完畢。")


if __name__ == "__main__":
    # 為了能夠直接執行此腳本進行測試，需要 Python 能夠找到 apps 目錄。
    # setup_project_path() 函數處理了這個問題。

    # 簡易測試的命令列參數模擬 (實際使用時由命令列提供)
    # sys.argv.extend([
    #     "--input-dir", "data_workspace/test_uploads",
    #     "--db-path", "data_workspace/temp/test_file_processor.duckdb"
    # ])
    # print(f"DEBUG: sys.argv after extend: {sys.argv}")

    # 確保 shutil 被匯入 (如果之前沒有)
    import shutil

    main()
