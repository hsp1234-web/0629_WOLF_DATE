# -*- coding: utf-8 -*-
# 應用程式: 壓力指數報告生成器
# 版本: 1.1 (新增路徑自我校正與絕對導入)
# 日期: 2025-06-30

import os
import sys
import argparse
from datetime import datetime
import pandas as pd
import numpy as np

# ==============================================================================
# ✅ FIX v1.1: 路徑自我校正 (Path Self-Correction)
# ------------------------------------------------------------------------------
# 此段程式碼確保無論從何處執行此腳本，都能將專案根目錄加入系統路徑，
# 從而解決所有 'ImportError: attempted relative import with no known parent package' 問題。
# 這是實現「密封測試」與「路徑獨立性」的關鍵。
# ==============================================================================
def add_project_root_to_path():
    """尋找專案根目錄 (包含 .git 的目錄) 並將其加入 sys.path"""
    current_path = os.path.abspath(os.path.dirname(__file__))
    project_root = current_path
    # 向上遍歷目錄，直到找到 .git 資料夾或到達根目錄
    while not os.path.exists(os.path.join(project_root, '.git')):
        parent_path = os.path.dirname(project_root)
        if parent_path == project_root: # 已到達檔案系統根目錄
            print("警告：無法定位專案根目錄 (.git)，模組導入可能失敗。")
            return
        project_root = parent_path

    if project_root not in sys.path:
        sys.path.insert(0, project_root)
        print(f"✅ 專案根目錄 '{project_root}' 已成功加入系統路徑。")

# 在所有導入之前執行路徑校正
add_project_root_to_path()
# ==============================================================================

# ✅ FIX v1.1: 將相對導入改為絕對導入
from apps.stress_report_app.data_fetcher import (
    get_fred_base_data, get_yahoo_other_data, get_move_index,
    get_vix_index, fetch_nyfed_data
)
from apps.stress_report_app.calculator import (
    calculate_derived_indicators, calculate_stress_index, calculate_macd_momentum
)
from apps.stress_report_app.visualizer import create_stress_dashboard_plotly
from apps.stress_report_app.reporter import generate_text_analysis, compile_html_report
from src.utils.config_loader import load_project_config
from src.utils.logger import setup_logger

def main(args):
    """主執行函式"""
    # 設置日誌
    logger = setup_logger("StressReportApp")
    logger.info(f"===== 開始執行 '{os.path.basename(__file__)}' App (v3.0) =====")

    # 將參數轉換為字典以便記錄
    event_params = {
        "start_date": args.start_date,
        "end_date": args.end_date,
        "output_format": args.output_format,
        "no_charts": args.no_charts,
        "no_text": args.no_text,
        "use_ai_refine": args.use_ai_refine
    }
    logger.info(f"事件參數: {event_params}")

    # 載入設定檔
    try:
        config = load_project_config('config/project_config.yaml')
        logger.info("設定檔 'config/project_config.yaml' 載入成功。")
    except FileNotFoundError:
        logger.error("錯誤：找不到設定檔 'config/project_config.yaml'。")
        return

    # 讀取 API 金鑰
    fred_api_key = os.getenv('API_KEY_FRED')
    gemini_api_key = os.getenv('API_KEY_GEMINI', '')
    if not fred_api_key:
        logger.error("錯誤：未在環境變數中找到 'API_KEY_FRED'。")
        return
    logger.info("成功從環境變數 'API_KEY_FRED' 讀取 FRED API 金鑰。")

    # 處理日期
    start_date_dt = datetime.strptime(args.start_date, '%Y-%m-%d')
    end_date_dt = datetime.strptime(args.end_date, '%Y-%m-%d')
    logger.info(f"使用事件參數提供的日期範圍: {args.start_date} 至 {args.end_date}")

    # 建立輸出目錄
    run_timestamp = datetime.now().strftime('%Y-%m-%d_%H%M%S')
    output_dir = f"data_workspace/output/reports/{run_timestamp}_run"
    os.makedirs(output_dir, exist_ok=True)
    logger.info(f"報告將輸出至目錄: {output_dir}")

    # --- 階段一：數據獲取 ---
    logger.info("##### 階段：數據獲取 #####")
    fred_base_df = get_fred_base_data(fred_api_key, start_date_dt, end_date_dt, config)
    yahoo_other_df = get_yahoo_other_data(start_date_dt, end_date_dt, config)
    move_series = get_move_index(fred_api_key, start_date_dt, end_date_dt, config)
    vix_series = get_vix_index(fred_api_key, start_date_dt, end_date_dt, config)
    nyfed_series = fetch_nyfed_data(config)

    # --- 合併所有數據源 ---
    logger.info("--- 正在合併所有數據源 ---")
    base_idx = pd.date_range(start=start_date_dt, end=end_date_dt, freq='B')
    merged_df = pd.DataFrame(index=base_idx)

    if fred_base_df is not None and not fred_base_df.empty:
        merged_df = merged_df.join(fred_base_df, how='left')
    if yahoo_other_df is not None and not yahoo_other_df.empty:
        merged_df = merged_df.join(yahoo_other_df, how='left')
    if move_series is not None and not move_series.empty:
        merged_df['Volatility_Index'] = move_series.reindex(base_idx, method='ffill')
    else:
        merged_df['Volatility_Index'] = np.nan
    if vix_series is not None and not vix_series.empty:
        merged_df['VIX'] = vix_series.reindex(base_idx, method='ffill')
    else:
        merged_df['VIX'] = np.nan
    if nyfed_series is not None and not nyfed_series.empty:
        nyfed_df_temp = nyfed_series.to_frame(name='Total_Gross_Positions_Millions')
        merged_df = merged_df.join(nyfed_df_temp, how='left')
        if 'Total_Gross_Positions_Millions' in merged_df.columns:
            merged_df['Total_Gross_Positions_Millions'].ffill(inplace=True)
    else:
        merged_df['Total_Gross_Positions_Millions'] = np.nan

    logger.info(f"數據合併完成。合併後 DataFrame 維度: {merged_df.shape}")
    if merged_df.isna().all().all():
        logger.critical("CRITICAL: 所有數據源獲取失敗或合併後數據全為 NaN，無法繼續生成報告。")
        return

    # --- 階段二：指標計算 ---
    logger.info("##### 階段：指標計算 #####")
    calculated_df = calculate_derived_indicators(merged_df.copy())
    calculated_df = calculate_stress_index(calculated_df, config)
    final_df = calculate_macd_momentum(calculated_df, config)
    logger.info(f"指標計算完成。最終 DataFrame 維度: {final_df.shape}")

    # --- 階段三：圖表生成 ---
    plotly_fig = None
    if not args.no_charts:
        logger.info("##### 階段：圖表生成 (Plotly) #####")
        # 傳遞 date_range 給 create_stress_dashboard_plotly
        plotly_fig = create_stress_dashboard_plotly(final_df, config, date_range=(start_date_dt, end_date_dt))
        if plotly_fig:
            logger.info("Plotly 組合式儀表板 Figure 物件已生成。")
        else:
            logger.warning("Plotly 儀表板生成失敗或返回空物件。")

    # --- 階段四：報告生成 ---
    if args.output_format in ['html', 'md']:
        logger.info("##### 階段：報告生成 #####")
        text_analysis_content = ""
        if not args.no_text:
            text_analysis_content = generate_text_analysis(final_df, config)

        # AI 文字潤飾
        if args.use_ai_refine and text_analysis_content:
            if gemini_api_key:
                logger.info("啟用 AI 文字潤飾...")
                # 這裡可以加入呼叫 Gemini API 的邏輯
                # from some_gemini_module import refine_text
                # text_analysis_content = refine_text(text_analysis_content, gemini_api_key)
                logger.info("AI 文字潤飾完成 (模擬)。") # 暫時模擬
            else:
                logger.warning("已請求 AI 潤飾，但缺少 Gemini API 金鑰，跳過此步驟。")
        else:
            logger.info("跳過 AI 文字潤飾 (未啟用或缺少 Gemini API 金鑰)。")

        # 編譯報告
        report_path = compile_html_report(
            final_df,
            plotly_fig,
            text_analysis_content,
            config,
            output_dir,
            args.output_format
        )
        logger.info(f"最終 {args.output_format.upper()} 報告已成功生成於: {report_path}")

    logger.info(f"===== '{os.path.basename(__file__)}' App (v3.0) 執行完畢 =====")
    logger.info(f"所有輸出檔案位於: {output_dir}")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="生成交易商壓力指數報告。")
    parser.add_argument('--start-date', required=True, help="報告開始日期 (YYYY-MM-DD)")
    parser.add_argument('--end-date', required=True, help="報告結束日期 (YYYY-MM-DD)")
    parser.add_argument('--output-format', choices=['html', 'md', 'test_run'], default='html', help="輸出報告的格式")
    parser.add_argument('--no-charts', action='store_true', help="不生成視覺化圖表")
    parser.add_argument('--no-text', action='store_true', help="不生成文字分析")
    parser.add_argument('--use-ai-refine', action='store_true', help="使用 AI 潤飾文字分析")

    args = parser.parse_args()
    main(args)
