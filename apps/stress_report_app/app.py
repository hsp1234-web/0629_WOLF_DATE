# -*- coding: utf-8 -*-
# 應用程式容器: 壓力指數報告生成器 (app.py)
# 版本: 3.0 (SOP v3.0 - App-in-a-Box)
# 日期: 2025-06-30 (重構日期)

import os
import sys # 添加 sys 導入
import argparse
from datetime import datetime
import pandas as pd # 保留以備 Pydantic 模型實例化時可能需要 (儘管在此檔案中不直接操作 DF)
import pydantic # 用於 ValidationError
import yaml # 用於載入設定檔

# 專案模組導入
# 當使用 `python -m apps.stress_report_app._test_harness` 執行時，
# Python 會將 `apps/stress_report_app` 的父目錄 (即 `apps/`) 加入到 sys.path，
# 因此，對於 `apps` 目錄下的其他模組，可以直接用 `apps.module` 的方式導入。
# 對於 `src` 下的模組，如果 `PROJECT_ROOT` 已被 `_test_harness.py` 加入 `sys.path`，
# 則 `from src.utils.logger import setup_logger` 應該是有效的。

# 為了在 `python -m ...` 和潛在的直接執行 `app.py` (如果PYTHONPATH正確設置) 兩種情況下都能工作，
# 且更符合 Python 包的規範，我們優先使用絕對導入路徑 (相對於專案根目錄)。
# _test_harness.py 負責確保專案根目錄在 sys.path 中。

from apps.stress_report_app.schemas import (
    AppConfig, FetchedData, CalculatedData, VisualizationData, ReportData
)
import apps.stress_report_app.data_fetcher as data_fetcher
import apps.stress_report_app.calculator as calculator
import apps.stress_report_app.visualizer as visualizer
import apps.stress_report_app.reporter as reporter

from src.utils.logger import setup_logger


def main():
    """應用程式主執行函式，實現 App-in-a-Box 流水線"""
    # 1. 設置日誌 (應在所有操作之前)
    logger = setup_logger("StressReportApp_Container") # 使用新的日誌名稱
    logger.info(f"===== 開始執行 '{os.path.basename(__file__)}' 應用程式容器 (SOP v3.0) =====")

    # 2. 解析命令列參數
    parser = argparse.ArgumentParser(description="全景市場分析儀 (應用程式容器)。")
    # 模式選擇
    parser.add_argument('--mode', choices=['macro', 'snapshot'], default='macro', help="執行模式：'macro' (每日宏觀趨勢報告) 或 'snapshot' (高頻市場快照分析)")

    # 宏觀報告模式參數 (舊有參數，部分調整為非必須)
    parser.add_argument('--start-date', required=False, help="報告開始日期 (YYYY-MM-DD)，宏觀模式下需要")
    parser.add_argument('--end-date', required=False, help="報告結束日期 (YYYY-MM-DD)，宏觀模式下需要")

    # 高頻快照模式參數
    parser.add_argument('--enable-yfinance', action='store_true', help="快照模式下：啟用 yfinance 分鐘級數據")
    parser.add_argument('--tickers', type=str, default='^VIX,SPY,TLT', help='快照模式下：關注的標的 (以逗號分隔)')
    parser.add_argument('--interval', type=str, default='5m', help='快照模式下：yfinance 數據顆粒度 (例如 1m, 5m, 1h)')
    parser.add_argument('--period', type=str, default='1d', help='快照模式下：yfinance 回溯週期 (例如 1d, 5d, 7d)')
    parser.add_argument('--process-uploads', action='store_true', help="快照模式下：處理手動上傳的交易所檔案")

    # 通用參數
    parser.add_argument('--output-format', choices=['html', 'md', 'test_run'], default='html', help="輸出報告的格式")
    parser.add_argument('--no-charts', action='store_true', help="不生成視覺化圖表 (主要影響宏觀模式)")
    parser.add_argument('--no-text', action='store_true', help="不生成文字分析 (主要影響宏觀模式)")
    parser.add_argument('--use-ai-refine', action='store_true', help="使用 AI 潤飾文字分析 (主要影響宏觀模式)")
    parser.add_argument('--config-path', default='config/project_config.yaml', help="設定檔路徑")
    parser.add_argument('--debug', action='store_true', help="啟用除錯模式")
    args = parser.parse_args()

    logger.info(f"命令列參數: {vars(args)}")

    # 驗證參數組合
    if args.mode == 'macro' and (not args.start_date or not args.end_date):
        logger.error("錯誤：宏觀模式 (--mode=macro) 需要 --start-date 和 --end-date 參數。")
        return 1

    # 3. 載入並驗證設定檔
    try:
        with open(args.config_path, 'r', encoding='utf-8') as f:
            raw_config = yaml.safe_load(f)
        validated_config = AppConfig(**raw_config)
        logger.info(f"設定檔 '{args.config_path}' 載入並通過 Pydantic 驗證成功。")
    except FileNotFoundError:
        logger.error(f"錯誤：找不到設定檔 '{args.config_path}'。程式終止。")
        return 1 # 返回非零表示錯誤
    except pydantic.ValidationError as e:
        logger.error(f"錯誤：設定檔 '{args.config_path}' 驗證失敗。詳細資訊:")
        logger.error(e.json(indent=2)) #確保繁體中文能正確顯示
        return 1
    except yaml.YAMLError as e:
        logger.error(f"錯誤：解析設定檔 '{args.config_path}' 時發生 YAML 錯誤: {e}。程式終止。")
        return 1


    # 讀取 API 金鑰 (FRED API Key 是必須的)
    # 注意: Gemini API Key 是可選的，reporter 模組會處理其缺失情況
    fred_api_key = os.getenv('API_KEY_FRED')
    gemini_api_key = os.getenv('API_KEY_GEMINI') # reporter.py 會處理 None 的情況

    if not fred_api_key:
        logger.error("錯誤：未在環境變數中找到 'API_KEY_FRED'。數據獲取階段將失敗。程式終止。")
        return 1
    logger.info("成功從環境變數 'API_KEY_FRED' 讀取 FRED API 金鑰。")
    if gemini_api_key:
        logger.info("已讀取環境變數 'API_KEY_GEMINI'。")
    else:
        logger.info("環境變數 'API_KEY_GEMINI' 未設定 (AI 潤飾功能將不可用)。")


    # 處理日期 (字串轉 datetime 物件)
    try:
        start_date_dt = datetime.strptime(args.start_date, '%Y-%m-%d')
        end_date_dt = datetime.strptime(args.end_date, '%Y-%m-%d')
        logger.info(f"日期範圍: {start_date_dt.strftime('%Y-%m-%d')} 至 {end_date_dt.strftime('%Y-%m-%d')}")
    except ValueError:
        logger.error("錯誤：日期格式不正確，請使用 YYYY-MM-DD。程式終止。")
        return 1

    # 準備輸出目錄 (如果不是 test_run 模式)
    output_dir = None
    base_report_filename = None
    if args.output_format != 'test_run':
        run_timestamp = datetime.now().strftime('%Y-%m-%d_%H%M%S')
        # 確保路徑是相對於專案根目錄 (假設 app.py 在 apps/stress_report_app/ 下)
        # 輸出目錄應由 reporter.compile_and_save_report 內部創建或接收參數
        # 這裡先定義基礎名稱，完整路徑在 compile_and_save_report 階段處理
        output_dir_base = "data_workspace/output/reports" # 相對於專案根目錄
        output_dir = os.path.join(output_dir_base, f"stress_report_{run_timestamp}_{args.output_format}")
        base_report_filename = f"stress_report_{run_timestamp}"
        try:
            os.makedirs(output_dir, exist_ok=True)
            logger.info(f"報告將輸出至目錄 (如果生成): {output_dir}")
        except OSError as e:
            logger.error(f"錯誤：無法創建輸出目錄 '{output_dir}': {e}。程式終止。")
            return 1


    # 4. 執行流水線 (Pipeline)
    if args.mode == 'macro':
        logger.info("##### 執行宏觀報告模式 #####")
        try:
            # === 階段一: 數據獲取 ===
            logger.info("##### [階段 1/5] 開始：宏觀數據獲取 #####")
            fetched_data: FetchedData = data_fetcher.fetch_all_data(
                start_date_dt, end_date_dt, validated_config, fred_api_key, logger
            )
            logger.info(f"##### [階段 1/5] 完成：宏觀數據獲取。獲取到 merged_df 維度: {fetched_data.merged_df.shape} #####")

            # === 階段二: 指標計算 ===
            logger.info("##### [階段 2/5] 開始：宏觀指標計算 #####")
            calculated_data: CalculatedData = calculator.calculate_all_indicators(fetched_data, logger)
            logger.info(f"##### [階段 2/5] 完成：宏觀指標計算。計算後 final_df 維度: {calculated_data.final_df.shape} #####")

            # === 階段三: 視覺化 ===
            viz_data: VisualizationData
            if not args.no_charts:
                logger.info("##### [階段 3/5] 開始：宏觀視覺化 #####")
                viz_data = visualizer.create_all_visuals(calculated_data, logger)
                if viz_data.plotly_fig:
                    logger.info("##### [階段 3/5] 完成：宏觀視覺化。Plotly 圖表已生成。#####")
                else:
                    logger.warning("##### [階段 3/5] 完成：宏觀視覺化。但 Plotly 圖表未能生成。#####")
            else:
                logger.info("##### [階段 3/5] 跳過：宏觀視覺化 (因 --no-charts 參數)。#####")
                viz_data = VisualizationData(
                    plotly_fig=None,
                    final_df=calculated_data.final_df,
                    config=calculated_data.config
                )

            # === 階段四: 報告數據準備 ===
            logger.info("##### [階段 4/5] 開始：宏觀報告數據準備 #####")
            final_report_data: ReportData = reporter.prepare_report_data(
                viz_data,
                no_text=args.no_text,
                use_ai_refine=args.use_ai_refine,
                gemini_api_key=gemini_api_key,
                logger_instance=logger
            )
            logger.info("##### [階段 4/5] 完成：宏觀報告數據準備。#####")

            # === 階段五: 編譯與儲存報告 ===
            if args.output_format == 'test_run':
                logger.info("##### [階段 5/5] 跳過：宏觀報告編譯與儲存 (因 --output-format=test_run)。#####")
                logger.info("宏觀模式 'test_run' 執行成功！")
            elif output_dir and base_report_filename:
                logger.info(f"##### [階段 5/5] 開始：編譯與儲存宏觀報告 (格式: {args.output_format}) #####")
                report_path = reporter.compile_and_save_report(
                    data=final_report_data,
                    output_format=args.output_format,
                    output_dir=output_dir,
                    base_filename=base_report_filename,
                    logger_instance=logger
                )
                if report_path:
                    logger.info(f"##### [階段 5/5] 完成：宏觀報告已成功生成於: {report_path} #####")
                else:
                    logger.error(f"##### [階段 5/5] 失敗：宏觀報告生成失敗。請檢查日誌。 #####")
                    return 1
            else:
                logger.error("錯誤：宏觀模式下，非 test_run 模式輸出目錄或檔名未正確設定。程式終止。")
                return 1

        except pydantic.ValidationError as e:
            logger.error("錯誤：宏觀模式執行期間發生 Pydantic 數據合約驗證失敗。詳細資訊:")
            logger.error(e.json(indent=2))
            return 1
        except Exception as e:
            logger.error(f"錯誤：宏觀模式執行期間發生未預期錯誤: {e}", exc_info=True)
            return 1

    elif args.mode == 'snapshot':
        logger.info("##### 執行高頻快照模式 #####")
        logger.info("注意：高頻快照模式的核心數據處理邏輯尚未完全實現。")
        if args.enable_yfinance:
            logger.info(f"  --enable-yfinance: True")
            logger.info(f"  --tickers: {args.tickers}")
            logger.info(f"  --interval: {args.interval}")
            logger.info(f"  --period: {args.period}")
            # 此處應有呼叫高頻數據收集器模組的邏輯
            logger.info("  (示意) 應呼叫 yfinance 高頻數據收集器...")

        if args.process_uploads:
            logger.info(f"  --process-uploads: True")
            # 此處應有呼叫手動檔案處理器模組的邏輯
            logger.info("  (示意) 應呼叫手動上傳檔案處理器...")

        if not args.enable_yfinance and not args.process_uploads:
            logger.warning("警告：快照模式已選擇，但未啟用 yfinance 數據且未要求處理上傳檔案。無數據源可處理。")
            # 根據需求，這裡可以選擇生成一個空的報告或直接退出
            # 暫時模擬生成一個簡單的提示性報告
            if args.output_format != 'test_run' and output_dir and base_report_filename:
                snapshot_placeholder_content = f"# 高頻快照報告 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})\n\n"
                snapshot_placeholder_content += "模式：快照\n\n"
                snapshot_placeholder_content += "**警告：未選擇任何數據源 (yfinance 或手動上傳)。**\n"

                report_file_path = os.path.join(output_dir, f"{base_report_filename}.{args.output_format if args.output_format != 'html' else 'txt'}") # 簡化處理
                try:
                    with open(report_file_path, 'w', encoding='utf-8') as f:
                        f.write(snapshot_placeholder_content)
                    logger.info(f"已生成佔位快照報告: {report_file_path}")
                except Exception as e:
                    logger.error(f"儲存佔位快照報告時出錯: {e}")
                    return 1
            elif args.output_format == 'test_run':
                 logger.info("快照模式 'test_run' (無數據源) 執行完畢。")

        # 此處未來應有快照數據分析、可視化和報告生成的邏輯
        logger.info("高頻快照模式 (示意流程) 執行完畢。")

    logger.info(f"===== '{os.path.basename(__file__)}' 應用程式容器執行完畢 =====")
    return 0

if __name__ == '__main__':
    # 為了讓 _test_harness.py 能正確執行，這裡的 main() 返回值很重要
    # sys.exit(main()) # 可以考慮用 sys.exit 來傳遞返回碼
    exit_code = main()
    sys.exit(exit_code) # 確保腳本的退出碼是 main 函式的返回碼
