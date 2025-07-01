# -*- coding: utf-8 -*-
"""
整合測試 for apps.daily_market_analyzer.run

使用 unittest.mock 來模擬外部依賴 (YFinanceClient, DBManager, ReportGenerator, AnalysisEngine)，
專注於測試 run.py 的主流程和協調邏輯。
"""
import unittest
from unittest.mock import patch, MagicMock, call
import pandas as pd
import sys
import os
from datetime import datetime, timedelta # 確保 timedelta 已導入

# 確保 run.py 和其依賴可以被導入
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from apps.daily_market_analyzer import run as daily_market_analyzer_run

class TestDailyMarketAnalyzerRun(unittest.TestCase):

    def create_mock_dataframe(self, ticker="TEST", interval="1m", num_rows=5, start_date_str="2024-01-01"):
        """輔助方法：創建一個模擬的 DataFrame，類似 hydrate_data_range 返回的格式。"""
        start_dt = datetime.strptime(start_date_str, "%Y-%m-%d")
        # 確保 DatetimeIndex 名稱為 'datetime' 以匹配 yfinance_client 中的處理
        idx_dates = [start_dt + timedelta(minutes=i*5) for i in range(num_rows)] # 分鐘級別變化以避免合併問題

        data = {
            'open': [100 + i for i in range(num_rows)],
            'high': [102 + i for i in range(num_rows)],
            'low': [99 + i for i in range(num_rows)],
            'close': [101 + i for i in range(num_rows)],
            'volume': [10000 + i*100 for i in range(num_rows)],
            'ticker': [ticker] * num_rows,
            'interval': [interval] * num_rows
        }
        # 創建帶有 UTC 時區的 DatetimeIndex
        df = pd.DataFrame(data, index=pd.DatetimeIndex(idx_dates, name='datetime').tz_localize('UTC'))
        return df

    def create_mock_execution_log(self, ticker, status="success", interval="1m", count=5, start_date_str="2024-01-01", num_days=1, message_override=None):
        """輔助方法：創建模擬的 execution_log。"""
        log = {}
        start_dt = datetime.strptime(start_date_str, "%Y-%m-%d")
        for i in range(num_days):
            date_key = (start_dt + timedelta(days=i)).strftime("%Y-%m-%d")
            log.setdefault(date_key, {})[ticker] = {
                "status": status,
                "interval": interval if status in ["success", "success_partial"] else None, # 確保 interval 只在成功時設定
                "count": count if status in ["success", "success_partial"] else 0, # 確保 count 只在成功時設定
                "message": message_override if message_override else f"{status} for {ticker} on {date_key}"
            }
        return log

    @patch('apps.daily_market_analyzer.run.ReportGenerator')
    @patch('apps.daily_market_analyzer.run.AnalysisEngine')
    @patch('apps.daily_market_analyzer.run.DBManager')
    @patch('apps.daily_market_analyzer.run.YFinanceClient')
    @patch('apps.daily_market_analyzer.run.argparse.ArgumentParser')
    def test_main_flow_success_case(self, mock_argparse, mock_yf_client_class, mock_db_manager_class, mock_analysis_engine_class, mock_report_generator_class):
        print("\n--- 測試案例：主要流程成功 (每日市場分析儀) ---")
        mock_args = MagicMock()
        mock_args.tickers = "AAPL,MSFT"
        mock_args.start_date = "2024-01-01"
        mock_args.end_date = "2024-01-02"
        mock_args.db_path = "mock_analyzer.db"
        mock_args.table_name = "mock_analyzer_ohlcv"
        mock_args.process_uploads = False

        mock_parser_instance = MagicMock()
        mock_parser_instance.parse_args.return_value = mock_args
        mock_argparse.return_value = mock_parser_instance

        mock_yf_client_instance = MagicMock()
        # AAPL 數據和日誌 (兩天)
        mock_aapl_df_day1 = self.create_mock_dataframe(ticker="AAPL", interval="1m", num_rows=1, start_date_str="2024-01-01")
        mock_aapl_df_day2 = self.create_mock_dataframe(ticker="AAPL", interval="1m", num_rows=1, start_date_str="2024-01-02")
        mock_aapl_df = pd.concat([mock_aapl_df_day1, mock_aapl_df_day2])

        mock_aapl_log = self.create_mock_execution_log(ticker="AAPL", interval="1m", count=1, start_date_str="2024-01-01", num_days=1)
        mock_aapl_log.update(self.create_mock_execution_log(ticker="AAPL", interval="1m", count=1, start_date_str="2024-01-02", num_days=1))

        # MSFT 數據和日誌 (兩天)
        mock_msft_df_day1 = self.create_mock_dataframe(ticker="MSFT", interval="5m", num_rows=1, start_date_str="2024-01-01")
        mock_msft_df_day2 = self.create_mock_dataframe(ticker="MSFT", interval="5m", num_rows=1, start_date_str="2024-01-02")
        mock_msft_df = pd.concat([mock_msft_df_day1, mock_msft_df_day2])

        mock_msft_log = self.create_mock_execution_log(ticker="MSFT", interval="5m", count=1, start_date_str="2024-01-01", num_days=1)
        mock_msft_log.update(self.create_mock_execution_log(ticker="MSFT", interval="5m", count=1, start_date_str="2024-01-02", num_days=1))


        def yf_hydrate_side_effect(ticker, start_date, end_date):
            if ticker == "AAPL":
                return mock_aapl_df, mock_aapl_log
            elif ticker == "MSFT":
                return mock_msft_df, mock_msft_log
            return pd.DataFrame(), {} # 返回空的 DataFrame 和 log
        mock_yf_client_instance.hydrate_data_range.side_effect = yf_hydrate_side_effect
        mock_yf_client_class.return_value = mock_yf_client_instance

        mock_db_manager_instance = MagicMock()
        mock_db_manager_class.return_value = mock_db_manager_instance
        mock_analysis_engine_instance = MagicMock()
        mock_analysis_engine_class.return_value = mock_analysis_engine_instance
        mock_report_generator_instance = MagicMock()
        mock_report_generator_instance.generate_full_report.return_value = "模擬報告內容" #確保返回字符串
        mock_report_generator_class.return_value = mock_report_generator_instance

        with patch('builtins.print') as mock_print: # 捕獲 print 輸出
            daily_market_analyzer_run.main()

            mock_argparse.assert_called_once()
            mock_parser_instance.parse_args.assert_called_once()
            mock_yf_client_class.assert_called_once_with()

            self.assertEqual(mock_yf_client_instance.hydrate_data_range.call_count, 2)
            mock_yf_client_instance.hydrate_data_range.assert_any_call("AAPL", "2024-01-01", "2024-01-02")
            mock_yf_client_instance.hydrate_data_range.assert_any_call("MSFT", "2024-01-01", "2024-01-02")

            mock_db_manager_class.assert_called_once_with(db_path="mock_analyzer.db")
            mock_db_manager_instance.create_ohlcv_table.assert_called_once_with(table_name="mock_analyzer_ohlcv")
            self.assertEqual(mock_db_manager_instance.upsert_data.call_count, 2)
            # 驗證 upsert_data 時傳遞的是 DataFrame
            self.assertTrue(pd.DataFrame.equals(mock_db_manager_instance.upsert_data.call_args_list[0][0][0], mock_aapl_df))
            self.assertEqual(mock_db_manager_instance.upsert_data.call_args_list[0][1]['table_name'], "mock_analyzer_ohlcv")
            self.assertTrue(pd.DataFrame.equals(mock_db_manager_instance.upsert_data.call_args_list[1][0][0], mock_msft_df))
            self.assertEqual(mock_db_manager_instance.upsert_data.call_args_list[1][1]['table_name'], "mock_analyzer_ohlcv")


            mock_analysis_engine_class.assert_called_once_with(db_manager_instance=mock_db_manager_instance)

            expected_overall_log = {}
            for date_key, ticker_log_val in mock_aapl_log.items():
                expected_overall_log.setdefault(date_key, {}).update(ticker_log_val)
            for date_key, ticker_log_val in mock_msft_log.items():
                expected_overall_log.setdefault(date_key, {}).update(ticker_log_val)

            mock_report_generator_class.assert_called_once_with(
                execution_log=expected_overall_log,
                analysis_engine_instance=mock_analysis_engine_instance
            )

            # 新增：首先確認 generate_full_report 是否被調用
            mock_report_generator_instance.generate_full_report.assert_called_once()

            # 獲取 call_args
            # call_args 是一個 unittest.mock.call 實例，它是一個元組 (args, kwargs)
            # args 是位置參數的元組，kwargs 是關鍵字參數的字典
            call_args_info = mock_report_generator_instance.generate_full_report.call_args

            # 位置參數應該是空的，因為 run.py 使用關鍵字參數調用
            self.assertEqual(len(call_args_info.args), 0, "generate_full_report 不應使用位置參數調用")

            # 關鍵字參數在 call_args.kwargs
            report_kwargs = call_args_info.kwargs

            self.assertEqual(report_kwargs['overall_start_date_str'], mock_args.start_date)
            self.assertEqual(report_kwargs['overall_end_date_str'], mock_args.end_date)
            self.assertIsInstance(report_kwargs['report_generation_time'], datetime)
            self.assertIsInstance(report_kwargs['task_duration_seconds'], float)
            self.assertEqual(report_kwargs['target_tickers'], ["AAPL", "MSFT"])
            self.assertEqual(report_kwargs['db_table_name'], mock_args.table_name)

            # 驗證報告內容被打印
            mock_print.assert_any_call("\n--- 市場分析報告內容 ---")
            mock_print.assert_any_call("模擬報告內容")


    @patch('apps.daily_market_analyzer.run.ReportGenerator')
    @patch('apps.daily_market_analyzer.run.AnalysisEngine')
    @patch('apps.daily_market_analyzer.run.DBManager')
    @patch('apps.daily_market_analyzer.run.YFinanceClient')
    @patch('apps.daily_market_analyzer.run.argparse.ArgumentParser')
    def test_main_flow_one_ticker_fails(self, mock_argparse, mock_yf_client_class, mock_db_manager_class, mock_analysis_engine_class, mock_report_generator_class):
        print("\n--- 測試案例：單一標的處理失敗 (每日市場分析儀) ---")
        mock_args = MagicMock()
        mock_args.tickers = "GOOD,BAD"
        mock_args.start_date = "2024-01-01"
        mock_args.end_date = "2024-01-01"
        mock_args.db_path = "mock_fail_analyzer.db"
        mock_args.table_name = "mock_fail_analyzer_ohlcv"
        mock_args.process_uploads = False

        mock_parser_instance = MagicMock()
        mock_parser_instance.parse_args.return_value = mock_args
        mock_argparse.return_value = mock_parser_instance

        mock_yf_client_instance = MagicMock()
        mock_good_df = self.create_mock_dataframe(ticker="GOOD", interval="1d", num_rows=1, start_date_str="2024-01-01")
        mock_good_log = self.create_mock_execution_log(ticker="GOOD", interval="1d", count=1, start_date_str="2024-01-01", num_days=1)
        mock_bad_log = self.create_mock_execution_log(ticker="BAD", status="failed_all_intervals", interval=None, count=0, start_date_str="2024-01-01", num_days=1)

        def yf_hydrate_side_effect_fail(ticker, start_date, end_date):
            if ticker == "GOOD":
                return mock_good_df, mock_good_log
            elif ticker == "BAD":
                return pd.DataFrame(), mock_bad_log # 返回空的 DataFrame 和失敗日誌
            return pd.DataFrame(), {}
        mock_yf_client_instance.hydrate_data_range.side_effect = yf_hydrate_side_effect_fail
        mock_yf_client_class.return_value = mock_yf_client_instance

        mock_db_manager_instance = MagicMock()
        mock_db_manager_class.return_value = mock_db_manager_instance
        mock_analysis_engine_instance = MagicMock()
        mock_analysis_engine_class.return_value = mock_analysis_engine_instance
        mock_report_generator_instance = MagicMock()
        mock_report_generator_class.return_value = mock_report_generator_instance

        daily_market_analyzer_run.main()

        mock_db_manager_instance.upsert_data.assert_called_once()
        self.assertTrue(pd.DataFrame.equals(mock_db_manager_instance.upsert_data.call_args[0][0], mock_good_df))
        self.assertEqual(mock_db_manager_instance.upsert_data.call_args[1]['table_name'], "mock_fail_analyzer_ohlcv")


        expected_overall_log_fail = {}
        for date_key, ticker_log_val in mock_good_log.items():
            expected_overall_log_fail.setdefault(date_key, {}).update(ticker_log_val)
        for date_key, ticker_log_val in mock_bad_log.items(): # BAD ticker 的日誌也應該被合併
            expected_overall_log_fail.setdefault(date_key, {}).update(ticker_log_val)

        mock_report_generator_class.assert_called_once_with(
            execution_log=expected_overall_log_fail,
            analysis_engine_instance=mock_analysis_engine_instance
        )
        mock_report_generator_instance.generate_full_report.assert_called_once()


    @patch('apps.daily_market_analyzer.run.YFinanceClient')
    @patch('apps.daily_market_analyzer.run.argparse.ArgumentParser')
    def test_main_flow_process_uploads_is_true(self, mock_argparse, mock_yf_client_class):
        print("\n--- 測試案例：處理上傳選項為 True (每日市場分析儀) ---")
        mock_args = MagicMock()
        mock_args.tickers = "AAPL"
        mock_args.start_date = "2024-01-01"
        mock_args.end_date = "2024-01-01"
        mock_args.db_path = "mock_analyzer.db"
        mock_args.table_name = "mock_analyzer_ohlcv"
        mock_args.process_uploads = True

        mock_parser_instance = MagicMock()
        mock_parser_instance.parse_args.return_value = mock_args
        mock_argparse.return_value = mock_parser_instance

        mock_yf_client_instance = MagicMock()
        mock_df = self.create_mock_dataframe(ticker="AAPL", start_date_str="2024-01-01", num_rows=1)
        mock_log = self.create_mock_execution_log(ticker="AAPL", start_date_str="2024-01-01", num_days=1)
        mock_yf_client_instance.hydrate_data_range.return_value = (mock_df, mock_log)
        mock_yf_client_class.return_value = mock_yf_client_instance

        with patch('apps.daily_market_analyzer.run.DBManager'), \
             patch('apps.daily_market_analyzer.run.AnalysisEngine'), \
             patch('apps.daily_market_analyzer.run.ReportGenerator'), \
             patch('builtins.print') as mock_print:

            daily_market_analyzer_run.main()

            mock_print.assert_any_call("資訊：--process-uploads 選項已指定，但此功能尚在開發中，將被略過。")
            mock_yf_client_instance.hydrate_data_range.assert_called_once_with("AAPL", "2024-01-01", "2024-01-01")


if __name__ == '__main__':
    print("--- 執行每日市場分析儀整合測試 (_test_run.py) ---")
    suite = unittest.TestLoader().loadTestsFromTestCase(TestDailyMarketAnalyzerRun)
    runner = unittest.TextTestRunner(verbosity=2)
    runner.run(suite)
