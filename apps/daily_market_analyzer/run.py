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
    from apps.daily_market_analyzer.yfinance_client import YFinanceClient
    from apps.daily_market_analyzer.db_manager import DBManager
    from apps.daily_market_analyzer.analysis_engine import AnalysisEngine # 新增
    from apps.daily_market_analyzer.report_generator import ReportGenerator
    print("DEBUG: Successfully imported YFinanceClient, DBManager, AnalysisEngine, ReportGenerator")
except ModuleNotFoundError as e:
    print(f"ERROR: ModuleNotFoundError during initial imports in run.py: {e}")
    print(f"DEBUG: Current sys.path: {sys.path}")
    try:
        print(f"DEBUG: Contents of 'apps/': {os.listdir('apps')}")
        print(f"DEBUG: Contents of 'apps/daily_market_analyzer/': {os.listdir('apps/daily_market_analyzer')}")
    except FileNotFoundError:
        print("DEBUG: 'apps/' or 'apps/daily_market_analyzer/' directory not found from current working directory.")
    raise

def main():
    """
    主執行函數 for Daily Market Analyzer。
    """
    parser = argparse.ArgumentParser(description="每日市場洞察報告與智能數據考古引擎。")
    parser.add_argument("--tickers", required=True, help="要分析的標的列表，以逗號分隔。")
    parser.add_argument("--start-date", required=True, help="分析開始日期 (YYYY-MM-DD)。")
    parser.add_argument("--end-date", required=True, help="分析結束日期 (YYYY-MM-DD)。")
    parser.add_argument("--db-path", default="data_workspace/daily_market_analyzer.duckdb",
                        help="DuckDB 資料庫檔案路徑。")
    parser.add_argument("--table-name", default="market_ohlcv_data", # 更改預設表名
                        help="資料庫中儲存 OHLCV 數據的表格名稱。")
    parser.add_argument("--process-uploads", action="store_true",
                        help="若指定，則處理 'uploads' 資料夾 (此功能待實現)。")

    args = parser.parse_args()

    print("--- 每日市場洞察報告引擎 v11.1 ---") # 更新應用名稱/版本
    overall_start_time = datetime.now()
    print(f"任務開始時間: {overall_start_time.strftime('%Y-%m-%d %H:%M:%S %Z%z')}") # 添加時區信息
    print(f"參數: Tickers='{args.tickers}', StartDate='{args.start_date}', EndDate='{args.end_date}', DB='{args.db_path}', Table='{args.table_name}'")

    if args.process_uploads:
        print("INFO: --process-uploads 被指定，但此功能尚在開發中，將被跳過。")
        # TODO: 添加 file_processor 邏輯

    # 初始化組件
    yf_client = YFinanceClient()
    db_manager = DBManager(db_path=args.db_path)
    analysis_engine = AnalysisEngine(db_manager_instance=db_manager) # 傳遞 db_manager 實例

    # 確保資料表存在 (使用 DBManager 的方法)
    db_manager.create_ohlcv_table(table_name=args.table_name)

    tickers_list = [ticker.strip().upper() for ticker in args.tickers.split(',')]

    overall_execution_log = {} # 用於聚合所有 tickers 的執行日誌

    for ticker in tickers_list:
        print(f"\n--- 開始處理標的: {ticker} ---")
        # hydrate_data_range 現在返回 (DataFrame | None, dict_execution_log)
        hydrated_df, ticker_execution_log = yf_client.hydrate_data_range(ticker, args.start_date, args.end_date)

        # 合併 ticker 的執行日誌到總日誌中
        for date_key, ticker_daily_log_value in ticker_execution_log.items():
            if date_key not in overall_execution_log:
                overall_execution_log[date_key] = {}
            overall_execution_log[date_key].update(ticker_daily_log_value) # ticker_daily_log_value 應為 {ticker: log_info}

        if hydrated_df is not None and not hydrated_df.empty:
            print(f"INFO: 標的 {ticker} 數據成功回填 {len(hydrated_df)} 筆。準備寫入資料庫...")
            try:
                db_manager.upsert_data(hydrated_df, table_name=args.table_name)
                print(f"INFO: 標的 {ticker} 數據成功寫入資料庫。")
            except Exception as e:
                print(f"錯誤: 標的 {ticker} 數據寫入資料庫失敗: {e}")
                # 更新 overall_execution_log 中對應日期的狀態為 db_error
                for date_str_key in pd.date_range(args.start_date, args.end_date).strftime('%Y-%m-%d'):
                    if date_str_key in overall_execution_log and ticker in overall_execution_log[date_str_key]:
                         overall_execution_log[date_str_key][ticker]['status'] = 'db_upsert_failed'
                         overall_execution_log[date_str_key][ticker]['message'] += f" DB upsert error: {str(e)}"
        else:
            print(f"INFO: 標的 {ticker} 未能回填任何數據 (基於 yfinance_client 的日誌)。")
            # overall_execution_log 應已由 yfinance_client 更新了此 ticker 的失敗狀態

        print(f"--- 標的: {ticker} 處理完畢 ---")

    overall_end_time = datetime.now()
    task_duration_seconds = (overall_end_time - overall_start_time).total_seconds()
    print(f"\n--- 所有標的處理完成 ---")
    print(f"任務結束時間: {overall_end_time.strftime('%Y-%m-%d %H:%M:%S %Z%z')}")
    print(f"總執行時長: {task_duration_seconds:.2f} 秒")

    # 初始化報告生成器 (傳入合併後的日誌和分析引擎實例)
    report_gen = ReportGenerator(execution_log=overall_execution_log,
                                 analysis_engine_instance=analysis_engine)

    print("\n--- 生成市場分析報告 ---")
    # 生成並打印總結報告
    report_gen.generate_full_report(
        overall_start_date_str=args.start_date,
        overall_end_date_str=args.end_date,
        report_generation_time=datetime.now(), # 使用當前時間作為報告生成時間
        task_duration_seconds=task_duration_seconds,
        target_tickers=tickers_list,
        db_table_name=args.table_name
    )

    print("--- 每日市場洞察報告引擎任務執行完畢 ---") # 更新結束訊息

if __name__ == "__main__":
    print(f"DEBUG: Current CWD for __main__ in daily_market_analyzer/run.py: {os.getcwd()}")
    print(f"DEBUG: Current sys.path for __main__ in daily_market_analyzer/run.py: {sys.path}")

    # 確保導入路徑正確
    if 'YFinanceClient' not in globals() or 'AnalysisEngine' not in globals(): # 檢查新加入的 AnalysisEngine
        try:
            # 更新導入路徑以匹配新的應用名稱
            from apps.daily_market_analyzer.yfinance_client import YFinanceClient
            from apps.daily_market_analyzer.db_manager import DBManager
            from apps.daily_market_analyzer.analysis_engine import AnalysisEngine
            from apps.daily_market_analyzer.report_generator import ReportGenerator
            print("DEBUG: Late imports in daily_market_analyzer __main__ successful.")
        except ModuleNotFoundError as e:
            print(f"ERROR: Late ModuleNotFoundError in daily_market_analyzer __main__: {e}")

    main()
