# -*- coding: utf-8 -*-
"""
Analysis Engine for Daily Market Analyzer (v12.0).
"""
import pandas as pd
from datetime import datetime

# from .db_manager import DBManager # Assuming in the same package

class AnalysisEngine:
    def __init__(self, db_manager_instance):
        self.db_manager = db_manager_instance
        print("INFO: AnalysisEngine (v12.0) 初始化完畢。")

    def analyze_daily_ticker_data(self, ticker: str, date_str: str, table_name: str = "market_ohlcv_analyzer") -> dict:
        # print(f"DEBUG: AnalysisEngine: Analyzing {ticker} for {date_str}")
        daily_data_df = self.db_manager.query_data_for_day(ticker, date_str, table_name)

        if daily_data_df.empty:
            return {"status": "no_data", "message": f"無 {ticker} 在 {date_str} 的數據。"}

        close_price = daily_data_df['close'].iloc[-1]
        prev_close_price = self.db_manager.query_previous_day_close(ticker, date_str, table_name)

        price_change_pct = 0.0
        if prev_close_price is not None:
            if prev_close_price != 0:
                price_change_pct = ((close_price - prev_close_price) / prev_close_price) * 100
            elif close_price > 0:
                price_change_pct = float('inf')

        high_price = daily_data_df['high'].max()
        low_price = daily_data_df['low'].min()

        volatility_range_pct = 0.0
        if low_price != 0 :
            volatility_range_pct = ((high_price - low_price) / low_price) * 100
        elif high_price > 0:
            volatility_range_pct = float('inf')

        total_volume = daily_data_df['volume'].sum()

        # 決定此日期的主要 interval (若有多種，優先選顆粒度粗的，例如 '1d')
        # 這裡假設 daily_data_df 中 interval 欄位的值對於當日數據是一致的，
        # 或者取用出現頻次最高的 interval，或日線 interval。
        # 簡化：取第一筆記錄的 interval。在實際應用中，如果一天內有多種 interval 數據被查詢到，
        # 可能需要更複雜的邏輯來決定哪個是「代表性」的 interval。
        # 不過，由於 analysis_engine 是基於 DBManager 已存儲的數據，
        # 而 YFinanceClient 在存儲時已確定了最優 interval，所以這裡的 interval 應該是確定的。
        representative_interval = daily_data_df['interval'].iloc[0] if 'interval' in daily_data_df.columns else "N/A"


        analysis_result = {
            "status": "success",
            "interval": representative_interval, # 新增 interval 資訊
            "close": f"{close_price:.2f}",
            "prev_close": f"{prev_close_price:.2f}" if prev_close_price is not None else "N/A",
            "change_pct": f"{price_change_pct:+.2f}%" if price_change_pct != float('inf') else "新生或極大變化",
            "high": f"{high_price:.2f}",
            "low": f"{low_price:.2f}",
            "range_pct": f"{volatility_range_pct:.2f}%" if volatility_range_pct != float('inf') else "極大波動或從0開始",
            "volume": f"{total_volume:,.0f}"
        }
        return analysis_result

if __name__ == '__main__':
    print("--- AnalysisEngine (v12.0) 測試 ---")
    # This would require a mock or real DBManager setup.
    # For now, just ensuring the class can be instantiated.
    class MockDBManagerForEngine:
        def query_data_for_day(self, ticker, date_str, table_name):
            if ticker == "MOCK" and date_str == "2024-01-01":
                data = {'datetime': [pd.Timestamp('2024-01-01 10:00:00', tz='UTC')],
                        'open': [100], 'high': [105], 'low': [99], 'close': [102], 'volume': [1000], 'interval': ['1d']}
                return pd.DataFrame(data).set_index('datetime')
            return pd.DataFrame()
        def query_previous_day_close(self, ticker, date_str, table_name, max_lookback_days=30):
            if ticker == "MOCK" and date_str == "2024-01-01": return 100.0
            return None

    mock_db = MockDBManagerForEngine()
    engine = AnalysisEngine(db_manager_instance=mock_db)
    results = engine.analyze_daily_ticker_data("MOCK", "2024-01-01")
    print(f"Mock Analysis Results: {results}")
    assert results['status'] == 'success'
    assert results['close'] == '102.00'
    print("--- AnalysisEngine (v12.0) 測試完畢 ---")
