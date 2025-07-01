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
    parser = argparse.ArgumentParser(description="生成交易商壓力指數報告 (應用程式容器)。")
    parser.add_argument('--start-date', required=True, help="報告開始日期 (YYYY-MM-DD)")
    parser.add_argument('--end-date', required=True, help="報告結束日期 (YYYY-MM-DD)")
    parser.add_argument('--output-format', choices=['html', 'md', 'test_run'], default='html', help="輸出報告的格式")
    parser.add_argument('--no-charts', action='store_true', help="不生成視覺化圖表")
    parser.add_argument('--no-text', action='store_true', help="不生成文字分析 (將影響報告內容)")
    parser.add_argument('--use-ai-refine', action='store_true', help="使用 AI 潤飾文字分析 (需設定 Gemini API Key)")
    parser.add_argument('--config-path', default='config/project_config.yaml', help="設定檔路徑")
    args = parser.parse_args()

    logger.info(f"命令列參數: {vars(args)}")

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
    try:
        # === 階段一: 數據獲取 ===
        logger.info("##### [階段 1/5] 開始：數據獲取 #####")
        # 將 fred_api_key 傳遞給 data_fetcher
        fetched_data: FetchedData = data_fetcher.fetch_all_data(
            start_date_dt, end_date_dt, validated_config, fred_api_key, logger
        )
        logger.info(f"##### [階段 1/5] 完成：數據獲取。獲取到 merged_df 維度: {fetched_data.merged_df.shape} #####")

        # === 階段二: 指標計算 ===
        logger.info("##### [階段 2/5] 開始：指標計算 #####")
        calculated_data: CalculatedData = calculator.calculate_all_indicators(fetched_data, logger)
        logger.info(f"##### [階段 2/5] 完成：指標計算。計算後 final_df 維度: {calculated_data.final_df.shape} #####")

        # === 階段三: 視覺化 ===
        viz_data: VisualizationData
        if not args.no_charts:
            logger.info("##### [階段 3/5] 開始：視覺化 #####")
            viz_data = visualizer.create_all_visuals(calculated_data, logger)
            if viz_data.plotly_fig:
                logger.info("##### [階段 3/5] 完成：視覺化。Plotly 圖表已生成。#####")
            else:
                logger.warning("##### [階段 3/5] 完成：視覺化。但 Plotly 圖表未能生成。#####")
        else:
            logger.info("##### [階段 3/5] 跳過：視覺化 (因 --no-charts 參數)。#####")
            # 即使跳過圖表生成，也需要創建一個符合 VisualizationData 合約的物件以傳遞數據
            viz_data = VisualizationData(
                plotly_fig=None,
                final_df=calculated_data.final_df,
                config=calculated_data.config
            )

        # === 階段四: 報告數據準備 ===
        # reporter.prepare_report_data 負責生成文字分析，並將所有數據打包成 ReportData
        logger.info("##### [階段 4/5] 開始：報告數據準備 #####")
        final_report_data: ReportData = reporter.prepare_report_data(
            viz_data,
            no_text=args.no_text,
            use_ai_refine=args.use_ai_refine,
            gemini_api_key=gemini_api_key, # 從環境變數讀取
            logger_instance=logger
        )
        logger.info("##### [階段 4/5] 完成：報告數據準備。#####")


        # === 階段五: 編譯與儲存報告 ===
        if args.output_format == 'test_run':
            logger.info("##### [階段 5/5] 跳過：報告編譯與儲存 (因 --output-format=test_run)。#####")
            logger.info("應用程式容器 'test_run' 模式執行成功！")
        elif output_dir and base_report_filename: # 確保目錄和檔名已準備好
            logger.info(f"##### [階段 5/5] 開始：編譯與儲存報告 (格式: {args.output_format}) #####")
            report_path = reporter.compile_and_save_report(
                data=final_report_data,
                output_format=args.output_format,
                output_dir=output_dir, # 傳遞先前創建的目錄
                base_filename=base_report_filename, # 傳遞先前定義的檔名
                logger_instance=logger
            )
            if report_path:
                logger.info(f"##### [階段 5/5] 完成：報告已成功生成於: {report_path} #####")
            else:
                logger.error(f"##### [階段 5/5] 失敗：報告生成失敗。請檢查日誌。 #####")
                return 1 # 報告生成失敗也視為錯誤
        else:
            # 理論上不應該執行到這裡，因為 output_format 不是 test_run 時，output_dir 和 base_report_filename 應該已設定
            logger.error("錯誤：非 test_run 模式下，輸出目錄或檔名未正確設定。程式終止。")
            return 1

    except pydantic.ValidationError as e: # 捕獲流水線中 Pydantic 模型的驗證錯誤
        logger.error("錯誤：在應用程式流水線執行期間發生 Pydantic 數據合約驗證失敗。詳細資訊:")
        logger.error(e.json(indent=2))
        return 1
    except Exception as e: # 捕獲其他所有未預期錯誤
        logger.error(f"錯誤：應用程式執行期間發生未預期錯誤: {e}", exc_info=True) # exc_info=True 會記錄堆疊追蹤
        return 1

    logger.info(f"===== '{os.path.basename(__file__)}' 應用程式容器執行完畢 =====")
    return 0 # 返回 0 表示成功

if __name__ == '__main__':
    # 為了讓 _test_harness.py 能正確執行，這裡的 main() 返回值很重要
    # sys.exit(main()) # 可以考慮用 sys.exit 來傳遞返回碼
    exit_code = main()
    sys.exit(exit_code) # 確保腳本的退出碼是 main 函式的返回碼
