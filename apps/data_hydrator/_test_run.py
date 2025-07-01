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
from apps.data_hydrator import run as data_hydrator_run
# 如果 run.py 內部有全局的 YFinanceClient 等實例化，可能需要在測試中 patch 它們的模組路徑

class TestDataHydratorRun(unittest.TestCase):

    def create_mock_dataframe(self, ticker="TEST", interval="1m", num_rows=5):
        """輔助方法：創建一個模擬的 DataFrame，類似 hydrate_data_range 返回的格式。"""
        dates = pd.to_datetime([f'2024-01-0{i+1} 09:30:00' for i in range(num_rows)]).tz_localize('UTC')
        data = {
            'open': [100 + i for i in range(num_rows)],
            'high': [102 + i for i in range(num_rows)],
            'low': [99 + i for i in range(num_rows)],
            'close': [101 + i for i in range(num_rows)],
            'volume': [10000 + i*100 for i in range(num_rows)],
            'ticker': [ticker] * num_rows,
            'interval': [interval] * num_rows
        }
        df = pd.DataFrame(data, index=dates)
        # df.index.name = 'datetime' # YFinanceClient 返回的 df index 應該是 datetime
        return df

    @patch('apps.data_hydrator.run.ReportGenerator')
    @patch('apps.data_hydrator.run.DBManager')
    @patch('apps.data_hydrator.run.YFinanceClient')
    @patch('apps.data_hydrator.run.argparse.ArgumentParser')
    def test_main_flow_success_case(self, mock_argparse, mock_yf_client_class, mock_db_manager_class, mock_report_generator_class):
        print("\n--- 測試: test_main_flow_success_case ---")
        # --- 1. 設定 Mock 物件 ---

        # Mock argparse
        mock_args = MagicMock()
        mock_args.tickers = "AAPL,MSFT"
        mock_args.start_date = "2024-01-01"
        mock_args.end_date = "2024-01-05"
        mock_args.db_path = "mock_test.db"
        mock_args.table_name = "mock_ohlcv"
        mock_args.process_uploads = False

        mock_parser_instance = MagicMock()
        mock_parser_instance.parse_args.return_value = mock_args
        mock_argparse.return_value = mock_parser_instance

        # Mock YFinanceClient instance and its method
        mock_yf_client_instance = MagicMock()
        # 讓 hydrate_data_range 根據 ticker 返回不同的 mock DataFrame
        mock_aapl_df = self.create_mock_dataframe(ticker="AAPL", interval="1m", num_rows=10)
        mock_msft_df = self.create_mock_dataframe(ticker="MSFT", interval="5m", num_rows=5)

        # 使用 side_effect 根據傳入的 ticker 返回不同的 DataFrame
        def yf_hydrate_side_effect(ticker, start_date, end_date):
            if ticker == "AAPL":
                return mock_aapl_df
            elif ticker == "MSFT":
                return mock_msft_df
            return None
        mock_yf_client_instance.hydrate_data_range.side_effect = yf_hydrate_side_effect
        mock_yf_client_class.return_value = mock_yf_client_instance

        # Mock DBManager instance and its methods
        mock_db_manager_instance = MagicMock()
        mock_db_manager_class.return_value = mock_db_manager_instance

        # Mock ReportGenerator instance and its method
        mock_report_generator_instance = MagicMock()
        mock_report_generator_class.return_value = mock_report_generator_instance

        # --- 2. 執行被測函數 ---
        data_hydrator_run.main()

        # --- 3. 驗證 (Assertions) ---

        # 驗證 ArgumentParser 被調用
        mock_argparse.assert_called_once()
        mock_parser_instance.parse_args.assert_called_once()

        # 驗證 YFinanceClient 被初始化
        mock_yf_client_class.assert_called_once_with()

        # 驗證 hydrate_data_range 被為每個 ticker 調用一次，且參數正確
        self.assertEqual(mock_yf_client_instance.hydrate_data_range.call_count, 2)
        mock_yf_client_instance.hydrate_data_range.assert_any_call("AAPL", "2024-01-01", "2024-01-05")
        mock_yf_client_instance.hydrate_data_range.assert_any_call("MSFT", "2024-01-01", "2024-01-05")

        # 驗證 DBManager 被初始化，且 create_ohlcv_table 被調用
        mock_db_manager_class.assert_called_once_with(db_path="mock_test.db")
        mock_db_manager_instance.create_ohlcv_table.assert_called_once_with(table_name="mock_ohlcv")

        # 驗證 upsert_data 被為每個成功的 DataFrame 調用一次
        self.assertEqual(mock_db_manager_instance.upsert_data.call_count, 2)
        # 驗證 AAPL 的數據被傳入
        # 注意：直接比較 DataFrame 可能會因為內部細節而不穩定，通常比較 shape 或特定內容
        # args_list = mock_db_manager_instance.upsert_data.call_args_list
        # self.assertTrue(any(args[0][0].equals(mock_aapl_df) and args[0][1] == "mock_ohlcv" for args in args_list))
        # self.assertTrue(any(args[0][0].equals(mock_msft_df) and args[0][1] == "mock_ohlcv" for args in args_list))
        # 更簡單的驗證方式是檢查呼叫參數的 shape 和 table_name
        mock_db_manager_instance.upsert_data.assert_any_call(mock_aapl_df, table_name="mock_ohlcv")
        mock_db_manager_instance.upsert_data.assert_any_call(mock_msft_df, table_name="mock_ohlcv")


        # 驗證 ReportGenerator 被初始化和調用
        mock_report_generator_class.assert_called_once_with()
        mock_report_generator_instance.create_summary_report.assert_called_once()

        # 驗證傳遞給 create_summary_report 的參數
        report_args, _ = mock_report_generator_instance.create_summary_report.call_args

        successful_hydrations_arg = report_args[0]['successful_hydrations']
        failed_tickers_arg = report_args[0]['failed_tickers']

        self.assertEqual(len(successful_hydrations_arg), 2)
        self.assertTrue(any(s['ticker'] == 'AAPL' and s['interval'] == '1m' and s['num_rows'] == 10 for s in successful_hydrations_arg))
        self.assertTrue(any(s['ticker'] == 'MSFT' and s['interval'] == '5m' and s['num_rows'] == 5 for s in successful_hydrations_arg))
        self.assertEqual(len(failed_tickers_arg), 0)
        self.assertEqual(report_args[0]['target_tickers'], ["AAPL", "MSFT"])
        self.assertEqual(report_args[0]['target_start_date'], "2024-01-01")
        self.assertEqual(report_args[0]['target_end_date'], "2024-01-05")

    @patch('apps.data_hydrator.run.ReportGenerator')
    @patch('apps.data_hydrator.run.DBManager')
    @patch('apps.data_hydrator.run.YFinanceClient')
    @patch('apps.data_hydrator.run.argparse.ArgumentParser')
    def test_main_flow_one_ticker_fails(self, mock_argparse, mock_yf_client_class, mock_db_manager_class, mock_report_generator_class):
        print("\n--- 測試: test_main_flow_one_ticker_fails ---")
        mock_args = MagicMock()
        mock_args.tickers = "GOOD,BAD"
        mock_args.start_date = "2024-01-01"
        mock_args.end_date = "2024-01-02"
        mock_args.db_path = "mock_fail.db"
        mock_args.table_name = "mock_fail_ohlcv"
        mock_args.process_uploads = False

        mock_parser_instance = MagicMock()
        mock_parser_instance.parse_args.return_value = mock_args
        mock_argparse.return_value = mock_parser_instance

        mock_yf_client_instance = MagicMock()
        mock_good_df = self.create_mock_dataframe(ticker="GOOD", interval="1d", num_rows=2)

        def yf_hydrate_side_effect_fail(ticker, start_date, end_date):
            if ticker == "GOOD":
                return mock_good_df
            elif ticker == "BAD":
                return None # 模擬 BAD ticker 抓取失敗
            return None
        mock_yf_client_instance.hydrate_data_range.side_effect = yf_hydrate_side_effect_fail
        mock_yf_client_class.return_value = mock_yf_client_instance

        mock_db_manager_instance = MagicMock()
        mock_db_manager_class.return_value = mock_db_manager_instance
        mock_report_generator_instance = MagicMock()
        mock_report_generator_class.return_value = mock_report_generator_instance

        data_hydrator_run.main()

        mock_db_manager_instance.upsert_data.assert_called_once_with(mock_good_df, table_name="mock_fail_ohlcv")

        report_args, _ = mock_report_generator_instance.create_summary_report.call_args
        successful_hydrations_arg = report_args[0]['successful_hydrations']
        failed_tickers_arg = report_args[0]['failed_tickers']

        self.assertEqual(len(successful_hydrations_arg), 1)
        self.assertEqual(successful_hydrations_arg[0]['ticker'], "GOOD")
        self.assertEqual(len(failed_tickers_arg), 1)
        self.assertIn("BAD", failed_tickers_arg)

    @patch('apps.data_hydrator.run.YFinanceClient') # 只需 patch YFClient 來測試其未被調用的情況
    @patch('apps.data_hydrator.run.argparse.ArgumentParser')
    def test_main_flow_process_uploads_is_true(self, mock_argparse, mock_yf_client_class):
        print("\n--- 測試: test_main_flow_process_uploads_is_true ---")
        # 主要驗證當 process_uploads 為 True 時，相關邏輯被觸發 (目前是打印訊息)
        # 並且其他流程 (如 YFClient 調用) 可能不執行或按預期執行

        mock_args = MagicMock()
        mock_args.tickers = "AAPL"
        mock_args.start_date = "2024-01-01"
        mock_args.end_date = "2024-01-05"
        mock_args.db_path = "mock_test.db"
        mock_args.table_name = "mock_ohlcv"
        mock_args.process_uploads = True # 設置為 True

        mock_parser_instance = MagicMock()
        mock_parser_instance.parse_args.return_value = mock_args
        mock_argparse.return_value = mock_parser_instance

        mock_yf_client_instance = MagicMock()
        mock_yf_client_instance.hydrate_data_range.return_value = self.create_mock_dataframe() # 即使 uploads=True，後續流程仍應執行
        mock_yf_client_class.return_value = mock_yf_client_instance

        # 為了這個測試，我們需要 mock DBManager 和 ReportGenerator，否則它們會實際執行
        with patch('apps.data_hydrator.run.DBManager'), \
             patch('apps.data_hydrator.run.ReportGenerator'), \
             patch('builtins.print') as mock_print: # 也 mock print 來檢查日誌輸出

            data_hydrator_run.main()

            # 驗證 process_uploads 的 INFO 訊息被打印
            mock_print.assert_any_call("INFO: --process-uploads 被指定，但此功能尚在開發中，將被跳過。")

            # 驗證 YFinanceClient 仍然被調用 (因為 process_uploads 只是先執行，不阻止後續)
            mock_yf_client_instance.hydrate_data_range.assert_called_once_with("AAPL", "2024-01-01", "2024-01-05")


if __name__ == '__main__':
    print("--- 執行 Data Hydrator 整合測試 (_test_run.py) ---")
    # 為了讓測試結果更清晰，可以在此處加入一些 print 語句
    # 或者使用更詳細的 test runner
    # unittest.main(verbosity=2)
    suite = unittest.TestLoader().loadTestsFromTestCase(TestDataHydratorRun)
    runner = unittest.TextTestRunner(verbosity=2)
    runner.run(suite)

```
