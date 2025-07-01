# -*- coding: utf-8 -*-
"""
高頻數據擷取器執行入口。
"""
import argparse
import sys
import os

def setup_project_path():
    """
    設定專案路徑，確保可以正確匯入其他模組。
    此處為SOP規範的路徑自我校正樣板碼。
    """
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

setup_project_path()

from apps.hf_data_ingestor.yfinance_client import YFinanceClient
from apps.hf_data_ingestor.db_manager import DBManager

def main():
    """
    主執行函數。
    解析命令列參數，並協調 YFinanceClient 和 DBManager 完成數據擷取與儲存。
    """
    parser = argparse.ArgumentParser(description="高頻數據擷取器 (自適應版)")
    parser.add_argument("--tickers", required=True, help="要抓取的標的，以逗號分隔")
    # 【修改】這個 interval 現在代表「起始嘗試區間」
    parser.add_argument("--start-interval", default="1m", help="起始嘗試的數據顆粒度 (e.g., 1m, 5m, 1h)")
    parser.add_argument("--db-path", default="data_workspace/market_data.duckdb", help="資料庫檔案路徑")
    args = parser.parse_args()

    client = YFinanceClient()
    # 根據草案，db_manager 變數名為 db，但現有代碼是 db_manager，為保持一致性，沿用 db_manager
    db_manager = DBManager(args.db_path)
    db_manager.create_futures_table() # 確保表存在

    tickers_list = [ticker.strip() for ticker in args.tickers.split(',')]

    print(f"INFO: 開始自適應擷取 {len(tickers_list)} 個標的之數據: {', '.join(tickers_list)}")

    for ticker in tickers_list:
        print(f"--- 正在自適應處理 {ticker} (從 {args.start_interval} 開始) ---")
        try:
            # 【核心修改】調用新的自適應方法
            data_df, final_interval = client.fetch_data_adaptively(ticker, args.start_interval)

            # 如果最終成功獲取到數據
            if data_df is not None and final_interval is not None:
                # 在存入資料庫前，可以加上 final_interval 作為一個新的欄位
                data_df['interval'] = final_interval
                # 注意：草案中的 db.upsert_data，此處對應 db_manager.upsert_data
                # 舊的 data_df.name = ticker 賦值已移除，假設 db_manager.upsert_data 不需要它
                db_manager.upsert_data(data_df, "futures_ohlcv")
                print(f"成功儲存 {ticker} 的 {len(data_df)} 筆數據 (最終區間: {final_interval})。")
            else:
                print(f"未能為 {ticker} 擷取到任何數據。")
        except Exception as e:
            # 保留通用錯誤捕獲，以防 fetch_data_adaptively 內部未捕獲的意外錯誤
            print(f"錯誤: 自適應處理 {ticker} 時發生未預期錯誤: {e}")

    print("INFO: 所有標的處理完畢。")

if __name__ == "__main__":
    main()
