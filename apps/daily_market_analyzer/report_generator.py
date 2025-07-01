# -*- coding: utf-8 -*-
"""
報告生成模組 for 每日市場分析儀。
負責將數據抓取日誌和分析引擎的結果匯總成人類可讀的每日市場報告。
"""
import pandas as pd
from datetime import datetime

# 假設 analysis_engine.py 和 db_manager.py 在同一級或可被導入
# from .analysis_engine import AnalysisEngine # 如果在套件內
# from .db_manager import DBManager

class ReportGenerator:
    def __init__(self, execution_log: dict, analysis_engine_instance): #參數改為 analysis_engine_instance
        """
        初始化報告生成器 (ReportGenerator)。

        Args:
            execution_log (dict): 結構化的執行日誌，由 YFinanceClient.hydrate_data_range 生成。
                                  格式: {"YYYY-MM-DD": {"TICKER": {"status": ..., "interval": ..., "count": ...}}}
            analysis_engine_instance: AnalysisEngine 的一個實例。
        """
        self.execution_log = execution_log
        self.analyzer = analysis_engine_instance # 使用傳入的實例
        self.target_tickers_overall = [] # 將在 generate_full_report 中從 execution_log 推斷或由 run.py 傳入
        print("資訊：報告生成器 (ReportGenerator) 初始化完畢。")

    def _generate_header(self, overall_start_date_str: str, overall_end_date_str: str,
                         report_generation_time: datetime, task_duration_seconds: float) -> str:
        """生成報告的總體標頭部分。"""
        header_lines = [
            "==================================================",
            "        📈 每日市場洞察報告 (Daily Market Insights)",
            "==================================================",
            f"報告生成時間: {report_generation_time.strftime('%Y-%m-%d %H:%M:%S UTC%z')}",
            f"數據分析範圍: 從 {overall_start_date_str} 到 {overall_end_date_str}",
            f"總任務執行時長: {task_duration_seconds:.2f} 秒",
            f"分析標的列表: {', '.join(self.target_tickers_overall) if self.target_tickers_overall else '未指定'}",
            "--------------------------------------------------"
        ]
        return "\n".join(header_lines)

    def _generate_inventory_md(self, date_str: str) -> str:
        """生成指定日期的數據清單 Markdown 部分。"""
        lines = ["\n#### 📜 本日數據盤點 (Data Inventory)"]
        daily_log_for_date = self.execution_log.get(date_str, {})

        if not daily_log_for_date and not self.target_tickers_overall: # 如果當天沒日誌且也沒全局目標，才說無記錄
            lines.append(f"- {date_str}: 無任何標的之處理記錄。")
            return "\n".join(lines)
        elif not daily_log_for_date and self.target_tickers_overall: # 當天沒日誌，但有全局目標
             pass # 繼續遍歷 target_tickers_overall

        processed_tickers_in_log = list(daily_log_for_date.keys())

        # 確保所有在 target_tickers_overall 中的股票都被提及
        # 即使它們在當天的 execution_log 中沒有條目
        all_tickers_to_report_on = sorted(list(set(processed_tickers_in_log + self.target_tickers_overall)))


        for ticker in all_tickers_to_report_on:
            result = daily_log_for_date.get(ticker)
            if result:
                status = result.get('status', 'unknown')
                interval = result.get('interval', 'N/A')
                count = result.get('count', 0)
                message = result.get('message', '')

                if status == 'success' or status == 'success_partial':
                    lines.append(f"- ✅ **{ticker}**: 成功獲取 **{interval}** 數據 ({count} 筆). {message}")
                elif status == 'no_data_for_interval':
                     lines.append(f"- 🟡 **{ticker}**: 使用顆粒度 **{interval}** 嘗試後未發現數據. {message}")
                elif status == 'skipped_1m_due_to_30day_limit':
                    lines.append(f"- ⏩ **{ticker}**: 跳過 **1m** 數據 (因超出30天限制). {message}")
                elif status == 'failed_all_intervals':
                     lines.append(f"- ❌ **{ticker}**: 所有嘗試的顆粒度均未能獲取數據. {message}")
                elif status == 'failed_chunk':
                     lines.append(f"- ⚠️ **{ticker}**: 在顆粒度 **{interval}** 的某個區塊抓取失敗. {message}")
                else: # pending, or other statuses
                    lines.append(f"- ❓ **{ticker}**: 狀態未知或未完成 ({status}). {message}")
            elif ticker in self.target_tickers_overall: # Ticker is in overall list but not in today's log
                lines.append(f"- ❔ **{ticker}**: 在本日的執行日誌中無記錄 (可能未處理或無數據)。")
            # If ticker not in overall list and not in daily log, it's skipped (should not happen with current logic)


        if not all_tickers_to_report_on and not daily_log_for_date : #再次檢查是否真的無內容可報告
            lines.append(f"- {date_str}: 無任何標的之處理記錄或目標標的。")

        return "\n".join(lines)

    def _generate_snapshot_md(self, date_str: str, table_name: str) -> str:
        """生成指定日期的市場概覽 Markdown 部分。"""
        lines = ["\n#### 📊 本日市場快照 (Market Snapshot)"]
        daily_log_for_date = self.execution_log.get(date_str, {})

        tickers_to_analyze = [
            ticker for ticker, result in daily_log_for_date.items()
            if result.get('status') in ['success', 'success_partial'] and result.get('count', 0) > 0
        ]

        # 也考慮 self.target_tickers_overall 中，當天 execution_log 記錄為成功的股票
        # 這可以確保即使某股票數據是歷史補齊的，只要當天 execution_log 標為成功，就會嘗試分析
        additional_tickers_from_overall = [
            ticker for ticker in self.target_tickers_overall
            if ticker not in tickers_to_analyze and
               daily_log_for_date.get(ticker, {}).get('status') in ['success', 'success_partial'] and
               daily_log_for_date.get(ticker, {}).get('count', 0) > 0
        ]
        tickers_to_analyze = sorted(list(set(tickers_to_analyze + additional_tickers_from_overall)))


        if not tickers_to_analyze:
            lines.append("- 今日無成功獲取數據之標的以供分析。")
            return "\n".join(lines)

        # 創建 Markdown 表格
        header = "| 股票代號 | 收盤價 | 較前日% | 日內高點 | 日內低點 | 波動幅度% | 成交量 | 前日收盤 |"
        separator = "|---|---|---|---|---|---|---|---|"
        lines.append(header)
        lines.append(separator)

        for ticker in tickers_to_analyze: # Iterating over sorted list
            # print(f"DEBUG: ReportGenerator: Analyzing {ticker} for snapshot on {date_str}")
            analysis = self.analyzer.analyze_daily_ticker_data(ticker, date_str, table_name)
            if analysis and analysis.get('status') == 'success':
                lines.append(
                    f"| **{ticker}** | {analysis.get('close', 'N/A')} | {analysis.get('change_pct', 'N/A')} | "
                    f"{analysis.get('high', 'N/A')} | {analysis.get('low', 'N/A')} | {analysis.get('range_pct', 'N/A')} | "
                    f"{analysis.get('volume', 'N/A')} | {analysis.get('prev_close', 'N/A')} |"
                )
            else: # 分析失敗或 ticker 在 execution_log 中是成功但分析時無數據 (理論上不應發生，除非DB問題)
                lines.append(f"| **{ticker}** | *分析失敗或無數據* | N/A | N/A | N/A | N/A | N/A | N/A |")

        # 如果 target_tickers_overall 中有的股票沒有出現在 snapshot 中 (因為 execution_log 中不是 success)
        # 可以在這裡補充一行說明它們為何未被分析，但 inventory 已經涵蓋了此資訊。

        return "\n".join(lines)

    def _generate_daily_section(self, date_str: str, table_name: str, target_tickers_for_run: list[str]) -> str:
        """生成單日的報告內容 (數據清單 + 市場快照)。"""
        # self.target_tickers_overall 已經在 generate_full_report 設定
        inventory_md = self._generate_inventory_md(date_str)
        snapshot_md = self._generate_snapshot_md(date_str, table_name)

        return f"\n## 🗓️ {date_str}\n{inventory_md}\n{snapshot_md}"

    def generate_full_report(self, overall_start_date_str: str, overall_end_date_str: str,
                             report_generation_time: datetime, task_duration_seconds: float,
                             target_tickers: list[str], db_table_name: str) -> str:
        """
        生成指定日期範圍的完整市場分析報告。

        Args:
            overall_start_date_str (str): 任務的總體開始日期 (YYYY-MM-DD)。
            overall_end_date_str (str): 任務的總體結束日期 (YYYY-MM-DD)。
            report_generation_time (datetime): 報告生成的時間戳。
            task_duration_seconds (float): 整個任務的執行時長（秒）。
            target_tickers (list[str]): 本次運行中用戶指定的股票代號列表。
            db_table_name (str): 分析時使用的資料庫表名。

        Returns:
            str: 格式化後的完整報告文字 (Markdown)。
        """
        self.target_tickers_overall = sorted(list(set(target_tickers))) # 設定本次報告的目標股票列表

        report_parts = [self._generate_header(overall_start_date_str, overall_end_date_str,
                                              report_generation_time, task_duration_seconds)]

        # 按日期倒序生成報告 (最新的日期在最前面)
        # 使用 pandas.date_range 來處理日期迭代
        try:
            date_range = pd.date_range(start=overall_start_date_str, end=overall_end_date_str, freq='D').sort_values(ascending=False)
        except Exception as e:
            print(f"錯誤：生成日期範圍時發生錯誤：{e}")
            report_parts.append(f"\n錯誤：無法生成從 {overall_start_date_str} 到 {overall_end_date_str} 的日期範圍報告。")
            return "\n\n---\n\n".join(report_parts)

        if date_range.empty and overall_start_date_str == overall_end_date_str: # Handle single day range
             date_range = pd.to_datetime([overall_start_date_str])


        for date_obj in date_range:
            date_str = date_obj.strftime('%Y-%m-%d')
            # print(f"DEBUG: ReportGenerator: Generating daily section for {date_str}")
            # target_tickers (即 self.target_tickers_overall) 會在 _generate_inventory_md 和 _generate_snapshot_md 中被參考
            daily_report_md = self._generate_daily_section(date_str, db_table_name, target_tickers)
            report_parts.append(daily_report_md)

        final_report_text = "\n\n---\n\n".join(report_parts) # 使用更明顯的分隔符
        # print(final_report_text) # 移除此處的打印，由 run.py 決定是否打印
        return final_report_text

if __name__ == '__main__':
    print("--- 報告生成器 (ReportGenerator) 測試 ---")

    # 模擬 AnalysisEngine (因為它依賴 DBManager)
    class MockAnalysisEngine:
        def analyze_daily_ticker_data(self, ticker, date_str, table_name="mock_table"):
            print(f"模擬分析引擎：正在分析標的 {ticker} 日期 {date_str}")
            if ticker == "AAPL" and date_str == "2024-07-25":
                return {"status": "success", "close": "150.90", "prev_close": "149.80", "change_pct": "+0.73%",
                        "high": "152.00", "low": "149.00", "range_pct": "2.01%", "volume": "330,000"}
            if ticker == "GOOG" and date_str == "2024-07-25":
                 return {"status": "success", "close": "2500.50", "prev_close": "2490.00", "change_pct": "+0.42%",
                        "high": "2510.00", "low": "2480.00", "range_pct": "1.21%", "volume": "1,200,000"}
            if ticker == "MSFT" and date_str == "2024-07-24": # Different date
                 return {"status": "success", "close": "300.00", "prev_close": "298.00", "change_pct": "+0.67%",
                        "high": "301.00", "low": "297.00", "range_pct": "1.35%", "volume": "900,000"}
            return {"status": "no_data", "message": f"模擬：標的 {ticker} 在 {date_str} 無數據"}

    mock_analyzer = MockAnalysisEngine()

    # 模擬 execution_log
    mock_exec_log = {
        "2024-07-25": {
            "AAPL": {"status": "success", "interval": "1m", "count": 390, "message": "Fetched 1m data."},
            "GOOG": {"status": "success", "interval": "5m", "count": 78, "message": "Fetched 5m data."},
            "TSLA": {"status": "failed_all_intervals", "interval": None, "count": 0, "message": "All intervals failed."},
        },
        "2024-07-24": {
            "AAPL": {"status": "no_data_for_interval", "interval": "1d", "count": 0, "message": "No 1d data found."},
            "MSFT": {"status": "success", "interval": "1h", "count": 7, "message": "Hourly data fetched."},
            "NVDA": {"status": "success_partial", "interval": "15m", "count": 10, "message": "Partial 15m data."} # NVDA only has log on this day
        },
        "2024-07-23": { # 日期在範圍內，但可能沒有任何 ticker 的日誌
             "XYZ": {"status": "pending", "interval": None, "count": 0, "message": "Still pending"}
        }
    }

    reporter = ReportGenerator(execution_log=mock_exec_log, analysis_engine_instance=mock_analyzer)

    report_start_date = "2024-07-23"
    report_end_date = "2024-07-25"
    overall_target_tickers = ["AAPL", "GOOG", "MSFT", "TSLA", "XYZ", "NVDA"] # NVDA 完全沒有出現在 log 中

    print(f"\n--- 生成從 {report_start_date} 到 {report_end_date} 的報告 ---")
    full_report = reporter.generate_full_report(
        overall_start_date_str=report_start_date,
        overall_end_date_str=report_end_date,
        report_generation_time=datetime.now(),
        task_duration_seconds=123.45,
        target_tickers=overall_target_tickers,
        db_table_name="mock_ohlcv_data"
    )

    # 打印報告以供手動檢查
    print("\n--- 完整報告內容 ---")
    print(full_report)

    # 簡單驗證報告中是否包含特定日期和股票的資訊
    assert "🗓️ 2024-07-25" in full_report
    assert "AAPL" in full_report and "1m" in full_report and "390" in full_report # AAPL on 25th
    assert "TSLA" in full_report and "所有嘗試的顆粒度均未能獲取數據" in full_report # TSLA on 25th
    assert "NVDA" in full_report and "在本日的執行日誌中無記錄" in full_report and "2024-07-25" in full_report # NVDA on 25th (no log)

    assert "🗓️ 2024-07-24" in full_report
    assert "MSFT" in full_report and "1h" in full_report # MSFT on 24th
    assert "NVDA" in full_report and "15m" in full_report and "2024-07-24" in full_report # NVDA on 24th (has log)

    assert "🗓️ 2024-07-23" in full_report
    assert "XYZ" in full_report and "狀態未知或未完成" in full_report # XYZ on 23rd
    assert "市場快照" in full_report
    assert "| **AAPL** | 150.90 | +0.73% |" in full_report # 驗證 snapshot 內容 for AAPL on 25th
    assert "| **NVDA** | *分析失敗或無數據* | N/A | N/A | N/A | N/A | N/A | N/A |" not in full_report # NVDA on 25th should not be in snapshot
    # More specific check for NVDA on 2024-07-24 snapshot - needs MockAnalysisEngine to return data for NVDA
    # For now, we assume if it's in execution_log as success_partial, it would be attempted for analysis.

    print("\n--- 報告生成器 (ReportGenerator) 測試完畢 ---")
