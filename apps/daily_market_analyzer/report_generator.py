# -*- coding: utf-8 -*-
"""
報告生成模組 for Daily Market Analyzer (v12.0).
"""
import pandas as pd
from datetime import datetime

# from .analysis_engine import AnalysisEngine # Assuming in the same package

class ReportGenerator:
    def __init__(self, execution_log: dict, analysis_engine_instance):
        self.execution_log = execution_log
        self.analyzer = analysis_engine_instance
        self.target_tickers_overall = [] # Should be set by run.py or inferred if needed
        print("INFO: ReportGenerator (v12.0) 初始化完畢。")

    def _generate_header(self, overall_start_date_str: str, overall_end_date_str: str,
                         report_generation_time: datetime, task_duration_seconds: float) -> str:
        header_lines = [
            "==================================================",
            "        📈 每日市場洞察報告 (v12.0)",
            "==================================================",
            f"報告生成時間: {report_generation_time.strftime('%Y-%m-%d %H:%M:%S %Z%z')}", # Added timezone
            f"數據分析範圍: 從 {overall_start_date_str} 到 {overall_end_date_str}",
            f"總任務執行時長: {task_duration_seconds:.2f} 秒",
            f"分析標的列表: {', '.join(sorted(list(self.target_tickers_overall))) if self.target_tickers_overall else '未指定'}",
            "--------------------------------------------------"
        ]
        return "\n".join(header_lines)

    def _generate_inventory_md(self, date_str: str) -> str:
        lines = ["\n#### 📜 本日數據盤點 (Data Inventory)"]
        daily_log_for_date = self.execution_log.get(date_str, {})

        # Use self.target_tickers_overall to ensure all targeted tickers are listed for the day
        # even if some had no processing attempt logged (though yf_client should log all attempts).

        # Get all tickers that have an entry for this date in the log OR are in the overall target list
        # This ensures we report on all targeted tickers for each day.
        tickers_for_this_day_report = sorted(list(set(list(daily_log_for_date.keys()) + self.target_tickers_overall)))


        if not tickers_for_this_day_report:
            lines.append(f"- {date_str}: 無任何標的處理記錄或目標標的。")
            return "\n".join(lines)

        for ticker in tickers_for_this_day_report:
            result = daily_log_for_date.get(ticker)
            if result:
                status = result.get('status', 'unknown_in_log')
                interval = result.get('interval', 'N/A')
                count = result.get('count', 0)
                message = result.get('message', '') # Get message if available

                if status.startswith('success'): # Covers 'success' and 'success_partial'
                    lines.append(f"- ✅ **{ticker}**: 成功獲取 **{interval}** 數據 ({count} 筆). {message}")
                elif status == 'no_data_for_interval':
                     lines.append(f"- 🟡 **{ticker}**: 使用顆粒度 **{interval}** 未發現數據. {message}")
                elif status == 'skipped_1m_due_to_30day_limit':
                    lines.append(f"- ⏩ **{ticker}**: 跳過 **1m** (超出30天限制). {message}")
                elif status == 'failed_all_intervals':
                     lines.append(f"- ❌ **{ticker}**: 所有顆粒度均未能獲取數據. {message}")
                elif status == 'failed_chunk':
                     lines.append(f"- ⚠️ **{ticker}**: 顆粒度 **{interval}** 某區塊抓取失敗. {message}")
                elif status == 'db_upsert_failed':
                    lines.append(f"-  Datenbankfehler 💾 **{ticker}**: 數據庫寫入失敗. {message}")
                else:
                    lines.append(f"- ❓ **{ticker}**: 狀態: {status}. {message if message else '(無詳細訊息)'}")
            else:
                # This ticker was in target_tickers_overall but not in daily_log_for_date for this specific date
                lines.append(f"- ❔ **{ticker}**: 在本日的執行日誌中無記錄 (可能未輪到處理、被跳過，或未在目標日期範圍內)。")
        return "\n".join(lines)

    def _generate_snapshot_md(self, date_str: str, db_table_name: str) -> str:
        lines = ["\n#### 📊 本日市場快照 (Market Snapshot)"]
        daily_log_for_date = self.execution_log.get(date_str, {})

        tickers_successfully_fetched = sorted([
            ticker for ticker, result in daily_log_for_date.items()
            if result.get('status', '').startswith('success') and result.get('count', 0) > 0
        ])

        if not tickers_successfully_fetched:
            lines.append("- 今日無成功獲取數據的標的進行分析。")
            return "\n".join(lines)

        header = "| 股票代號 | 收盤價 | 較前日% | 日內高點 | 日內低點 | 波動幅度% | 成交量 | 使用顆粒度 | 前日收盤 |"
        separator = "|---|---|---|---|---|---|---|---|---|"
        lines.append(header)
        lines.append(separator)

        for ticker in tickers_successfully_fetched:
            analysis = self.analyzer.analyze_daily_ticker_data(ticker, date_str, db_table_name)
            log_entry = daily_log_for_date.get(ticker, {}) # Get log entry for interval

            if analysis and analysis.get('status') == 'success':
                lines.append(
                    f"| **{ticker}** | {analysis.get('close', 'N/A')} | {analysis.get('change_pct', 'N/A')} | "
                    f"{analysis.get('high', 'N/A')} | {analysis.get('low', 'N/A')} | {analysis.get('range_pct', 'N/A')} | "
                    f"{analysis.get('volume', 'N/A')} | {log_entry.get('interval','N/A')} | {analysis.get('prev_close', 'N/A')} |"
                )
            else:
                lines.append(f"| **{ticker}** | *分析失敗或無數據* | N/A | N/A | N/A | N/A | N/A | {log_entry.get('interval','N/A')} | N/A |")
        return "\n".join(lines)

    def _generate_daily_section(self, date_str: str, db_table_name: str) -> str:
        inventory_md = self._generate_inventory_md(date_str)
        snapshot_md = self._generate_snapshot_md(date_str, db_table_name)
        return f"\n## 🗓️ {date_str}\n{inventory_md}\n{snapshot_md}"

    def generate_full_report(self, overall_start_date_str: str, overall_end_date_str: str,
                             report_generation_time: datetime, task_duration_seconds: float,
                             target_tickers: list[str], db_table_name: str) -> str:
        self.target_tickers_overall = sorted(list(set(target_tickers)))

        report_parts = [self._generate_header(overall_start_date_str, overall_end_date_str,
                                              report_generation_time, task_duration_seconds)]
        try:
            date_range = pd.date_range(start=overall_start_date_str, end=overall_end_date_str, freq='D').sort_values(ascending=False)
            if date_range.empty and overall_start_date_str == overall_end_date_str:
                 date_range = pd.to_datetime([overall_start_date_str])
        except Exception as e:
            print(f"錯誤: 生成報告日期範圍時出錯: {e}")
            report_parts.append(f"\n錯誤：無法生成日期範圍從 {overall_start_date_str} 到 {overall_end_date_str}。")
            return "\n\n---\n\n".join(report_parts)

        for date_obj in date_range:
            date_str = date_obj.strftime('%Y-%m-%d')
            daily_report_md = self._generate_daily_section(date_str, db_table_name)
            report_parts.append(daily_report_md)

        final_report_text = "\n\n---\n\n".join(report_parts)
        print("\n" + final_report_text + "\n")
        return final_report_text

if __name__ == '__main__':
    print("--- ReportGenerator (v12.0) 測試 ---")
    class MockAnalysisEngineForReport:
        def analyze_daily_ticker_data(self, ticker, date_str, table_name="mock_table"):
            if ticker == "TICKA" and date_str == "2024-07-25":
                return {"status": "success", "close": "100.00", "prev_close": "99.00", "change_pct": "+1.01%",
                        "high": "101.00", "low": "99.50", "range_pct": "1.51%", "volume": "1,000"}
            return {"status": "no_data", "message": "Mock no data"}

    mock_analyzer_inst = MockAnalysisEngineForReport()
    mock_exec_log_rg = {
        "2024-07-25": {
            "TICKA": {"status": "success", "interval": "1d", "count": 1, "message": "OK"},
            "TICKB": {"status": "failed_all_intervals", "interval": None, "count": 0, "message": "Failed"},
        },
        "2024-07-24": {
            "TICKA": {"status": "no_data_for_interval", "interval": "1d", "count": 0, "message": "No data at 1d"},
        }
    }
    reporter = ReportGenerator(execution_log=mock_exec_log_rg, analysis_engine_instance=mock_analyzer_inst)
    report = reporter.generate_full_report("2024-07-24", "2024-07-25", datetime.now(), 10.5, ["TICKA", "TICKB", "TICKC"], "test_table")
    assert "🗓️ 2024-07-25" in report
    assert "TICKA**: 成功獲取 **1d** 數據 (1 筆)" in report
    assert "TICKB**: 所有顆粒度均未能獲取數據" in report
    assert "TICKC**: 在本日的執行日誌中無記錄" in report # Test for ticker in target but not in log for the day
    assert "| **TICKA** | 100.00 | +1.01% |" in report
    print("--- ReportGenerator (v12.0) 測試完畢 ---")
