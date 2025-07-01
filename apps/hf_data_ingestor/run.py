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
    parser = argparse.ArgumentParser(description="高頻數據擷取器 - 從 yfinance 獲取市場數據並存入 DuckDB。")
    parser.add_argument("--tickers", required=True, help="要抓取的標的，以逗號分隔 (例如: \"AAPL,MSFT,^GSPC\")。")
    parser.add_argument("--interval", default="5m", help="數據顆粒度 (例如: 1m, 5m, 1h, 1d)。預設為 5m。")
    parser.add_argument("--db-path", default="data_workspace/market_data.duckdb", help="DuckDB 資料庫檔案路徑。預設為 data_workspace/market_data.duckdb。")

    args = parser.parse_args()

    client = YFinanceClient()
    db_manager = DBManager(args.db_path)
    db_manager.create_futures_table() # 確保目標資料表存在

    tickers_list = [ticker.strip() for ticker in args.tickers.split(',')]

    print(f"INFO: 開始擷取 {len(tickers_list)} 個標的之數據: {', '.join(tickers_list)}")

    for ticker in tickers_list:
        print(f"--- 正在處理 {ticker} (間隔: {args.interval}) ---")
        try:
            data_df = client.fetch_data(ticker, args.interval)
            if data_df is not None and not data_df.empty:
                # 為 DataFrame 賦予 'name' 屬性，以便 db_manager 使用
                data_df.name = ticker
                db_manager.upsert_data(data_df, "futures_ohlcv")
                print(f"INFO: 成功儲存 {ticker} 的 {len(data_df)} 筆數據。")
            elif data_df is None:
                print(f"警告: 抓取 {ticker} 數據時發生錯誤，未返回任何數據。")
            else: # data_df.empty
                print(f"INFO: {ticker} 在指定期間內沒有可用的新數據。")
        except Exception as e:
            print(f"錯誤: 處理 {ticker} 時發生未預期錯誤: {e}")

    print("INFO: 所有標的處理完畢。")

if __name__ == "__main__":
    main()
