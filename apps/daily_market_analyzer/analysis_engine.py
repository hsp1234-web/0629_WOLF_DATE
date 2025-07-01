# -*- coding: utf-8 -*-
"""
分析引擎 for 每日市場分析儀。
負責從資料庫提取數據並計算每日市場指標。
"""
import pandas as pd
from datetime import datetime # 需要 datetime 來處理日期字串轉換 (雖然 DBManager 可能已處理)

import logging # 新增 logging 模組

# 假設 db_manager.py 與此檔案在同一目錄下，或者已在 sys.path 中
# from .db_manager import DBManager # 使用相對導入，如果它們是同一個 package 的一部分

class AnalysisEngine:
    def __init__(self, db_manager_instance, report_interval='1d'): # 修改簽名以接收 report_interval
        """
        初始化分析引擎 (AnalysisEngine)。

        Args:
            db_manager_instance: DBManager 的一個實例，用於資料庫查詢。
            report_interval (str): 用於報告分析的時間顆粒度。
        """
        self.db_manager = db_manager_instance # 修改屬性名以匹配參數
        self.report_interval = report_interval # 新增此屬性
        self.logger = logging.getLogger(self.__class__.__name__) # 標準日誌記錄器
        # 使用 self.logger 進行日誌記錄
        self.logger.info(f"分析引擎 (AnalysisEngine) 初始化完畢。報告顆粒度設定為: {self.report_interval}")

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
        # 並且包含 'datetime', 'open', 'high', 'low', 'close', 'volume' 欄位
        raw_daily_data_df = self.db_manager.query_data_for_day(ticker, date_str, table_name)

        if raw_daily_data_df.empty:
            self.logger.info(f"標的 {ticker} 在日期 {date_str} 無原始數據。")
            return {"status": "no_data", "message": f"無 {ticker} 在 {date_str} 的原始數據。"}

        # 數據重採樣 (Resampling)
        # 確保索引是 datetime 類型
        # db_manager.query_data_for_day 應該已經返回以 datetime 為索引的 DataFrame
        # 如果不是，則需要取消註解以下兩行：
        # raw_daily_data_df['datetime'] = pd.to_datetime(raw_daily_data_df['datetime'])
        # raw_daily_data_df.set_index('datetime', inplace=True)

        # 定義聚合規則
        aggregation_rules = {
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }

        # 執行重採樣
        try:
            # 確保索引是 DatetimeIndex
            if not isinstance(raw_daily_data_df.index, pd.DatetimeIndex):
                # 嘗試轉換 'datetime' 欄 (如果存在) 或索引
                if 'datetime' in raw_daily_data_df.columns:
                    raw_daily_data_df['datetime'] = pd.to_datetime(raw_daily_data_df['datetime'])
                    raw_daily_data_df.set_index('datetime', inplace=True)
                else: # 假設索引可以直接轉換
                    raw_daily_data_df.index = pd.to_datetime(raw_daily_data_df.index)

            resampled_df = raw_daily_data_df.resample(self.report_interval).agg(aggregation_rules)
            resampled_df.dropna(how='all', inplace=True) # 移除所有值均為 NaN 的行 (因重採樣產生)
        except Exception as e:
            self.logger.error(f"標的 {ticker} 在日期 {date_str} 進行數據重採樣至 {self.report_interval} 時失敗: {e}")
            return {"status": "resample_error", "message": f"數據重採樣失敗: {e}"}

        if resampled_df.empty:
            self.logger.info(f"標的 {ticker} 在日期 {date_str} 重採樣至 {self.report_interval} 後數據為空。")
            return {"status": "no_data_after_resample", "message": f"重採樣至 {self.report_interval} 後無數據。"}

        # 計算指標: 使用重採樣後的數據 (resampled_df)
        # 當日收盤價、最高價、最低價、總成交量直接從最後一個有效時間段的聚合結果中獲取
        # 注意：對於 '1d' 或更長間隔，通常只有一行數據。
        # 對於日內間隔 (如 '1h', '4h')，我們可能需要考慮如何定義「當日」的這些值。
        # 此處假設：
        # - close_price 是最後一個 interval 的收盤價。
        # - high_price 是所有 intervals 中的最高價。
        # - low_price 是所有 intervals 中的最低價。
        # - total_volume 是所有 intervals 的成交量總和。

        latest_interval_data = resampled_df.iloc[-1]
        close_price = latest_interval_data['close']

        # 整體高低價和總成交量應基於重採樣後的數據計算
        high_price = resampled_df['high'].max()
        low_price = resampled_df['low'].min()
        total_volume = resampled_df['volume'].sum()

        # 獲取前一日收盤價 (此邏輯可能需要根據 report_interval 調整，但目前保持不變)
        # prev_close_price 的獲取邏輯，基於的是「前一個交易日」的收盤價，
        # 這與 report_interval 的日內細分無關。
        prev_close_price = self.db_manager.query_previous_day_close(ticker, date_str, table_name)

        price_change_pct = 0.0
        if prev_close_price is not None and prev_close_price != 0:
            price_change_pct = ((close_price - prev_close_price) / prev_close_price) * 100
        elif prev_close_price is not None and prev_close_price == 0 and close_price > 0:
            price_change_pct = float('inf')
        # 如果 prev_close_price is None，price_change_pct 保持 0.0

        volatility_range_pct = 0.0
        if low_price != 0:
            volatility_range_pct = ((high_price - low_price) / low_price) * 100
        elif high_price > 0: # low_price is 0, high_price > 0
            volatility_range_pct = float('inf')

        # 準備返回的結果
        analysis_result = {
            "status": "success",
            "close": f"{close_price:.2f}" if pd.notna(close_price) else "N/A",
            "prev_close": f"{prev_close_price:.2f}" if prev_close_price is not None else "N/A",
            "change_pct": f"{price_change_pct:+.2f}%" if price_change_pct != float('inf') else "新生或極大變化",
            "high": f"{high_price:.2f}" if pd.notna(high_price) else "N/A",
            "low": f"{low_price:.2f}" if pd.notna(low_price) else "N/A",
            "range_pct": f"{volatility_range_pct:.2f}%" if volatility_range_pct != float('inf') else "極大波動或從0開始",
            "volume": f"{total_volume:,.0f}" if pd.notna(total_volume) else "N/A"
        }
        # self.logger.debug(f"標的 {ticker} 在日期 {date_str} (顆粒度 {self.report_interval}) 的分析結果: {analysis_result}")

        # 直接定義數值型變量，用於計算和解讀
        numeric_change_pct = 0.0
        numeric_vol_range_pct = 0.0

        if prev_close_price is not None:
            if prev_close_price != 0:
                numeric_change_pct = ((close_price - prev_close_price) / prev_close_price) * 100
            elif close_price > 0: # prev_close is 0, current_close > 0
                numeric_change_pct = float('inf')

        # high_price, low_price, total_volume 已從 resampled_df 計算得到 (這些將不再在此計算)

        # 【核心修改 v13.1】
        # 不再計算單一匯總指標或市場解讀。
        # 直接返回包含重採樣 DataFrame 的結果。

        # numeric_change_pct 和 numeric_vol_range_pct 的計算也移除，
        # 這些將根據需要在 ReportGenerator 中針對 '1d' 情況計算。

        # 準備返回的結果
        # analysis_result = {
        #     "status": "success",
        #     "close": f"{close_price:.2f}" if pd.notna(close_price) else "N/A",
        #     "prev_close": f"{prev_close_price:.2f}" if prev_close_price is not None else "N/A",
        #     "change_pct": f"{price_change_pct:+.2f}%" if price_change_pct != float('inf') else "新生或極大變化",
        #     "high": f"{high_price:.2f}" if pd.notna(high_price) else "N/A",
        #     "low": f"{low_price:.2f}" if pd.notna(low_price) else "N/A",
        #     "range_pct": f"{volatility_range_pct:.2f}%" if volatility_range_pct != float('inf') else "極大波動或從0開始",
        #     "volume": f"{total_volume:,.0f}" if pd.notna(total_volume) else "N/A"
        # }
        # self.logger.debug(f"標的 {ticker} 在日期 {date_str} (顆粒度 {self.report_interval}) 的分析結果: {analysis_result}")


        # # 準備用於解讀的輸入 (移除)
        # interpretation_input = { ... }
        # interpretation_str = self._generate_market_interpretation(interpretation_input) (移除)
        # analysis_result["interpretation"] = interpretation_str (移除)

        # 返回新的資料結構
        return {"status": "success", "dataframe": resampled_df}

    def _generate_market_interpretation(self, internal_analysis_metrics: dict) -> str:
        """
        根據內部數值型分析指標，生成一句話的市場解讀。
        【v13.1 注意】此方法可能不再由此類直接調用，或其邏輯將遷移至 ReportGenerator。
        暫時保留此方法，但其調用點已移除。
        """
        if internal_analysis_metrics.get('status') != 'success':
            return "數據不足，無法解讀。"

        try:
            change = internal_analysis_metrics.get('change_pct_num', 0.0)
            vol_range = internal_analysis_metrics.get('range_pct_num', 0.0)

            # 處理 inf 的情況
            if change == float('inf') or change == float('-inf'):
                return "價格出現極大變動。"
            if vol_range == float('inf'):
                return "日內波動劇烈，風險加劇。"

        except Exception: # 捕獲可能的轉換錯誤，儘管這裡應該是數值了
            return "解讀時指標處理錯誤。"

        interpretation = "市場常規波動。" # 默認解讀

        # 規則引擎 (優先級可以根據需要調整)
        if vol_range > 3.0: # 優先判斷劇烈波動
            interpretation = "日內波動劇烈，風險加劇。"
            if change > 1.0: # 劇烈波動且上漲
                interpretation += " 偏多方積極。"
            elif change < -1.0: # 劇烈波動且下跌
                interpretation += " 偏空方主導。"
        elif change > 1.5:
            interpretation = "強勢上漲，動能顯著。"
        elif change < -1.5:
            interpretation = "顯著下跌，市場承壓。"
        elif vol_range < 0.5 and abs(change) < 0.2:
            interpretation = "窄幅整理，多空僵持。"
        elif abs(change) < 0.5 and vol_range < 1.0 : # 溫和波動
             interpretation = "溫和波動，趨勢不明。"

        return interpretation

    def analyze_daily_options_data(self, date_str: str) -> dict | None:
        """
        分析指定日期的整體選擇權市場指標。
        (目前為模擬實現)

        Args:
            date_str (str): 要分析的日期 (YYYY-MM-DD)。

        Returns:
            dict | None: 包含選擇權指標的字典，或在無數據/未實現時返回 None 或特定狀態。
        """
        print(f"資訊 (AnalysisEngine): 正在為日期 {date_str} 分析選擇權數據 (模擬)。")

        # 模擬資料庫查詢或數據處理
        # TODO: 在真實場景中，這裡會調用 self.db_manager 的方法查詢選擇權相關數據
        # 例如: options_summary = self.db_manager.query_daily_options_summary(date_str)

        # 模擬不同日期的返回結果
        if date_str == "2024-06-03": # 假設這天有選擇權數據
            return {
                "status": "success",
                "put_call_volume_ratio": 0.78,
                "put_call_oi_ratio": 0.95,
                "message": "選擇權數據分析完成。"
            }
        elif date_str == "2024-07-25": # 假設這天也有
             return {
                "status": "success",
                "put_call_volume_ratio": 1.05, # 看跌期權更活躍
                "put_call_oi_ratio": 1.10,
                "message": "選擇權數據分析完成，市場情緒偏謹慎。"
            }
        else: # 其他日期模擬無數據
            return {
                "status": "no_data",
                "message": f"日期 {date_str} 無可用選擇權數據進行分析。"
            }

if __name__ == '__main__':
    print("--- 分析引擎 (AnalysisEngine) 測試 (需要搭配模擬的 DBManager) ---")

    import sys
    import os
    current_dir = os.path.dirname(os.path.abspath(__file__))
    if current_dir not in sys.path:
        sys.path.append(current_dir)

    try:
        from db_manager import DBManager
    except ImportError:
        print("無法直接導入 DBManager，測試將使用純 Mock。這在實際執行 run.py 時應能正常工作。")
        class DBManager:
            def __init__(self, db_path): self.db_path = db_path
            def query_data_for_day(self, ticker, date_str, table_name): return pd.DataFrame()
            def query_previous_day_close(self, ticker, current_date_str, table_name): return None

    class MockDBManagerForEngineTest:
        def query_data_for_day(self, ticker, date_str, table_name="default_table"):
            # print(f"模擬資料庫(引擎測試): query_data_for_day 針對 {ticker}, {date_str}")
            if ticker == "AAPL" and date_str == "2024-07-25":
                data = {
                    'open': [150.0, 151.0, 150.5], 'high': [152.0, 151.5, 151.0],
                    'low': [149.0, 150.0, 149.5], 'close': [151.5, 150.8, 150.9],
                    'volume': [100000, 120000, 110000],
                    'interval': ['5m', '5m', '5m']
                }
                idx = pd.to_datetime([f"{date_str} 09:30:00", f"{date_str} 09:35:00", f"{date_str} 16:00:00"]).tz_localize('UTC')
                return pd.DataFrame(data, index=idx)
            return pd.DataFrame()

        def query_previous_day_close(self, ticker, current_date_str, table_name="default_table"):
            # print(f"模擬資料庫(引擎測試): query_previous_day_close 針對 {ticker}, {current_date_str}")
            if ticker == "AAPL" and current_date_str == "2024-07-25":
                return 149.80
            return None

    mock_db_instance = MockDBManagerForEngineTest()
    engine = AnalysisEngine(db_manager_instance=mock_db_instance)

    print("\n--- 測試 analyze_daily_ticker_data (有數據) ---")
    analysis_results = engine.analyze_daily_ticker_data("AAPL", "2024-07-25")
    print(f"標的 AAPL 在 2024-07-25 的分析結果: {analysis_results}")

    assert analysis_results['status'] == 'success'
    assert 'interpretation' in analysis_results
    print(f"解讀 (AAPL): {analysis_results['interpretation']}")
    # 預期: change = +0.73%, range = 2.01% => "市場常規波動。"
    assert analysis_results['interpretation'] == "市場常規波動。"


    print("\n--- 測試 analyze_daily_ticker_data (無數據) ---")
    analysis_no_data = engine.analyze_daily_ticker_data("MSFT", "2024-07-25")
    print(f"標的 MSFT 在 2024-07-25 的分析結果: {analysis_no_data}")
    assert analysis_no_data['status'] == 'no_data'
    assert analysis_no_data['interpretation'] == "數據不足，無法解讀。"

    class MockDBForInterpretation(MockDBManagerForEngineTest):
        def query_data_for_day(self, ticker, date_str, table_name="default_table"):
            idx = pd.to_datetime([f"{date_str} 16:00:00"]).tz_localize('UTC')
            if ticker == "STRONG_UP" and date_str == "2024-07-26":
                return pd.DataFrame({'open': [100], 'high': [103], 'low': [100], 'close': [102], 'volume': [1000]}, index=idx)
            if ticker == "SHARP_DROP" and date_str == "2024-07-26":
                return pd.DataFrame({'open': [100], 'high': [100], 'low': [97], 'close': [98], 'volume': [1000]}, index=idx)
            if ticker == "NARROW" and date_str == "2024-07-26":
                return pd.DataFrame({'open': [100], 'high': [100.2], 'low': [99.8], 'close': [100.1], 'volume': [1000]}, index=idx)
            if ticker == "VOLATILE" and date_str == "2024-07-26":
                return pd.DataFrame({'open': [100], 'high': [104], 'low': [99], 'close': [100.5], 'volume': [1000]}, index=idx)
            return super().query_data_for_day(ticker, date_str, table_name)

        def query_previous_day_close(self, ticker, current_date_str, table_name="default_table"):
            if ticker in ["STRONG_UP", "SHARP_DROP", "NARROW", "VOLATILE"] and current_date_str == "2024-07-26":
                return 100.0
            return super().query_previous_day_close(ticker, current_date_str, table_name)

    mock_db_interpret = MockDBForInterpretation()
    engine_interpret = AnalysisEngine(db_manager_instance=mock_db_interpret)

    print("\n--- 測試市場解讀規則 ---")
    res_strong_up = engine_interpret.analyze_daily_ticker_data("STRONG_UP", "2024-07-26")
    print(f"STRONG_UP 解讀: {res_strong_up['interpretation']}")
    assert res_strong_up['interpretation'] == "強勢上漲，動能顯著。"

    res_sharp_drop = engine_interpret.analyze_daily_ticker_data("SHARP_DROP", "2024-07-26")
    print(f"SHARP_DROP 解讀: {res_sharp_drop['interpretation']}")
    assert res_sharp_drop['interpretation'] == "顯著下跌，市場承壓。"

    res_narrow = engine_interpret.analyze_daily_ticker_data("NARROW", "2024-07-26")
    print(f"NARROW 解讀: {res_narrow['interpretation']}")
    assert res_narrow['interpretation'] == "窄幅整理，多空僵持。"

    res_volatile = engine_interpret.analyze_daily_ticker_data("VOLATILE", "2024-07-26")
    print(f"VOLATILE 解讀: {res_volatile['interpretation']}")
    assert res_volatile['interpretation'] == "日內波動劇烈，風險加劇。" # change 0.5%, range 5.05% -> 波動優先

    # 測試特殊指標情況 (直接調用內部方法)
    interp_special_change_inf = engine_interpret._generate_market_interpretation({
        "status": "success", "change_pct_num": float('inf'), "range_pct_num": 1.00
    })
    print(f"無限大變化解讀: {interp_special_change_inf}")
    assert interp_special_change_inf == "價格出現極大變動。"

    interp_special_range_inf = engine_interpret._generate_market_interpretation({
        "status": "success", "change_pct_num": 0.50, "range_pct_num": float('inf')
    })
    print(f"無限大波動解讀: {interp_special_range_inf}")
    assert interp_special_range_inf == "日內波動劇烈，風險加劇。"

    print("\n--- 測試 analyze_daily_options_data ---")
    options_res_with_data = engine_interpret.analyze_daily_options_data("2024-06-03")
    print(f"選擇權數據 (2024-06-03): {options_res_with_data}")
    assert options_res_with_data and options_res_with_data['status'] == 'success'
    assert 'put_call_volume_ratio' in options_res_with_data

    options_res_no_data = engine_interpret.analyze_daily_options_data("2024-06-01")
    print(f"選擇權數據 (2024-06-01): {options_res_no_data}")
    assert options_res_no_data and options_res_no_data['status'] == 'no_data'

    print("\n--- 分析引擎 (AnalysisEngine) 測試完畢 ---")
