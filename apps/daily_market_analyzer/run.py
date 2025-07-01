# -*- coding: utf-8 -*-
"""
每日市場分析儀 主執行入口。

接收命令列參數，協調 YFinanceClient 進行數據擷取與考古，
使用 DBManager 將數據存入資料庫，透過 AnalysisEngine 分析數據，
最後使用 ReportGenerator 生成每日市場洞察報告。
"""
import argparse
import sys
import os
from datetime import datetime
import pandas as pd

# 設定專案路徑，確保可以正確匯入其他模組
def setup_project_path():
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
        # print(f"DEBUG: Project root added to sys.path: {project_root}") # 移除調試信息

setup_project_path()

try:
    from apps.daily_market_analyzer.yfinance_client import YFinanceClient
    from apps.daily_market_analyzer.db_manager import DBManager
    from apps.daily_market_analyzer.analysis_engine import AnalysisEngine
    from apps.daily_market_analyzer.report_generator import ReportGenerator
    # print("DEBUG: Successfully imported YFinanceClient, DBManager, AnalysisEngine, ReportGenerator") # 移除調試信息
except ModuleNotFoundError as e:
    print(f"錯誤：導入模組時發生錯誤 (ModuleNotFoundError): {e}") # 中文化
    # print(f"DEBUG: Current sys.path: {sys.path}") # 保留或移除調試信息
    # try:
    #     print(f"DEBUG: Contents of 'apps/': {os.listdir('apps')}")
    #     print(f"DEBUG: Contents of 'apps/daily_market_analyzer/': {os.listdir('apps/daily_market_analyzer')}")
    # except FileNotFoundError:
    #     print("DEBUG: 'apps/' or 'apps/daily_market_analyzer/' directory not found from current working directory.")
    raise

def main():
    """
    主執行函數 for Daily Market Analyzer。
    """
    parser = argparse.ArgumentParser(description="每日市場洞察報告與智能數據考古引擎。")
    parser.add_argument("--tickers", required=True, help="要分析的標的列表，以逗號分隔 (例如: AAPL,MSFT)。") # 中文化 help
    parser.add_argument("--start-date", required=True, help="分析起始日期 (格式: YYYY-MM-DD)。") # 中文化 help
    parser.add_argument("--end-date", required=True, help="分析結束日期 (格式: YYYY-MM-DD)。") # 中文化 help
    parser.add_argument("--db-path", default="data_workspace/daily_market_analyzer.duckdb",
                        help="DuckDB 資料庫檔案路徑。") # 中文化 help
    parser.add_argument("--table-name", default="market_ohlcv_data",
                        help="資料庫中儲存 OHLCV 數據的表格名稱。") # 中文化 help
    parser.add_argument("--process-uploads", action="store_true",
                        help="若指定，則處理 'uploads' 資料夾 (此功能待實現)。") # 中文化 help

    args = parser.parse_args()

    print("--- 每日市場洞察報告引擎 v12.0 ---") # 更新版本號
    overall_start_time = datetime.now()
    report_generation_time_for_filename = datetime.now() # <<-- 新增：提前定義用於檔案名的時間戳

    # 時區信息 %Z%z 可能因環境導致不同輸出，可考慮標準化為 UTC 或移除
    print(f"任務開始時間: {overall_start_time.strftime('%Y-%m-%d %H:%M:%S')}") # 簡化時間格式
    print(f"執行參數: 標的='{args.tickers}', 起始日='{args.start_date}', 結束日='{args.end_date}', 資料庫='{args.db_path}', 資料表='{args.table_name}'") # 中文化

    if args.process_uploads:
        print("資訊：--process-uploads 選項已指定，但此功能尚在開發中，將被略過。") # 中文化
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
        print(f"\n--- 開始處理標的: {ticker} ---") # 中文化
        # hydrate_data_range 現在返回 (DataFrame | None, dict_execution_log)
        hydrated_df, ticker_execution_log = yf_client.hydrate_data_range(ticker, args.start_date, args.end_date)

        # 合併 ticker 的執行日誌到總日誌中
        for date_key, ticker_daily_log_value in ticker_execution_log.items():
            if date_key not in overall_execution_log:
                overall_execution_log[date_key] = {}
            overall_execution_log[date_key].update(ticker_daily_log_value) # ticker_daily_log_value 應為 {ticker: log_info}

        if hydrated_df is not None and not hydrated_df.empty:
            print(f"資訊：標的 {ticker} 成功擷取 {len(hydrated_df)} 筆數據。準備寫入資料庫...") # 中文化
            try:
                db_manager.upsert_data(hydrated_df, table_name=args.table_name)
                print(f"資訊：標的 {ticker} 數據成功寫入資料庫。") # 中文化
            except Exception as e:
                print(f"錯誤：標的 {ticker} 數據寫入資料庫失敗: {e}") # 中文化
                # 更新 overall_execution_log 中對應日期的狀態為 db_error
                for date_str_key in pd.date_range(args.start_date, args.end_date).strftime('%Y-%m-%d'):
                    if date_str_key in overall_execution_log and ticker in overall_execution_log[date_str_key]:
                         overall_execution_log[date_str_key][ticker]['status'] = 'db_upsert_failed'
                         # 確保 message 是字串且可附加
                         base_message = overall_execution_log[date_str_key][ticker].get('message', "")
                         if not isinstance(base_message, str): base_message = str(base_message)
                         overall_execution_log[date_str_key][ticker]['message'] = base_message + f" 資料庫更新失敗: {str(e)}" # 中文化
        else:
            print(f"資訊：標的 {ticker} 未擷取到任何數據 (詳見 yfinance_client 日誌)。") # 中文化
            # overall_execution_log 應已由 yfinance_client 更新了此 ticker 的失敗狀態

        print(f"--- 標的: {ticker} 處理完畢 ---") # 中文化

    overall_end_time = datetime.now()
    task_duration_seconds = (overall_end_time - overall_start_time).total_seconds()
    print(f"\n--- 所有標的處理完成 ---") # 中文化
    print(f"任務結束時間: {overall_end_time.strftime('%Y-%m-%d %H:%M:%S')}") # 簡化時間格式
    print(f"總執行時長: {task_duration_seconds:.2f} 秒") # 中文化

    # 初始化報告生成器 (傳入合併後的日誌和分析引擎實例)
    report_gen = ReportGenerator(execution_log=overall_execution_log,
                                 analysis_engine_instance=analysis_engine)

    print("\n--- 生成市場分析報告 ---") # 中文化

    current_report_time = datetime.now() # 用於傳遞給 ReportGenerator
    final_report_str = report_gen.generate_full_report(
        overall_start_date_str=args.start_date,
        overall_end_date_str=args.end_date,
        report_generation_time=current_report_time,
        task_duration_seconds=task_duration_seconds,
        target_tickers=tickers_list,
        db_table_name=args.table_name
    )

    # 打印報告到控制台 (預覽)
    print("\n--- 市場分析報告內容預覽 ---")
    preview_lines = final_report_str.splitlines()[:30]
    for line in preview_lines:
        print(line)
    if len(final_report_str.splitlines()) > 30:
        print("... (報告內容過長，已截斷預覽) ...")

    # 將報告寫入檔案
    report_output_dir = os.path.join("data_workspace", "reports") # 定義報告輸出目錄
    os.makedirs(report_output_dir, exist_ok=True) # 確保目錄存在

    # 使用提前定義的時間戳 report_generation_time_for_filename
    print(f"DEBUG: Type of report_generation_time_for_filename before strftime: {type(report_generation_time_for_filename)}")
    report_filename_dt_str = report_generation_time_for_filename.strftime('%Y%m%d_%H%M%S')
    report_filename = f"market_analysis_report_{report_filename_dt_str}.md"
    report_filepath = os.path.join(report_output_dir, report_filename)

    try:
        with open(report_filepath, "w", encoding="utf-8") as f:
            f.write(final_report_str)
        print(f"\n報告已成功儲存至：{report_filepath}")
    except IOError as e:
        print(f"\n錯誤：儲存報告至檔案失敗：{e}")

    print("\n--- 每日市場洞察報告引擎任務執行完畢 ---")

if __name__ == "__main__":
    # print(f"DEBUG: Current CWD for __main__ in daily_market_analyzer/run.py: {os.getcwd()}") # 移除調試信息
    # print(f"DEBUG: Current sys.path for __main__ in daily_market_analyzer/run.py: {sys.path}") # 移除調試信息

    # 移除 __main__ 中的延遲導入，因為已在頂部導入
    # if 'YFinanceClient' not in globals() or 'AnalysisEngine' not in globals(): # 檢查新加入的 AnalysisEngine
    #     try:
    #         # 更新導入路徑以匹配新的應用名稱
    #         from apps.daily_market_analyzer.yfinance_client import YFinanceClient
    #         from apps.daily_market_analyzer.db_manager import DBManager
    #         from apps.daily_market_analyzer.analysis_engine import AnalysisEngine
    #         from apps.daily_market_analyzer.report_generator import ReportGenerator
    #         # print("DEBUG: Late imports in daily_market_analyzer __main__ successful.") # 移除調試信息
    #     except ModuleNotFoundError as e:
    #         print(f"ERROR: Late ModuleNotFoundError in daily_market_analyzer __main__: {e}") # 中文化

    main()
