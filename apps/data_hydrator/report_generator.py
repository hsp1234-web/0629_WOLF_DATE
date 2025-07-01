# -*- coding: utf-8 -*-
"""
報告生成模組 for Data Hydrator。
負責在數據回填任務完成後，生成一份總結報告。
"""
import pandas as pd
from datetime import datetime

class ReportGenerator:
    """
    生成數據回填任務總結報告。
    """
    def __init__(self):
        """
        初始化 ReportGenerator。
        """
        print("INFO: ReportGenerator 初始化完畢。")

    def create_summary_report(self,
                              successful_hydrations: list[dict],
                              failed_tickers: list[str],
                              overall_start_time: datetime,
                              overall_end_time: datetime,
                              target_tickers: list[str],
                              target_start_date: str,
                              target_end_date: str) -> str:
        """
        生成關於本次數據回填任務的總結文字報告。

        Args:
            successful_hydrations (list[dict]): 成功回填的 Ticker 資訊列表。
                每個 dict 包含: {'ticker': str, 'interval': str, 'num_rows': int,
                                'min_date': datetime, 'max_date': datetime}
            failed_tickers (list[str]): 未能成功回填任何數據的 Ticker 列表。
            overall_start_time (datetime): 整個回填任務的開始時間。
            overall_end_time (datetime): 整個回填任務的結束時間。
            target_tickers (list[str]): 本次任務嘗試處理的所有 Ticker。
            target_start_date (str): 本次任務的目標開始日期。
            target_end_date (str): 本次任務的目標結束日期。

        Returns:
            str: 生成的總結報告文字。
        """
        report_lines = []
        duration = overall_end_time - overall_start_time

        report_lines.append("=" * 50)
        report_lines.append("        數據回填與分析任務總結報告")
        report_lines.append("=" * 50)
        report_lines.append(f"報告生成時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report_lines.append(f"任務執行時長: {duration}")
        report_lines.append("-" * 50)
        report_lines.append(f"目標分析標的: {', '.join(target_tickers)}")
        report_lines.append(f"目標時間範圍: 從 {target_start_date} 到 {target_end_date}")
        report_lines.append("-" * 50)

        report_lines.append("\n📈 **成功回填的標的**:")
        if successful_hydrations:
            for item in successful_hydrations:
                report_lines.append(
                    f"  - {item['ticker']}: "
                    f"成功使用顆粒度 '{item['interval']}' "
                    f"回填 {item['num_rows']} 筆數據。 "
                    f"(數據範圍: {item['min_date'].strftime('%Y-%m-%d')} to {item['max_date'].strftime('%Y-%m-%d')})"
                )
        else:
            report_lines.append("  (本次任務無成功回填的標的)")

        report_lines.append("\n⚠️ **未能回填的標的**:")
        if failed_tickers:
            for ticker in failed_tickers:
                report_lines.append(f"  - {ticker}: 未能在此時間範圍內獲取任何數據。")
        else:
            report_lines.append("  (所有目標標的均已成功處理或部分處理)")

        report_lines.append("\n" + "=" * 50)
        report_lines.append("報告結束")
        report_lines.append("=" * 50)

        summary_report = "\n".join(report_lines)
        print("\n" + summary_report + "\n") # 同時打印到控制台
        return summary_report

if __name__ == '__main__':
    print("--- ReportGenerator 測試 ---")
    reporter = ReportGenerator()

    # 模擬數據
    successful_ops = [
        {'ticker': 'AAPL', 'interval': '1m', 'num_rows': 3900,
         'min_date': datetime(2024,7,1), 'max_date': datetime(2024,7,3)},
        {'ticker': 'GOOG', 'interval': '5m', 'num_rows': 1560,
         'min_date': datetime(2024,6,1), 'max_date': datetime(2024,6,15)},
        {'ticker': '^VIX', 'interval': '1d', 'num_rows': 20,
         'min_date': datetime(2024,5,1), 'max_date': datetime(2024,5,20)},
    ]
    failed_ops = ['MSFT', 'NONEXISTENT']

    start_time = datetime.now() - timedelta(minutes=5)
    end_time = datetime.now()

    target_tickers_list = ['AAPL', 'GOOG', '^VIX', 'MSFT', 'NONEXISTENT']
    target_start = "2024-05-01"
    target_end = "2024-07-03"

    report_text = reporter.create_summary_report(
        successful_hydrations=successful_ops,
        failed_tickers=failed_ops,
        overall_start_time=start_time,
        overall_end_time=end_time,
        target_tickers=target_tickers_list,
        target_start_date=target_start,
        target_end_date=target_end
    )

    # print("\n--- 生成的報告文字 ---")
    # print(report_text)
    print("\n--- ReportGenerator 測試完畢 ---")
