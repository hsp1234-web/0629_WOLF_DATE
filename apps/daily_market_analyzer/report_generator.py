# -*- coding: utf-8 -*-
"""
報告生成模組 for 每日市場分析儀。
負責將數據抓取日誌和分析引擎的結果匯總成人類可讀的每日市場報告。
"""
import pandas as pd
from datetime import datetime

class ReportGenerator:
    def __init__(self, execution_log: dict, analysis_engine_instance):
        self.execution_log = execution_log
        self.analyzer = analysis_engine_instance
        self.target_tickers_overall = []
        self.db_table_name = None
        print("資訊：報告生成器 (ReportGenerator) 初始化完畢。")

    def _generate_task_summary_md(self, overall_start_date_str: str, overall_end_date_str: str,
                                  report_generation_time: datetime, task_duration_seconds: float,
                                  target_tickers: list[str], overall_execution_log: dict) -> str:
        interval_counts = {}
        final_ticker_status = {}
        for ticker in target_tickers:
            final_ticker_status[ticker] = "no_data"
            found_success_for_ticker = False
            for date_key in pd.date_range(start=overall_start_date_str, end=overall_end_date_str).strftime('%Y-%m-%d'):
                log_entry = overall_execution_log.get(date_key, {}).get(ticker, {})
                if log_entry.get('status') == 'success' and log_entry.get('count', 0) > 0:
                    final_ticker_status[ticker] = "success"
                    interval = log_entry.get('interval')
                    if interval: interval_counts[interval] = interval_counts.get(interval, 0) + 1
                    found_success_for_ticker = True
                elif not found_success_for_ticker and \
                     (log_entry.get('status') == 'skipped_1m_due_to_30day_limit' or \
                      (log_entry.get('status') == 'success_partial' and log_entry.get('count', 0) > 0) or \
                      (log_entry.get('status') == 'success' and log_entry.get('count', 0) > 0 and log_entry.get('interval') != '1m')):
                    final_ticker_status[ticker] = "fallback"
                    interval = log_entry.get('interval')
                    if interval: interval_counts[interval] = interval_counts.get(interval, 0) + 1

        successful_tickers_count = sum(1 for status in final_ticker_status.values() if status in ["success", "fallback"])
        summary_parts = []
        if successful_tickers_count == len(target_tickers):
            summary_parts.append(f"成功為所有 {len(target_tickers)} 個標的獲取數據。")
        elif successful_tickers_count > 0:
            summary_parts.append(f"成功為 {successful_tickers_count} 個標的獲取數據，{len(target_tickers) - successful_tickers_count} 個標的數據獲取不完整或失敗。")
        else:
            summary_parts.append(f"未能為任何目標標的成功獲取數據。")
        if interval_counts:
            common_intervals = sorted(interval_counts.items(), key=lambda item: item[1], reverse=True)
            summary_parts.append(f"主要獲取到的數據顆粒度包括：{', '.join([f'{i[0]}' for i in common_intervals])}。")
        else:
            summary_parts.append("未獲取到有效數據顆粒度。")
        summary_text = " ".join(summary_parts)
        header_lines = ["# 數據回填與市場分析報告\n", "## 任務總結",
                        f"- **執行時間**: {report_generation_time.strftime('%Y-%m-%d %H:%M:%S UTC%z')}",
                        f"- **分析範圍**: {overall_start_date_str} 至 {overall_end_date_str}",
                        f"- **分析標的**: {', '.join(target_tickers) if target_tickers else '未指定'}",
                        f"- **總結**: {summary_text}", "\n---"]
        return "\n".join(header_lines)

    def _generate_inventory_md(self, date_str: str) -> str:
        lines = ["\n#### 📜 本日數據盤點 (Data Inventory)"]
        daily_log_for_date = self.execution_log.get(date_str, {})
        tickers_to_report = self.target_tickers_overall if self.target_tickers_overall else list(daily_log_for_date.keys())
        if not tickers_to_report and not daily_log_for_date:
             lines.append(f"- {date_str}: 無任何標的之處理記錄或目標標的。")
             return "\n".join(lines)
        for ticker in sorted(list(set(tickers_to_report))):
            log_entry = daily_log_for_date.get(ticker); status_display = ""; reason_display = ""
            if log_entry:
                actual_status = log_entry.get('status'); actual_interval = log_entry.get('interval')
                actual_count = log_entry.get('count', 0); message = log_entry.get('message', "")
                if actual_status == 'success':
                    is_fallback = False
                    if actual_interval and actual_interval != '1m' and not ticker.startswith('^') and \
                       ((message and "skipped" in message.lower() and "1m" in message.lower()) or \
                        (not (message and "skipped" in message.lower() and "1m" in message.lower()))):
                        is_fallback = True
                    if is_fallback:
                        status_display = f"⚠️ **{ticker}**: 降級至 **{actual_interval}** 數據 ({actual_count} 筆)."
                        reason_display = "*(註：1分鐘線數據超出回溯限制或不可用)*" if "skipped" in message.lower() and "1m" in message.lower() else f"*(註：已獲取 {actual_interval} 數據)*"
                    else: status_display = f"✅ **{ticker}**: 成功獲取 **{actual_interval}** 數據 ({actual_count} 筆)."
                elif actual_status in ['no_data_for_interval', 'failed_all_intervals'] or \
                     (actual_status == 'skipped_1m_due_to_30day_limit' and actual_count == 0 and not actual_interval):
                    status_display = f"❌ **{ticker}**: 未能獲取到當日數據."
                    if "Market closed" in message: reason_display = "*(註：市場休市)*"
                    elif "All intervals failed" in message or "No data found for" in message : reason_display = "*(註：所有嘗試均失敗或無數據)*"
                    elif actual_status == 'skipped_1m_due_to_30day_limit': reason_display = "*(註：1分鐘數據不可用且無其他替代數據)*"
                    else: reason_display = f"*(註：{message})*"
                elif actual_status == 'skipped_1m_due_to_30day_limit':
                    status_display = f"❔ **{ticker}**: 1分鐘數據嘗試被跳過."; reason_display = f"*(註：{message})*"
                else: status_display = f"❌ **{ticker}**: 數據處理時發生問題."; reason_display = f"*(註：狀態 [{actual_status}]. 詳細: {message})*"
            else: status_display = f"❔ **{ticker}**: 無當日處理記錄."; reason_display = "*(註：可能當日未執行處理、無數據或過程被跳過)*"
            lines.append(status_display + (f" {reason_display}" if reason_display else ""))
        return "\n".join(lines)

    def _generate_snapshot_md(self, date_str: str) -> str:
        header_text = "\n#### 📊 本日市場快照 (Market Snapshot)\n\n"
        table_header = "| 標的 | 收盤價 | 漲跌% | 日內波幅% | 成交量 | 市場解讀 |\n"
        table_separator = "|:---|:---:|:---:|:---:|:---:|:---|\n"
        table_rows = []; tickers_for_snapshot = []
        daily_log_for_date = self.execution_log.get(date_str, {})
        for ticker in self.target_tickers_overall:
            log_entry = daily_log_for_date.get(ticker, {})
            if log_entry.get('status') == 'success' and log_entry.get('count', 0) > 0:
                tickers_for_snapshot.append(ticker)
        tickers_for_snapshot = sorted(list(set(tickers_for_snapshot)))
        for ticker in tickers_for_snapshot:
            analysis = self.analyzer.analyze_daily_ticker_data(ticker, date_str, self.db_table_name)
            if analysis and analysis.get('status') == 'success':
                row = f"| **{ticker}** | {analysis.get('close', 'N/A')} | {analysis.get('change_pct', 'N/A')} | " \
                      f"{analysis.get('range_pct', 'N/A')} | {analysis.get('volume', 'N/A')} | {analysis.get('interpretation', 'N/A')} |"
                table_rows.append(row)
        if not table_rows: return header_text + "- 今日無成功獲取數據之標的以供市場快照分析。\n"
        return header_text + table_header + table_separator + "\n".join(table_rows)

    def _generate_options_sentiment_md(self, date_str: str) -> str:
        options_analysis = self.analyzer.analyze_daily_options_data(date_str)
        lines = ["\n#### 📈 選擇權市場情緒 (Options Market Sentiment)"]
        if options_analysis and options_analysis.get('status') == 'success':
            pc_volume_ratio = options_analysis.get('put_call_volume_ratio', 'N/A')
            pc_oi_ratio = options_analysis.get('put_call_oi_ratio', 'N/A')
            if isinstance(pc_volume_ratio, float): pc_volume_ratio = f"{pc_volume_ratio:.2f}"
            if isinstance(pc_oi_ratio, float): pc_oi_ratio = f"{pc_oi_ratio:.2f}"
            lines.append(f"- **Put/Call 成交量比 (Volume Ratio)**: {pc_volume_ratio}")
            lines.append(f"- **Put/Call 未平倉量比 (OI Ratio)**: {pc_oi_ratio}")
            try:
                vol_ratio_num = float(pc_volume_ratio)
                if vol_ratio_num > 1.0: lines.append(f"- *情緒解讀*: 看跌期權成交相對活躍，市場情緒偏謹慎。")
                elif vol_ratio_num < 0.7 and vol_ratio_num > 0: lines.append(f"- *情緒解讀*: 看漲期權成交相對活躍，市場情緒偏樂觀。")
                else: lines.append(f"- *情緒解讀*: P/C成交量比較為均衡。")
            except ValueError: lines.append(f"- *情緒解讀*: 成交量比數據不足，無法解讀情緒。")
        else:
            message = options_analysis.get('message', "本日無選擇權數據可供分析。") if options_analysis else "本日選擇權數據分析未執行或失敗。"
            lines.append(f"- {message}")
        return "\n".join(lines)

    def _generate_daily_section(self, date_str: str) -> str:
        inventory_md = self._generate_inventory_md(date_str)
        snapshot_md = self._generate_snapshot_md(date_str)
        options_sentiment_md = self._generate_options_sentiment_md(date_str)
        return f"\n## 🗓️ {date_str}\n{inventory_md}\n{snapshot_md}\n{options_sentiment_md}"

    def generate_full_report(self, overall_start_date_str: str, overall_end_date_str: str,
                             report_generation_time: datetime, task_duration_seconds: float,
                             target_tickers: list[str], db_table_name: str) -> str:
        self.target_tickers_overall = sorted(list(set(target_tickers)))
        self.db_table_name = db_table_name
        task_summary_md = self._generate_task_summary_md(
            overall_start_date_str, overall_end_date_str, report_generation_time,
            task_duration_seconds, self.target_tickers_overall, self.execution_log)
        report_parts = [task_summary_md]
        try:
            date_range = pd.date_range(start=overall_start_date_str, end=overall_end_date_str, freq='D').sort_values(ascending=False)
        except Exception as e:
            print(f"錯誤：生成日期範圍時發生錯誤：{e}")
            report_parts.append(f"\n錯誤：無法生成從 {overall_start_date_str} 到 {overall_end_date_str} 的日期範圍報告。")
            return "\n\n---\n\n".join(report_parts)
        if date_range.empty and overall_start_date_str == overall_end_date_str:
             date_range = pd.to_datetime([overall_start_date_str])
        for date_obj in date_range:
            date_str = date_obj.strftime('%Y-%m-%d')
            daily_report_md = self._generate_daily_section(date_str)
            report_parts.append(daily_report_md)
        final_report_text = "\n\n---\n\n".join(report_parts)
        return final_report_text

if __name__ == '__main__':
    print("--- 報告生成器 (ReportGenerator) v2.0 (含選擇權) 測試 ---")

    class MockAnalysisEngineV2:
        def analyze_daily_ticker_data(self, ticker, date_str, table_name="mock_table"):
            if ticker == "AAPL" and date_str == "2024-07-25":
                return {"status": "success", "close": "150.90", "prev_close": "149.80", "change_pct": "+0.73%", "high": "152.00", "low": "149.00", "range_pct": "2.01%", "volume": "330,000", "interpretation": "市場常規波動。"}
            if ticker == "GOOG" and date_str == "2024-07-25":
                 return {"status": "success", "close": "2500.50", "prev_close": "2490.00", "change_pct": "+0.42%", "high": "2510.00", "low": "2480.00", "range_pct": "1.21%", "volume": "1,200,000", "interpretation": "溫和波動，趨勢不明。"}
            if ticker == "MSFT" and date_str == "2024-07-24":
                 return {"status": "success", "close": "300.00", "prev_close": "298.00", "change_pct": "+0.67%", "high": "301.00", "low": "297.00", "range_pct": "1.35%", "volume": "900,000", "interpretation": "市場常規波動。"}
            if ticker == "TSLA" and date_str == "2024-07-25":
                return {"status": "no_data", "message": f"模擬：標的 {ticker} 在 {date_str} 無數據", "interpretation": "數據不足，無法解讀。"}
            return {"status": "no_data", "message": f"模擬：標的 {ticker} 在 {date_str} 無數據", "interpretation": "數據不足，無法解讀。"}

    class MockAnalysisEngineV2WithOptionsMenu(MockAnalysisEngineV2):
        def analyze_daily_options_data(self, date_str):
            print(f"模擬分析引擎V2+選項：正在分析選擇權數據 日期 {date_str}")
            if date_str == "2024-07-25":
                return {"status": "success", "put_call_volume_ratio": 1.05, "put_call_oi_ratio": 1.10}
            elif date_str == "2024-06-03":
                 return {"status": "success", "put_call_volume_ratio": 0.78, "put_call_oi_ratio": 0.95}
            return {"status": "no_data", "message": f"日期 {date_str} 無選擇權數據"}

    mock_analyzer_v2_options = MockAnalysisEngineV2WithOptionsMenu()

    mock_exec_log_v2 = {
        "2024-07-25": {
            "AAPL": {"status": "success", "interval": "1m", "count": 390, "message": "Final data for 2024-07-25 with 1m (390 rows)."},
            "GOOG": {"status": "success", "interval": "5m", "count": 78, "message": "Skipped 1m due to limit. Final data for 2024-07-25 with 5m (78 rows)."},
            "TSLA": {"status": "failed_all_intervals", "interval": None, "count": 0, "message": "All intervals failed for 2024-07-25."},
        },
        "2024-07-24": {
            "AAPL": {"status": "no_data_for_interval", "interval": "1d", "count": 0, "message": "No data found for 2024-07-24 with 1d after all chunks. Market closed?"},
            "MSFT": {"status": "success", "interval": "1h", "count": 7, "message": "Final data for 2024-07-24 with 1h (7 rows)."},
            "NVDA": {"status": "failed_chunk", "interval": "15m", "count": 0, "message": "Failed to fetch/process chunk covering 2024-07-24 with 15m."},
        },
        "2024-07-23": {
             "XYZ": {"status": "pending", "interval": None, "count": 0, "message": "Still pending"}
        }
    }

    reporter_v2_options = ReportGenerator(execution_log=mock_exec_log_v2,
                                          analysis_engine_instance=mock_analyzer_v2_options)

    report_start_date = "2024-07-23"
    report_end_date = "2024-07-25"
    overall_target_tickers = ["AAPL", "GOOG", "MSFT", "TSLA", "XYZ", "NVDA", "ADI"]

    print(f"\n--- 生成 v2.0 (含選擇權) 報告從 {report_start_date} 到 {report_end_date} ---")
    full_report_v2_options = reporter_v2_options.generate_full_report(
        overall_start_date_str=report_start_date,
        overall_end_date_str=report_end_date,
        report_generation_time=datetime(2024, 7, 26, 10, 0, 0),
        task_duration_seconds=123.45,
        target_tickers=overall_target_tickers,
        db_table_name="mock_ohlcv_data_v2"
    )

    print("\n--- v2.0 (含選擇權) 完整報告內容 ---")
    print(full_report_v2_options)

    assert "# 數據回填與市場分析報告" in full_report_v2_options
    assert f"- **分析標的**: {', '.join(overall_target_tickers)}" in full_report_v2_options
    assert "成功為 3 個標的獲取數據" in full_report_v2_options
    assert "主要獲取到的數據顆粒度包括：1m, 5m, 1h。" in full_report_v2_options

    report_day_2024_07_25 = full_report_v2_options.split("## 🗓️ 2024-07-25")[1].split("## 🗓️ 2024-07-24")[0]
    assert "#### 📜 本日數據盤點 (Data Inventory)" in report_day_2024_07_25
    assert "✅ **AAPL**: 成功獲取 **1m** 數據 (390 筆)." in report_day_2024_07_25
    assert "⚠️ **GOOG**: 降級至 **5m** 數據 (78 筆). *(註：1分鐘線數據超出回溯限制或不可用)*" in report_day_2024_07_25
    assert "❌ **TSLA**: 未能獲取到當日數據. *(註：所有嘗試均失敗或無數據)*" in report_day_2024_07_25
    assert "❔ **ADI**: 無當日處理記錄。" in report_day_2024_07_25
    assert "#### 📊 本日市場快照 (Market Snapshot)" in report_day_2024_07_25
    assert "| **AAPL** | 150.90 | +0.73% | 2.01% | 330,000 | 市場常規波動。 |" in report_day_2024_07_25
    assert "| **GOOG** | 2500.50 | +0.42% | 1.21% | 1,200,000 | 溫和波動，趨勢不明。 |" in report_day_2024_07_25
    assert "TSLA" not in report_day_2024_07_25.split("#### 📊 本日市場快照 (Market Snapshot)")[1]
    assert "#### 📈 選擇權市場情緒 (Options Market Sentiment)" in report_day_2024_07_25
    assert "- **Put/Call 成交量比 (Volume Ratio)**: 1.05" in report_day_2024_07_25
    assert "- **Put/Call 未平倉量比 (OI Ratio)**: 1.10" in report_day_2024_07_25
    assert "- *情緒解讀*: 看跌期權成交相對活躍，市場情緒偏謹慎。" in report_day_2024_07_25

    report_day_2024_07_24 = full_report_v2_options.split("## 🗓️ 2024-07-24")[1].split("## 🗓️ 2024-07-23")[0]
    assert "#### 📈 選擇權市場情緒 (Options Market Sentiment)" in report_day_2024_07_24
    assert f"- 日期 2024-07-24 無選擇權數據" in report_day_2024_07_24

    report_day_2024_07_23 = full_report_v2_options.split("## 🗓️ 2024-07-23")[1]
    assert "#### 📈 選擇權市場情緒 (Options Market Sentiment)" in report_day_2024_07_23
    assert f"- 日期 2024-07-23 無選擇權數據" in report_day_2024_07_23

    print("\n--- 報告生成器 (ReportGenerator) v2.0 (含選擇權) 測試完畢 ---")
