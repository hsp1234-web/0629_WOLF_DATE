# -*- coding: utf-8 -*-
"""
整合測試 for apps.data_hydrator.run

使用 unittest.mock 來模擬外部依賴 (YFinanceClient, DBManager, ReportGenerator)，
專注於測試 run.py 的主流程和協調邏輯。
"""
import unittest
from unittest.mock import patch, MagicMock, call
import pandas as pd
import sys
import os
from datetime import datetime

# 確保 run.py 和其依賴可以被導入
# (這部分與 run.py 中的 setup_project_path 類似，確保測試環境也能找到模組)
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# 現在可以導入 run
from apps.daily_market_analyzer import run as daily_market_analyzer_run
# 如果 run.py 內部有全局的 YFinanceClient 等實例化，可能需要在測試中 patch 它們的模組路徑

class TestDailyMarketAnalyzerRun(unittest.TestCase): # 更新類名

    def create_mock_dataframe(self, ticker="TEST", interval="1m", num_rows=5, start_date_str="2024-01-01"):
        """輔助方法：創建一個模擬的 DataFrame，類似 hydrate_data_range 返回的格式。"""
        start_dt = datetime.strptime(start_date_str, "%Y-%m-%d")
        dates = pd.to_datetime([start_dt + timedelta(days=i) for i in range(num_rows)]).tz_localize('UTC')
        # 確保時間戳不完全相同，以便測試多筆記錄
        dates = [d.replace(hour=9, minute=30+i*5) for i, d in enumerate(dates)]

        data = {
            'open': [100 + i for i in range(num_rows)],
            'high': [102 + i for i in range(num_rows)],
            'low': [99 + i for i in range(num_rows)],
            'close': [101 + i for i in range(num_rows)],
            'volume': [10000 + i*100 for i in range(num_rows)],
            'ticker': [ticker] * num_rows,
            'interval': [interval] * num_rows
        }
        df = pd.DataFrame(data, index=pd.DatetimeIndex(dates, name='datetime'))
        return df

    def create_mock_execution_log(self, ticker, status="success", interval="1m", count=5, start_date_str="2024-01-01", num_days=1, message_override=None):
        """輔助方法：創建模擬的 execution_log。"""
        log = {}
        start_dt = datetime.strptime(start_date_str, "%Y-%m-%d")
        for i in range(num_days):
            date_key = (start_dt + timedelta(days=i)).strftime("%Y-%m-%d")
            log.setdefault(date_key, {})[ticker] = {
                "status": status,
                "interval": interval if status == "success" else None,
                "count": count if status == "success" else 0,
                "message": message_override if message_override else f"{status} for {ticker} on {date_key}"
            }
        return log

    @patch('apps.daily_market_analyzer.run.ReportGenerator')
    @patch('apps.daily_market_analyzer.run.AnalysisEngine') # 新增 Mock
    @patch('apps.daily_market_analyzer.run.DBManager')
    @patch('apps.daily_market_analyzer.run.YFinanceClient')
    @patch('apps.daily_market_analyzer.run.argparse.ArgumentParser')
    def test_main_flow_success_case(self, mock_argparse, mock_yf_client_class, mock_db_manager_class, mock_analysis_engine_class, mock_report_generator_class):
        print("\n--- 測試: test_main_flow_success_case (DailyMarketAnalyzer) ---")
        # --- 1. 設定 Mock 物件 ---

        # Mock argparse
        mock_args = MagicMock()
        mock_args.tickers = "AAPL,MSFT"
        mock_args.start_date = "2024-01-01" # 測試用日期
        mock_args.end_date = "2024-01-02"   # 縮短日期範圍以簡化 mock execution_log
        mock_args.db_path = "mock_analyzer.db"
        mock_args.table_name = "mock_analyzer_ohlcv"
        mock_args.process_uploads = False

        mock_parser_instance = MagicMock()
        mock_parser_instance.parse_args.return_value = mock_args
        mock_argparse.return_value = mock_parser_instance

        # Mock YFinanceClient instance and its method
        mock_yf_client_instance = MagicMock()
        # Mock YFinanceClient instance and its method
        mock_yf_client_instance = MagicMock()
        mock_aapl_df = self.create_mock_dataframe(ticker="AAPL", interval="1m", num_rows=2, start_date_str="2024-01-01")
        mock_msft_df = self.create_mock_dataframe(ticker="MSFT", interval="5m", num_rows=2, start_date_str="2024-01-01")

        mock_aapl_log = self.create_mock_execution_log(ticker="AAPL", interval="1m", count=1, start_date_str="2024-01-01", num_days=1)
        mock_aapl_log.update(self.create_mock_execution_log(ticker="AAPL", interval="1m", count=1, start_date_str="2024-01-02", num_days=1))

        mock_msft_log = self.create_mock_execution_log(ticker="MSFT", interval="5m", count=1, start_date_str="2024-01-01", num_days=1)
        mock_msft_log.update(self.create_mock_execution_log(ticker="MSFT", interval="5m", count=1, start_date_str="2024-01-02", num_days=1))

        def yf_hydrate_side_effect(ticker, start_date, end_date):
            if ticker == "AAPL":
                return mock_aapl_df, mock_aapl_log
            elif ticker == "MSFT":
                return mock_msft_df, mock_msft_log
            return None, {}
        mock_yf_client_instance.hydrate_data_range.side_effect = yf_hydrate_side_effect
        mock_yf_client_class.return_value = mock_yf_client_instance

        # Mock DBManager
        mock_db_manager_instance = MagicMock()
        mock_db_manager_class.return_value = mock_db_manager_instance

        # Mock AnalysisEngine
        mock_analysis_engine_instance = MagicMock()
        mock_analysis_engine_class.return_value = mock_analysis_engine_instance

        # Mock ReportGenerator
        mock_report_generator_instance = MagicMock()
        mock_report_generator_class.return_value = mock_report_generator_instance

        # --- 2. 執行被測函數 ---
        daily_market_analyzer_run.main() # 更新調用目標

        # --- 3. 驗證 (Assertions) ---
        mock_argparse.assert_called_once()
        mock_parser_instance.parse_args.assert_called_once()
        mock_yf_client_class.assert_called_once_with()

        self.assertEqual(mock_yf_client_instance.hydrate_data_range.call_count, 2)
        mock_yf_client_instance.hydrate_data_range.assert_any_call("AAPL", "2024-01-01", "2024-01-02")
        mock_yf_client_instance.hydrate_data_range.assert_any_call("MSFT", "2024-01-01", "2024-01-02")

        mock_db_manager_class.assert_called_once_with(db_path="mock_analyzer.db")
        mock_db_manager_instance.create_ohlcv_table.assert_called_once_with(table_name="mock_analyzer_ohlcv")
        self.assertEqual(mock_db_manager_instance.upsert_data.call_count, 2)
        mock_db_manager_instance.upsert_data.assert_any_call(mock_aapl_df, table_name="mock_analyzer_ohlcv")
        mock_db_manager_instance.upsert_data.assert_any_call(mock_msft_df, table_name="mock_analyzer_ohlcv")

        # 驗證 AnalysisEngine 被初始化
        mock_analysis_engine_class.assert_called_once_with(db_manager_instance=mock_db_manager_instance)

        # 驗證 ReportGenerator 初始化和調用
        # 構建預期的 overall_execution_log
        expected_overall_log = {}
        for date_key, ticker_log_val in mock_aapl_log.items():
            expected_overall_log.setdefault(date_key, {}).update(ticker_log_val)
        for date_key, ticker_log_val in mock_msft_log.items():
            expected_overall_log.setdefault(date_key, {}).update(ticker_log_val)

        mock_report_generator_class.assert_called_once_with(
            execution_log=expected_overall_log,
            analysis_engine_instance=mock_analysis_engine_instance
        )
        mock_report_generator_instance.generate_full_report.assert_called_once()

        # 驗證傳遞給 generate_full_report 的參數
        report_call_args = mock_report_generator_instance.generate_full_report.call_args[0]
        self.assertEqual(report_call_args[0], mock_args.start_date) # overall_start_date_str
        self.assertEqual(report_call_args[1], mock_args.end_date)   # overall_end_date_str
        self.assertIsInstance(report_call_args[2], datetime)        # report_generation_time
        self.assertIsInstance(report_call_args[3], float)           # task_duration_seconds
        self.assertEqual(report_call_args[4], ["AAPL", "MSFT"])     # target_tickers
        self.assertEqual(report_call_args[5], mock_args.table_name) # db_table_name


    @patch('apps.daily_market_analyzer.run.ReportGenerator') # 更新 patch 路徑
    @patch('apps.daily_market_analyzer.run.AnalysisEngine')
    @patch('apps.daily_market_analyzer.run.DBManager')
    @patch('apps.daily_market_analyzer.run.YFinanceClient')
    @patch('apps.daily_market_analyzer.run.argparse.ArgumentParser')
    def test_main_flow_one_ticker_fails(self, mock_argparse, mock_yf_client_class, mock_db_manager_class, mock_analysis_engine_class, mock_report_generator_class):
        print("\n--- 測試: test_main_flow_one_ticker_fails (DailyMarketAnalyzer) ---")
        mock_args = MagicMock()
        mock_args.tickers = "GOOD,BAD"
        mock_args.start_date = "2024-01-01"
        mock_args.end_date = "2024-01-01" # 單日測試
        mock_args.db_path = "mock_fail_analyzer.db"
        mock_args.table_name = "mock_fail_analyzer_ohlcv"
        mock_args.process_uploads = False

        mock_parser_instance = MagicMock()
        mock_parser_instance.parse_args.return_value = mock_args
        mock_argparse.return_value = mock_parser_instance

        mock_yf_client_instance = MagicMock()
        mock_good_df = self.create_mock_dataframe(ticker="GOOD", interval="1d", num_rows=1, start_date_str="2024-01-01")
        mock_good_log = self.create_mock_execution_log(ticker="GOOD", interval="1d", count=1, start_date_str="2024-01-01")
        mock_bad_log = self.create_mock_execution_log(ticker="BAD", status="failed_all_intervals", interval=None, count=0, start_date_str="2024-01-01")

        def yf_hydrate_side_effect_fail(ticker, start_date, end_date):
            if ticker == "GOOD":
                return mock_good_df, mock_good_log
            elif ticker == "BAD":
                return None, mock_bad_log # 模擬 BAD ticker 抓取失敗
            return None, {}
        mock_yf_client_instance.hydrate_data_range.side_effect = yf_hydrate_side_effect_fail
        mock_yf_client_class.return_value = mock_yf_client_instance

        mock_db_manager_instance = MagicMock()
        mock_db_manager_class.return_value = mock_db_manager_instance
        mock_analysis_engine_instance = MagicMock() # Mock AnalysisEngine
        mock_analysis_engine_class.return_value = mock_analysis_engine_instance
        mock_report_generator_instance = MagicMock()
        mock_report_generator_class.return_value = mock_report_generator_instance

        daily_market_analyzer_run.main() # 更新調用

        mock_db_manager_instance.upsert_data.assert_called_once_with(mock_good_df, table_name="mock_fail_analyzer_ohlcv")

        # 驗證傳遞給 ReportGenerator 的 execution_log
        expected_overall_log_fail = {}
        expected_overall_log_fail.update(mock_good_log)
        expected_overall_log_fail.update(mock_bad_log)

        mock_report_generator_class.assert_called_once_with(
            execution_log=expected_overall_log_fail,
            analysis_engine_instance=mock_analysis_engine_instance
        )
        mock_report_generator_instance.generate_full_report.assert_called_once()
        # 可以進一步檢查 generate_full_report 的參數，但這裡主要關注 execution_log


    @patch('apps.daily_market_analyzer.run.YFinanceClient') # 更新 patch 路徑
    @patch('apps.daily_market_analyzer.run.argparse.ArgumentParser')
    def test_main_flow_process_uploads_is_true(self, mock_argparse, mock_yf_client_class):
        print("\n--- 測試: test_main_flow_process_uploads_is_true (DailyMarketAnalyzer) ---")
        mock_args = MagicMock()
        mock_args.tickers = "AAPL"
        mock_args.start_date = "2024-01-01"
        mock_args.end_date = "2024-01-01"
        mock_args.db_path = "mock_analyzer.db"
        mock_args.table_name = "mock_analyzer_ohlcv"
        mock_args.process_uploads = True # 設置為 True

        mock_parser_instance = MagicMock()
        mock_parser_instance.parse_args.return_value = mock_args
        mock_argparse.return_value = mock_parser_instance

        mock_yf_client_instance = MagicMock()
        # hydrate_data_range 現在返回 df, log_dict
        mock_df = self.create_mock_dataframe(ticker="AAPL", start_date_str="2024-01-01", num_rows=1)
        mock_log = self.create_mock_execution_log(ticker="AAPL", start_date_str="2024-01-01", num_days=1)
        mock_yf_client_instance.hydrate_data_range.return_value = (mock_df, mock_log)
        mock_yf_client_class.return_value = mock_yf_client_instance

        # Mock 其他依賴項
        with patch('apps.daily_market_analyzer.run.DBManager'), \
             patch('apps.daily_market_analyzer.run.AnalysisEngine'), \
             patch('apps.daily_market_analyzer.run.ReportGenerator'), \
             patch('builtins.print') as mock_print:

            daily_market_analyzer_run.main() # 更新調用

            mock_print.assert_any_call("INFO: --process-uploads 被指定，但此功能尚在開發中，將被跳過。")
            mock_yf_client_instance.hydrate_data_range.assert_called_once_with("AAPL", "2024-01-01", "2024-01-01")


if __name__ == '__main__':
    print("--- 執行 Daily Market Analyzer 整合測試 (_test_run.py) ---") # 更新打印信息
    suite = unittest.TestLoader().loadTestsFromTestCase(TestDailyMarketAnalyzerRun) # 更新類名
    runner = unittest.TextTestRunner(verbosity=2)
    runner.run(suite)

```
