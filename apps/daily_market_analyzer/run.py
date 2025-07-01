# -*- coding: utf-8 -*-
"""
Daily Market Analyzer 主執行入口 (v12.0)。
協調數據抓取、存儲、分析與報告生成。
"""
import argparse
import sys
import os
from datetime import datetime
import pandas as pd # 雖然 run.py 本身可能不直接操作太多DF，但導入以備不時之需或類型提示

def setup_project_path():
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
        # print(f"DEBUG: Project root added to sys.path: {project_root}")

setup_project_path()

# 核心模組導入
try:
    from apps.daily_market_analyzer.yfinance_client import YFinanceClient
    from apps.daily_market_analyzer.db_manager import DBManager
    from apps.daily_market_analyzer.analysis_engine import AnalysisEngine
    from apps.daily_market_analyzer.report_generator import ReportGenerator
except ModuleNotFoundError as e:
    print(f"CRITICAL ERROR: Failed to import core modules in run.py: {e}", file=sys.stderr)
    print(f"Current sys.path: {sys.path}", file=sys.stderr)
    # Attempt to list contents for debugging from where Python is trying to run
    try:
        print(f"Listing 'apps/' from CWD ({os.getcwd()}): {os.listdir('apps')}", file=sys.stderr)
        print(f"Listing 'apps/daily_market_analyzer/': {os.listdir('apps/daily_market_analyzer')}", file=sys.stderr)
    except FileNotFoundError:
        print("Could not list 'apps/' or 'apps/daily_market_analyzer/' from CWD.", file=sys.stderr)
    sys.exit(1) # Exit if core components cannot be imported

def main():
    parser = argparse.ArgumentParser(description="每日市場洞察報告與智能數據考古引擎 (v12.0)")
    parser.add_argument("--tickers", required=True, help="要分析的標的列表，以逗號分隔 (e.g., \"AAPL,MSFT,^GSPC\")")
    parser.add_argument("--start-date", required=True, help="分析開始日期 (YYYY-MM-DD)")
    parser.add_argument("--end-date", required=True, help="分析結束日期 (YYYY-MM-DD)")
    parser.add_argument("--db-path", default="data_workspace/daily_market_analysis.duckdb",
                        help="DuckDB 資料庫檔案路徑 (預設: data_workspace/daily_market_analysis.duckdb)")
    parser.add_argument("--table-name", default="market_ohlcv_data_v12",
                        help="資料庫中儲存 OHLCV 數據的表格名稱 (預設: market_ohlcv_data_v12)")
    parser.add_argument("--process-uploads", action="store_true",
                        help="若指定，則處理 'uploads' 資料夾 (此功能待實現)")
    args = parser.parse_args()

    print("--- 每日市場洞察報告引擎 v12.0 ---")
    overall_start_time = datetime.now()
    print(f"任務開始時間: {overall_start_time.strftime('%Y-%m-%d %H:%M:%S %Z%z')}")
    print(f"參數: Tickers='{args.tickers}', StartDate='{args.start_date}', EndDate='{args.end_date}', DB='{args.db_path}', Table='{args.table_name}'")

    if args.process_uploads:
        print("INFO: --process-uploads 被指定，但此功能尚在開發中，將被跳過。")
        # TODO: Implement file_processor logic here if needed

    # 初始化核心組件
    yf_client = YFinanceClient()
    db_manager = DBManager(db_path=args.db_path)
    analysis_engine = AnalysisEngine(db_manager_instance=db_manager)

    # 確保資料庫和目標表格已準備就緒
    try:
        db_manager.create_ohlcv_table(table_name=args.table_name)
    except Exception as e:
        print(f"CRITICAL ERROR: Failed to create or access database table '{args.table_name}': {e}", file=sys.stderr)
        sys.exit(1)

    tickers_list = [ticker.strip().upper() for ticker in args.tickers.split(',') if ticker.strip()]
    if not tickers_list:
        print("錯誤: 未提供有效的股票代碼。請檢查 --tickers 參數。", file=sys.stderr)
        sys.exit(1)

    overall_execution_log = {} # 用於聚合所有 tickers 的執行日誌

    for ticker_symbol in tickers_list:
        print(f"\n processing_ticker 開始處理標的: {ticker_symbol} processing_ticker ")

        hydrated_df, ticker_specific_exec_log = yf_client.hydrate_data_range(
            ticker_symbol, args.start_date, args.end_date
        )

        # 合併單一 ticker 的執行日誌到總體日誌中
        for date_key, daily_ticker_log_val in ticker_specific_exec_log.items():
            overall_execution_log.setdefault(date_key, {}).update(daily_ticker_log_val)

        if hydrated_df is not None and not hydrated_df.empty:
            print(f"INFO: 標的 {ticker_symbol} 數據成功回填 {len(hydrated_df)} 筆。準備寫入資料庫...")
            try:
                db_manager.upsert_data(hydrated_df, table_name=args.table_name)
                print(f"INFO: 標的 {ticker_symbol} 數據成功寫入資料庫。")
                # 日誌已由 yfinance_client 生成並合併，此處無需再手動更新 overall_execution_log 的成功狀態
            except Exception as e:
                print(f"錯誤: 標的 {ticker_symbol} 數據寫入資料庫失敗: {e}")
                # 更新 overall_execution_log 中對應日期的狀態為 db_error
                # 遍歷此 ticker 在 overall_execution_log 中已有的日期條目
                for date_str_key in overall_execution_log:
                    if ticker_symbol in overall_execution_log[date_str_key]:
                         # 只更新那些原先是某種成功或 pending 狀態的條目
                        if overall_execution_log[date_str_key][ticker_symbol]['status'].startswith('success') or \
                           overall_execution_log[date_str_key][ticker_symbol]['status'] == 'pending':
                            overall_execution_log[date_str_key][ticker_symbol]['status'] = 'db_upsert_failed'
                            original_message = overall_execution_log[date_str_key][ticker_symbol].get('message', '')
                            overall_execution_log[date_str_key][ticker_symbol]['message'] = f"{original_message} DB upsert error: {str(e)}".strip()
        else:
            print(f"INFO: 標的 {ticker_symbol} 未能回填任何數據 (基於 yfinance_client 的日誌)。")
            # overall_execution_log 應已由 yfinance_client 更新此 ticker 的失敗/無數據狀態

        print(f" processing_ticker 標的: {ticker_symbol} 處理完畢 processing_ticker ")

    overall_end_time = datetime.now()
    task_duration_seconds = (overall_end_time - overall_start_time).total_seconds()

    print(f"\n\n All_Tickers_Processed --- 所有標的處理完成 --- All_Tickers_Processed ")
    print(f"任務結束時間: {overall_end_time.strftime('%Y-%m-%d %H:%M:%S %Z%z')}")
    print(f"總執行時長: {task_duration_seconds:.2f} 秒")

    # 初始化報告生成器 (傳入合併後的日誌和分析引擎實例)
    report_generator = ReportGenerator(execution_log=overall_execution_log,
                                       analysis_engine_instance=analysis_engine)

    print("\n--- 生成市場分析報告 ---")
    report_generator.generate_full_report(
        overall_start_date_str=args.start_date,
        overall_end_date_str=args.end_date,
        report_generation_time=datetime.now(),
        task_duration_seconds=task_duration_seconds,
        target_tickers=tickers_list, # 傳遞用戶請求的 ticker 列表
        db_table_name=args.table_name
    )

    print("--- 每日市場洞察報告引擎任務執行完畢 ---")

if __name__ == "__main__":
    # print(f"DEBUG: CWD for __main__ in daily_market_analyzer/run.py: {os.getcwd()}")
    # print(f"DEBUG: sys.path for __main__ in daily_market_analyzer/run.py: {sys.path}")
    main()
