# -*- coding: utf-8 -*-
"""
分析引擎 for 每日市場分析儀。
負責從資料庫提取數據並計算每日市場指標。
"""
import pandas as pd
from datetime import datetime # 需要 datetime 來處理日期字串轉換 (雖然 DBManager 可能已處理)

# 假設 db_manager.py 與此檔案在同一目錄下，或者已在 sys.path 中
# from .db_manager import DBManager # 使用相對導入，如果它們是同一個 package 的一部分

class AnalysisEngine:
    def __init__(self, db_manager_instance): # 修改參數名以清晰表示是實例
        """
        初始化分析引擎 (AnalysisEngine)。

        Args:
            db_manager_instance: DBManager 的一個實例，用於資料庫查詢。
        """
        self.db_manager = db_manager_instance # 修改屬性名以匹配參數
        print("資訊：分析引擎 (AnalysisEngine) 初始化完畢。")

    def analyze_daily_ticker_data(self, ticker: str, date_str: str, table_name: str = "market_ohlcv_analyzer") -> dict:
        """
        分析單一標的在某一天的市場表現。

        Args:
            ticker (str): 股票代碼。
            date_str (str): 要分析的日期 (YYYY-MM-DD)。
            table_name (str): 包含 OHLCV 數據的資料表名稱。

        Returns:
            dict: 包含分析指標的字典，或在無數據時返回
                  {"status": "no_data", "message": "..."}。
                  指標包括: close, prev_close, change_pct, range_pct, high, low, volume。
        """
        # print(f"調試：分析引擎：正在分析標的 {ticker} 日期 {date_str} (資料表: {table_name})")

        # 從 DBManager 獲取當日數據
        # 假設 query_data_for_day 返回的 DataFrame 的 index 是 DatetimeIndex (UTC)
        daily_data_df = self.db_manager.query_data_for_day(ticker, date_str, table_name)

        if daily_data_df.empty:
            # print(f"調試：分析引擎：標的 {ticker} 在日期 {date_str} 無數據。")
            return {"status": "no_data", "message": f"無 {ticker} 在 {date_str} 的數據。"}

        # 計算指標
        # 當日收盤價：取當日數據的最後一筆 'close'
        # 假設 daily_data_df 已按時間升序排列 (由 query_data_for_day 保證)
        close_price = daily_data_df['close'].iloc[-1]

        # 獲取前一日收盤價
        prev_close_price = self.db_manager.query_previous_day_close(ticker, date_str, table_name)

        price_change_pct = 0.0
        if prev_close_price is not None:
            if prev_close_price != 0:
                price_change_pct = ((close_price - prev_close_price) / prev_close_price) * 100
            elif close_price > 0: # prev_close is 0, current_close > 0
                price_change_pct = float('inf') # 表示極大變化或從0開始的增長
        # 如果 prev_close_price is None (例如，新上市股票的第一天)，則 price_change_pct 保持 0.0

        high_price = daily_data_df['high'].max()
        low_price = daily_data_df['low'].min()

        volatility_range_pct = 0.0
        if low_price != 0 : # 避免除以零
            volatility_range_pct = ((high_price - low_price) / low_price) * 100
        elif high_price > 0: # low_price is 0, high_price > 0
            volatility_range_pct = float('inf')


        total_volume = daily_data_df['volume'].sum()

        # 準備返回的結果
        analysis_result = {
            "status": "success",
            "close": f"{close_price:.2f}",
            "prev_close": f"{prev_close_price:.2f}" if prev_close_price is not None else "N/A",
            "change_pct": f"{price_change_pct:+.2f}%" if price_change_pct != float('inf') else "新生或極大變化",
            "high": f"{high_price:.2f}",
            "low": f"{low_price:.2f}",
            "range_pct": f"{volatility_range_pct:.2f}%" if volatility_range_pct != float('inf') else "極大波動或從0開始",
            "volume": f"{total_volume:,.0f}"
        }
        # print(f"調試：分析引擎：標的 {ticker} 在日期 {date_str} 的分析結果: {analysis_result}")
        return analysis_result

if __name__ == '__main__':
    print("--- 分析引擎 (AnalysisEngine) 測試 (需要搭配模擬的 DBManager) ---")

    # 為了能獨立運行此測試，需要能夠導入 DBManager
    # 這假設 db_manager.py 與 analysis_engine.py 在同一目錄下
    # 或者 daily_market_analyzer 套件已在 sys.path 中
    import sys
    import os
    # Adjust path to import DBManager from the same directory if run directly
    current_dir = os.path.dirname(os.path.abspath(__file__))
    if current_dir not in sys.path:
        sys.path.append(current_dir)

    try:
        from db_manager import DBManager # Try to import directly
    except ImportError:
        print("無法直接導入 DBManager，測試將使用純 Mock。這在實際執行 run.py 時應能正常工作。")
        # Fallback to a simple Mock class if DBManager cannot be imported (e.g. path issues in isolated test)
        class DBManager: # Simple mock for testing structure
            def __init__(self, db_path): self.db_path = db_path
            def query_data_for_day(self, ticker, date_str, table_name): return pd.DataFrame()
            def query_previous_day_close(self, ticker, current_date_str, table_name): return None


    # 模擬 DBManager
    class MockDBManagerForEngineTest:
        def query_data_for_day(self, ticker, date_str, table_name="default_table"):
            print(f"模擬資料庫(引擎測試): query_data_for_day 針對 {ticker}, {date_str}")
            if ticker == "AAPL" and date_str == "2024-07-25":
                data = {
                    'open': [150.0, 151.0, 150.5], 'high': [152.0, 151.5, 151.0],
                    'low': [149.0, 150.0, 149.5], 'close': [151.5, 150.8, 150.9],
                    'volume': [100000, 120000, 110000],
                    'interval': ['5m', '5m', '5m'] # Interval might be useful for context
                }
                # Create a DatetimeIndex for a single day
                idx = pd.to_datetime([f"{date_str} 09:30:00", f"{date_str} 09:35:00", f"{date_str} 16:00:00"]).tz_localize('UTC')
                return pd.DataFrame(data, index=idx)
            return pd.DataFrame() # Return empty DataFrame for other cases

        def query_previous_day_close(self, ticker, current_date_str, table_name="default_table"):
            print(f"模擬資料庫(引擎測試): query_previous_day_close 針對 {ticker}, {current_date_str}")
            if ticker == "AAPL" and current_date_str == "2024-07-25":
                return 149.80 # Previous day's close for AAPL
            return None

    mock_db_instance = MockDBManagerForEngineTest()
    engine = AnalysisEngine(db_manager_instance=mock_db_instance)

    print("\n--- 測試 analyze_daily_ticker_data (有數據) ---")
    analysis_results = engine.analyze_daily_ticker_data("AAPL", "2024-07-25")
    print(f"標的 AAPL 在 2024-07-25 的分析結果: {analysis_results}")

    assert analysis_results['status'] == 'success'
    assert analysis_results['close'] == '150.90' # Last 'close' in mock data
    assert analysis_results['prev_close'] == '149.80'
    # Change = (150.90 - 149.80) / 149.80 * 100 = 1.10 / 149.80 * 100 = 0.7343...%
    assert analysis_results['change_pct'] == '+0.73%'
    assert analysis_results['high'] == '152.00' # Max 'high'
    assert analysis_results['low'] == '149.00'  # Min 'low'
    # Range = (152.00 - 149.00) / 149.00 * 100 = 3.00 / 149.00 * 100 = 2.0134...%
    assert analysis_results['range_pct'] == '2.01%'
    assert analysis_results['volume'] == '330,000' # Sum of 'volume'

    print("\n--- 測試 analyze_daily_ticker_data (無數據) ---")
    analysis_no_data = engine.analyze_daily_ticker_data("MSFT", "2024-07-25")
    print(f"標的 MSFT 在 2024-07-25 的分析結果: {analysis_no_data}")
    assert analysis_no_data['status'] == 'no_data'

    print("\n--- 分析引擎 (AnalysisEngine) 測試完畢 ---")
