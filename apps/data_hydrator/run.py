# -*- coding: utf-8 -*-
"""
Data Hydrator 主執行入口。

接收命令列參數，協調 YFinanceClient 進行數據回填，
使用 DBManager 將數據存入資料庫，並使用 ReportGenerator 生成總結報告。
"""
import argparse
import sys
import os
from datetime import datetime
import pandas as pd # 為了處理 DataFrame 的 min/max date

# 設定專案路徑，確保可以正確匯入其他模組
def setup_project_path():
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
        print(f"DEBUG: Project root added to sys.path: {project_root}")

setup_project_path()

# 延後導入，確保路徑已設定
# from apps.data_hydrator.yfinance_client import YFinanceClient
# from apps.data_hydrator.db_manager import DBManager
# from apps.data_hydrator.report_generator import ReportGenerator

# 嘗試解決 ModuleNotFoundError
try:
    from apps.data_hydrator.yfinance_client import YFinanceClient
    from apps.data_hydrator.db_manager import DBManager
    from apps.data_hydrator.report_generator import ReportGenerator
    print("DEBUG: Successfully imported YFinanceClient, DBManager, ReportGenerator")
except ModuleNotFoundError as e:
    print(f"ERROR: ModuleNotFoundError during initial imports in run.py: {e}")
    print(f"DEBUG: Current sys.path: {sys.path}")
    # 嘗試列出 apps 和 apps/data_hydrator 目錄內容以幫助調試
    try:
        print(f"DEBUG: Contents of 'apps/': {os.listdir('apps')}")
        print(f"DEBUG: Contents of 'apps/data_hydrator/': {os.listdir('apps/data_hydrator')}")
    except FileNotFoundError:
        print("DEBUG: 'apps/' or 'apps/data_hydrator/' directory not found from current working directory.")
    raise # 重新拋出錯誤，以便外部看到

def main():
    """
    主執行函數。
    """
    parser = argparse.ArgumentParser(description="全自動數據回填與報告生成引擎。")
    parser.add_argument("--tickers", required=True, help="要抓取的標的列表，以逗號分隔 (例如: \"AAPL,MSFT,^GSPC\")。")
    parser.add_argument("--start-date", required=True, help="數據回填的開始日期 (YYYY-MM-DD)。")
    parser.add_argument("--end-date", required=True, help="數據回填的結束日期 (YYYY-MM-DD)。")
    parser.add_argument("--db-path", default="data_workspace/market_data_hydrated.duckdb",
                        help="DuckDB 資料庫檔案路徑。預設: data_workspace/market_data_hydrated.duckdb。")
    parser.add_argument("--table-name", default="market_ohlcv_hydrated",
                        help="資料庫中儲存 OHLCV 數據的表格名稱。預設: market_ohlcv_hydrated。")
    parser.add_argument("--process-uploads", action="store_true",
                        help="若指定，則在處理 yfinance 數據前先處理 'uploads' 資料夾內的檔案 (此功能待實現)。")

    args = parser.parse_args()

    print("--- Data Hydrator v11.0 ---")
    overall_start_time = datetime.now()
    print(f"任務開始時間: {overall_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"參數: Tickers='{args.tickers}', StartDate='{args.start_date}', EndDate='{args.end_date}', DB='{args.db_path}', Table='{args.table_name}'")

    if args.process_uploads:
        print("INFO: --process-uploads 被指定，但此功能尚在開發中，將被跳過。")
        # TODO: 在此處添加 file_processor 的邏輯

    # 初始化組件
    yf_client = YFinanceClient() # 使用預設 cache_dir
    db_manager = DBManager(db_path=args.db_path)
    report_gen = ReportGenerator()

    # 確保資料表存在
    db_manager.create_ohlcv_table(table_name=args.table_name)

    tickers_list = [ticker.strip().upper() for ticker in args.tickers.split(',')]

    successful_hydrations_summary = []
    failed_tickers_summary = []

    for ticker in tickers_list:
        print(f"\n--- 開始處理標的: {ticker} ---")
        hydrated_df = yf_client.hydrate_data_range(ticker, args.start_date, args.end_date)

        if hydrated_df is not None and not hydrated_df.empty:
            print(f"INFO: 標的 {ticker} 數據成功回填 {len(hydrated_df)} 筆。準備寫入資料庫...")
            try:
                db_manager.upsert_data(hydrated_df, table_name=args.table_name)

                # 收集報告資訊
                # DataFrame 的 index 應該是 datetime
                min_date = hydrated_df.index.min()
                max_date = hydrated_df.index.max()
                # interval 應該是 DataFrame 的一個欄位
                actual_interval = hydrated_df['interval'].iloc[0] if 'interval' in hydrated_df.columns else "未知"

                successful_hydrations_summary.append({
                    'ticker': ticker,
                    'interval': actual_interval,
                    'num_rows': len(hydrated_df),
                    'min_date': min_date, # datetime object
                    'max_date': max_date  # datetime object
                })
                print(f"INFO: 標的 {ticker} 數據成功寫入資料庫。")
            except Exception as e:
                print(f"錯誤: 標的 {ticker} 數據寫入資料庫失敗: {e}")
                failed_tickers_summary.append(ticker) # 即使抓取成功，但寫入失敗也算失敗
        else:
            print(f"INFO: 標的 {ticker} 未能回填任何數據。")
            failed_tickers_summary.append(ticker)

        print(f"--- 標的: {ticker} 處理完畢 ---")

    overall_end_time = datetime.now()
    print(f"\n--- 所有標的處理完成 ---")
    print(f"任務結束時間: {overall_end_time.strftime('%Y-%m-%d %H:%M:%S')}")

    # 生成並打印總結報告
    print("\n--- 生成總結報告 ---")
    report_gen.create_summary_report(
        successful_hydrations=successful_hydrations_summary,
        failed_tickers=list(set(failed_tickers_summary)), # 去重
        overall_start_time=overall_start_time,
        overall_end_time=overall_end_time,
        target_tickers=tickers_list,
        target_start_date=args.start_date,
        target_end_date=args.end_date
    )

    print("--- Data Hydrator 任務執行完畢 ---")

if __name__ == "__main__":
    # 為了確保在直接執行此腳本時，相對導入能正常工作，
    # 我們需要確保 `apps` 目錄在 `sys.path` 中。
    # `setup_project_path()` 應該已經處理了這個。
    # 如果在IDE中執行，或者作為模組執行 (`python -m apps.data_hydrator.run`)，
    # Python的導入機制通常能正確處理。
    # 但如果直接 `python apps/data_hydrator/run.py`，則需要 `setup_project_path()`。

    # 顯示當前工作目錄和 sys.path 以幫助調試導入問題
    print(f"DEBUG: Current CWD for __main__: {os.getcwd()}")
    print(f"DEBUG: Current sys.path for __main__: {sys.path}")

    # 重新檢查導入，因為直接執行腳本時的導入行為可能不同
    # (setup_project_path 應該已經處理了這個，但作為雙重檢查)
    if 'YFinanceClient' not in globals():
        try:
            from apps.data_hydrator.yfinance_client import YFinanceClient
            from apps.data_hydrator.db_manager import DBManager
            from apps.data_hydrator.report_generator import ReportGenerator
            print("DEBUG: Late imports in __main__ successful.")
        except ModuleNotFoundError as e:
            print(f"ERROR: Late ModuleNotFoundError in __main__: {e}")
            # 不再 raise，因為上面已經 raise 過一次了

    main()
