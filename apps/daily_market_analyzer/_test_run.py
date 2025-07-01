# -*- coding: utf-8 -*-
"""
整合測試 for apps.daily_market_analyzer.run (v12.0)
使用 unittest.mock 來模擬外部依賴，專注於測試 run.py 的主流程和協調邏輯。
"""
import unittest
from unittest.mock import patch, MagicMock, call # call 用於驗證呼叫順序或多次呼叫的參數
import pandas as pd
import sys
import os
from datetime import datetime, timedelta

# 確保 run.py 和其依賴可以被導入
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# 導入被測試的模組
from apps.daily_market_analyzer import run as daily_market_analyzer_run

class TestDailyMarketAnalyzerRunV12(unittest.TestCase):

    def _create_mock_df(self, num_rows=1, ticker="MOCK", interval="1d",
                        start_datetime_str="2024-01-01 09:30:00"):
        """創建一個用於測試的模擬 DataFrame。"""
        start_dt = datetime.strptime(start_datetime_str, "%Y-%m-%d %H:%M:%S")
        dates = [start_dt + timedelta(minutes=i*5) for i in range(num_rows)] # 假設5分鐘間隔的數據點
        data = {
            'open': [100 + i for i in range(num_rows)],
            'high': [102 + i for i in range(num_rows)],
            'low': [99 + i for i in range(num_rows)],
            'close': [101 + i for i in range(num_rows)],
            'volume': [10000 + i*100 for i in range(num_rows)],
            'ticker': [ticker] * num_rows,
            'interval': [interval] * num_rows
        }
        df = pd.DataFrame(data, index=pd.DatetimeIndex(dates, name='datetime', tz='UTC'))
        return df

    def _create_mock_exec_log(self, ticker, date_str, status="success", interval="1d", count=1, msg="OK"):
        """創建一個用於測試的模擬單日單 ticker 執行日誌條目。"""
        return {
            date_str: {
                ticker: {"status": status, "interval": interval, "count": count, "message": msg}
            }
        }

    @patch('apps.daily_market_analyzer.run.datetime') # Mock datetime.now() for consistent time
    @patch('apps.daily_market_analyzer.run.ReportGenerator')
    @patch('apps.daily_market_analyzer.run.AnalysisEngine')
    @patch('apps.daily_market_analyzer.run.DBManager')
    @patch('apps.daily_market_analyzer.run.YFinanceClient')
    @patch('apps.daily_market_analyzer.run.argparse.ArgumentParser')
    def test_main_flow_all_success(self, mock_argparse, mock_yf_client_cls, mock_db_manager_cls,
                                   mock_analysis_engine_cls, mock_report_generator_cls, mock_datetime):
        print("\n--- Test: test_main_flow_all_success (v12.0) ---")

        # --- Setup Mocks ---
        # Mock datetime.now()
        mock_now_time = datetime(2024, 7, 28, 12, 0, 0)
        mock_datetime.now.return_value = mock_now_time

        # argparse
        mock_args = MagicMock()
        mock_args.tickers = "AAPL,GOOG"
        mock_args.start_date = "2024-07-25"
        mock_args.end_date = "2024-07-26"
        mock_args.db_path = "dummy_path.db"
        mock_args.table_name = "dummy_table"
        mock_args.process_uploads = False
        mock_parser = MagicMock()
        mock_parser.parse_args.return_value = mock_args
        mock_argparse.return_value = mock_parser

        # YFinanceClient
        mock_yf_instance = MagicMock()
        mock_aapl_df_d1 = self._create_mock_df(ticker="AAPL", interval="1m", num_rows=2, start_datetime_str="2024-07-25 09:30:00")
        mock_aapl_df_d2 = self._create_mock_df(ticker="AAPL", interval="1m", num_rows=2, start_datetime_str="2024-07-26 09:30:00")
        mock_goog_df_d1 = self._create_mock_df(ticker="GOOG", interval="5m", num_rows=1, start_datetime_str="2024-07-25 10:00:00")
        # GOOG on day 2 returns no data, but yf_client itself will fill the log correctly

        aapl_log = self._create_mock_exec_log("AAPL", "2024-07-25", "success", "1m", 2)
        aapl_log.update(self._create_mock_exec_log("AAPL", "2024-07-26", "success", "1m", 2))

        goog_log_d1 = self._create_mock_exec_log("GOOG", "2024-07-25", "success", "5m", 1)
        goog_log_d2 = self._create_mock_exec_log("GOOG", "2024-07-26", "no_data_for_interval", "5m", 0, "No data found with 5m for 2024-07-26 after all chunks.")
        goog_log = {**goog_log_d1, **goog_log_d2} # Combine day1 and day2 logs for GOOG

        def yf_side_effect(ticker, start_date, end_date):
            if ticker == "AAPL":
                # Concatenate DFs for AAPL if start/end covers both days, or return relevant part
                df_to_return = pd.concat([mock_aapl_df_d1, mock_aapl_df_d2])
                return df_to_return, aapl_log
            elif ticker == "GOOG":
                 # GOOG only has data for D1
                return mock_goog_df_d1, goog_log
            return pd.DataFrame(), {} # Should not happen in this test

        mock_yf_instance.hydrate_data_range.side_effect = yf_side_effect
        mock_yf_client_cls.return_value = mock_yf_instance

        # DBManager
        mock_db_instance = MagicMock()
        mock_db_manager_cls.return_value = mock_db_instance

        # AnalysisEngine
        mock_ae_instance = MagicMock()
        mock_analysis_engine_cls.return_value = mock_ae_instance

        # ReportGenerator
        mock_rg_instance = MagicMock()
        mock_report_generator_cls.return_value = mock_rg_instance

        # --- Run main ---
        daily_market_analyzer_run.main()

        # --- Assertions ---
        mock_argparse.assert_called_once()
        mock_yf_client_cls.assert_called_once_with()
        mock_db_manager_cls.assert_called_once_with(db_path=mock_args.db_path)
        mock_analysis_engine_cls.assert_called_once_with(db_manager_instance=mock_db_instance)

        mock_db_instance.create_ohlcv_table.assert_called_once_with(table_name=mock_args.table_name)

        self.assertEqual(mock_yf_instance.hydrate_data_range.call_count, 2) # AAPL, GOOG
        mock_yf_instance.hydrate_data_range.assert_any_call("AAPL", mock_args.start_date, mock_args.end_date)
        mock_yf_instance.hydrate_data_range.assert_any_call("GOOG", mock_args.start_date, mock_args.end_date)

        self.assertEqual(mock_db_instance.upsert_data.call_count, 2) # AAPL df, GOOG df
        # Check that the correct DataFrames were passed to upsert_data
        upsert_calls = mock_db_instance.upsert_data.call_args_list
        self.assertTrue(any(c[0][0].equals(pd.concat([mock_aapl_df_d1, mock_aapl_df_d2])) and c[0][1] == mock_args.table_name for c in upsert_calls))
        self.assertTrue(any(c[0][0].equals(mock_goog_df_d1) and c[0][1] == mock_args.table_name for c in upsert_calls))

        # Build expected overall execution log
        expected_overall_log = {}
        for date_key, ticker_log_val in aapl_log.items():
            expected_overall_log.setdefault(date_key, {}).update(ticker_log_val)
        for date_key, ticker_log_val in goog_log.items():
            expected_overall_log.setdefault(date_key, {}).update(ticker_log_val)

        mock_report_generator_cls.assert_called_once_with(
            execution_log=expected_overall_log,
            analysis_engine_instance=mock_ae_instance
        )

        report_call_args_list = mock_rg_instance.generate_full_report.call_args_list
        self.assertEqual(len(report_call_args_list), 1)
        report_call_args = report_call_args_list[0][0] # Get positional args from the call

        self.assertEqual(report_call_args[0], mock_args.start_date)
        self.assertEqual(report_call_args[1], mock_args.end_date)
        self.assertIsInstance(report_call_args[2], datetime) # report_generation_time
        self.assertIsInstance(report_call_args[3], float)    # task_duration_seconds
        self.assertEqual(report_call_args[4], ["AAPL", "GOOG"]) # target_tickers
        self.assertEqual(report_call_args[5], mock_args.table_name) # db_table_name

    # Can add more tests: one_ticker_fails, db_upsert_fails, process_uploads=True etc.
    # For brevity, only one detailed success case is shown here.

if __name__ == '__main__':
    print("--- 執行 Daily Market Analyzer 整合測試 (_test_run.py v12.0) ---")
    unittest.main(verbosity=2)
