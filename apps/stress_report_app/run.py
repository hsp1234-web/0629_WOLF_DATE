# -*- coding: utf-8 -*-
"""
應用程式主執行檔案：21_generate_dealer_stress_report (v3.0)

功能：
- 讀取設定檔 (config/project_config.yaml)
- 根據事件參數或設定檔決定日期範圍
- 依序呼叫 data_fetcher.py 中的函式來獲取所有數據 (含備援邏輯)
- 呼叫 calculator.py 中的函式來計算所有指標
- 呼叫 visualizer.py 中的函式 (使用 Plotly)，生成互動式圖表物件
- 呼叫 reporter.py 中的函式，組合文字分析、圖表 (HTML)，並 (可選) 呼叫 Gemini API 潤飾，最終生成 HTML 報告檔案。
"""

import yaml
import pandas as pd
from datetime import datetime, timedelta
import os
import logging
from typing import Dict, Any

# 假設本 App 的模組在 apps.21_generate_dealer_stress_report 路徑下
from .data_fetcher import (
    fetch_fred_data,
    fetch_yahoo_data,
    fetch_nyfed_data,
    get_move_index, # 新增：使用備援邏輯的 MOVE 指數獲取
    get_vix_index   # 新增：使用備援邏輯的 VIX 指數獲取
)
from .calculator import (
    calculate_derived_indicators,
    calculate_stress_index,
    calculate_macd_momentum
)
from .visualizer import (
    create_stress_dashboard_plotly, # 新增：Plotly 儀表板
    export_plotly_fig # 新增：Plotly 圖表匯出工具
)
from .reporter import (
    generate_text_analysis,
    call_gemini_api,
    compile_html_report
)


# --- 日誌設定 ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - [%(levelname)s] - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("StressReportApp") # 給 logger 一個明確的名稱

# --- 常數 ---
DEFAULT_CONFIG_PATH = "config/project_config.yaml"
DEFAULT_OUTPUT_DIR_BASE = "data_workspace/output/reports"

def load_config(config_path: str = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    """載入 YAML 設定檔。"""
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        logger.info(f"設定檔 '{config_path}' 載入成功。")
        return config
    except FileNotFoundError:
        logger.error(f"CRITICAL: 設定檔 '{config_path}' 未找到。應用程式無法執行。")
        raise
    except yaml.YAMLError as e:
        logger.error(f"CRITICAL: 解析設定檔 '{config_path}' 失敗: {e}。應用程式無法執行。")
        raise
    except Exception as e:
        logger.error(f"CRITICAL: 載入設定檔 '{config_path}' 時發生未預期錯誤: {e}。應用程式無法執行。")
        raise

def determine_date_range(event_params: Dict[str, Any], app_config: Dict[str, Any]) -> tuple[datetime, datetime]:
    """根據事件參數和應用程式設定決定分析的日期範圍。"""
    today = datetime.now()
    start_date_str = event_params.get('start_date')
    end_date_str = event_params.get('end_date')

    if start_date_str and end_date_str:
        try:
            start_dt = pd.to_datetime(start_date_str).to_pydatetime(warn=False)
            end_dt = pd.to_datetime(end_date_str).to_pydatetime(warn=False)
            if start_dt >= end_dt:
                logger.warning(f"事件參數中開始日期 ({start_date_str})晚於或等於結束日期 ({end_date_str})。將使用預設。")
            else:
                logger.info(f"使用事件參數提供的日期範圍: {start_dt.strftime('%Y-%m-%d')} 至 {end_dt.strftime('%Y-%m-%d')}")
                return start_dt, end_dt
        except ValueError:
            logger.warning(f"事件參數中的日期格式無效 ('{start_date_str}', '{end_date_str}')，將嘗試設定檔。")

    default_range_cfg = app_config.get('default_date_range', {})
    conf_start_str = default_range_cfg.get('start_date')
    conf_end_str = default_range_cfg.get('end_date')

    if conf_start_str:
        try:
            start_dt = pd.to_datetime(conf_start_str).to_pydatetime(warn=False)
            end_dt_final = today
            if conf_end_str and conf_end_str.lower() != "today":
                end_dt_final = pd.to_datetime(conf_end_str).to_pydatetime(warn=False)

            if start_dt >= end_dt_final:
                 logger.warning(f"設定檔中開始日期 ({conf_start_str})晚於或等於結束日期 ({conf_end_str or 'today'})。將使用硬編碼預設。")
            else:
                logger.info(f"使用設定檔預設的日期範圍: {start_dt.strftime('%Y-%m-%d')} 至 {end_dt_final.strftime('%Y-%m-%d')}")
                return start_dt, end_dt_final
        except ValueError:
            logger.warning(f"設定檔中的日期格式無效 ('{conf_start_str}', '{conf_end_str}')，將使用硬編碼預設。")

    end_dt_fallback = today
    start_dt_fallback = today - timedelta(days=365)
    logger.info(f"使用硬編碼預設日期範圍 (過去一年): {start_dt_fallback.strftime('%Y-%m-%d')} 至 {end_dt_fallback.strftime('%Y-%m-%d')}")
    return start_dt_fallback, end_dt_fallback

def generate_report_filename_base(config: Dict[str, Any], date_for_filename: datetime) -> str:
    """產生報告檔案名稱基礎部分 (不含副檔名)"""
    prefix = config.get('report_output', {}).get('report_filename_prefix', 'dealer_stress_report')
    # 確保時間格式不含可能導致路徑問題的特殊字元，如冒號
    time_str = date_for_filename.strftime('%Y%m%d_%H%M%S')
    return f"{prefix}_{time_str}"

def run_app(event_params: Dict[str, Any] = None):
    """執行壓力報告生成 App 的主流程 (v3.0)。"""
    if event_params is None: event_params = {}

    logger.info(f"===== 開始執行 '21_generate_dealer_stress_report' App (v3.0) =====")
    logger.info(f"事件參數: {event_params}")

    # 1. 載入設定檔
    config = load_config() # 若失敗會拋出異常並終止
    api_keys = config.get('api_keys', {})

    # 標準化金鑰管理：優先從環境變數讀取 FRED API Key
    fred_api_key = os.environ.get('API_KEY_FRED')
    if fred_api_key:
        logger.info("成功從環境變數 'API_KEY_FRED' 讀取 FRED API 金鑰。")
    else:
        logger.info("環境變數 'API_KEY_FRED' 未設定，嘗試從設定檔讀取。")
        fred_api_key = api_keys.get('fred')

    gemini_api_key = api_keys.get('gemini') # 用於 reporter

    if not fred_api_key: # FRED Key 是基礎數據的關鍵
        logger.critical("CRITICAL: 環境變數和設定檔中均未找到 FRED API 金鑰。無法繼續。")
        return

    # 2. 決定日期範圍
    start_date_dt, end_date_dt = determine_date_range(event_params, config)
    start_date_str = start_date_dt.strftime('%Y-%m-%d')
    end_date_str = end_date_dt.strftime('%Y-%m-%d')

    # 準備輸出目錄
    report_output_cfg = config.get('report_output', {})
    base_output_dir = report_output_cfg.get('output_directory', DEFAULT_OUTPUT_DIR_BASE)
    current_run_time = datetime.now()
    report_subdir_name = current_run_time.strftime(report_output_cfg.get('sub_directory_format', '%Y-%m-%d_%H%M%S_run'))
    output_dir_path = os.path.join(base_output_dir, report_subdir_name)

    try:
        os.makedirs(output_dir_path, exist_ok=True)
        logger.info(f"報告將輸出至目錄: {output_dir_path}")
    except OSError as e:
        logger.critical(f"CRITICAL: 無法建立報告輸出目錄 '{output_dir_path}': {e}。應用程式無法執行。")
        return

    # --- 3. 獲取數據 ---
    logger.info("##### 階段：數據獲取 #####")
    data_fetching_cfg = config.get('data_fetching', {})

    # FRED 基礎數據 (SOFR, DGS10, DGS2, RRP, WRESBAL 等)
    fred_base_df = fetch_fred_data(fred_api_key, start_date_str, end_date_str, data_fetching_cfg.get('fred_series_map', {}))

    # Yahoo Finance 其他數據 (例如 TLT 價格，如果計算IV需要)
    # 注意：MOVE 和 VIX 現在由專用函式處理
    yahoo_other_df = fetch_yahoo_data(start_date_str, end_date_str, data_fetching_cfg.get('yahoo_tickers_map', {}))

    # MOVE 指數 (含備援)
    move_series = get_move_index(start_date_str, end_date_str, config) # 傳整個 config

    # VIX 指數 (含備援)
    vix_series = get_vix_index(start_date_str, end_date_str, config) # 傳整個 config

    # NY Fed 持有量數據
    nyfed_series = fetch_nyfed_data(data_fetching_cfg.get('ny_fed_positions_urls', []), data_fetching_cfg.get('sbp_cols_config', {}))

    # --- 合併所有數據源 ---
    logger.info("--- 正在合併所有獲取的數據源 ---")
    # 以業務日為基礎索引
    base_idx = pd.date_range(start=start_date_dt, end=end_date_dt, freq='B')
    merged_df = pd.DataFrame(index=base_idx)

    if fred_base_df is not None and not fred_base_df.empty:
        merged_df = merged_df.join(fred_base_df, how='left')
    if yahoo_other_df is not None and not yahoo_other_df.empty:
        merged_df = merged_df.join(yahoo_other_df, how='left')

    if move_series is not None and not move_series.empty:
        # MOVE series 可能是日頻，需對齊業務日
        merged_df['MOVE_Index'] = move_series.reindex(base_idx, method='ffill')
    else:
        merged_df['MOVE_Index'] = np.nan
        logger.warning("MOVE 指數數據最終未能獲取，將以 NaN 填充。")

    if vix_series is not None and not vix_series.empty:
        # VIX series 可能是日頻，需對齊業務日
        merged_df['VIX_Index'] = vix_series.reindex(base_idx, method='ffill')
    else:
        merged_df['VIX_Index'] = np.nan
        logger.warning("VIX 指數數據最終未能獲取，將以 NaN 填充。")

    if nyfed_series is not None and not nyfed_series.empty:
        nyfed_df_temp = nyfed_series.to_frame().reindex(base_idx, method='ffill')
        merged_df = merged_df.join(nyfed_df_temp, how='left') # 已命名為 Total_Gross_Positions_Millions

    logger.info(f"數據合併完成。合併後 DataFrame 維度: {merged_df.shape}")
    if merged_df.isna().all(axis=None): # 檢查是否所有值都是 NaN
        logger.critical("CRITICAL: 所有數據源獲取失敗或合併後數據全為 NaN，無法繼續生成報告。")
        return

    # --- 4. 計算指標 ---
    logger.info("##### 階段：指標計算 #####")
    calc_params_cfg = config.get('calculation_params', {})
    # 注意：merged_df 中的 'Volatility_Index' 和 'VIX' 列名可能需要與 calculator 期望的統一
    # data_fetcher 中已將 MOVE 和 VIX 獲取結果命名為 MOVE_Index 和 VIX_Index
    # calculator 中可能需要查找這些名字，或者在 config 中定義映射
    # 為簡化，假設 calculator.py 中的 calculate_stress_index 會查找 'MOVE_Index' 和 'VIX_Index'

    df_with_indicators = calculate_derived_indicators(merged_df.copy())
    df_with_stress = calculate_stress_index(df_with_indicators.copy(), calc_params_cfg)

    final_df = df_with_stress.copy()
    viz_params_cfg = config.get('visualization_params', {}) # 用於 MACD 參數
    if viz_params_cfg.get('enable_macd_momentum_plot', False): # 檢查是否啟用MACD
        final_df = calculate_macd_momentum(final_df, viz_params_cfg) # 傳遞 viz_params 以獲取 macd_params

    logger.info(f"指標計算完成。最終 DataFrame 維度: {final_df.shape}")

    # --- 5. 生成視覺化圖表 (Plotly Figure 物件) ---
    logger.info("##### 階段：圖表生成 (Plotly) #####")
    # 傳遞整個 config 給 visualizer，讓它自行提取所需參數
    plotly_dashboard_fig = create_stress_dashboard_plotly(final_df, config, (start_date_dt, end_date_dt))

    plotly_figs_for_report = {}
    if plotly_dashboard_fig:
        logger.info("Plotly 組合式儀表板 Figure 物件已生成。")
        plotly_figs_for_report['dashboard_main'] = plotly_dashboard_fig
        # 如果有其他獨立的 Plotly 圖表，也可以在這裡生成並加入字典
    else:
        logger.warning("Plotly 組合式儀表板未能生成。報告中可能缺少圖表。")

    # --- 6. 生成報告內容與檔案 ---
    logger.info("##### 階段：報告生成 #####")
    report_filename_base = generate_report_filename_base(config, current_run_time)

    # 6.1 生成文字分析內容
    text_analysis_content = generate_text_analysis(final_df, config) # 傳遞 config

    # 6.2 (可選) Gemini API 潤飾
    gemini_analysis_html_output = None
    gemini_error_output = None
    if report_output_cfg.get('include_ai_analysis', False) and gemini_api_key:
        ai_text = call_gemini_api(
            text_analysis_content["gemini_input_data"],
            gemini_api_key,
            config.get('gemini_config', {})
        )
        if ai_text and "失敗" not in ai_text and "未能" not in ai_text and "套件未安裝" not in ai_text and "金鑰未設定" not in ai_text:
            gemini_analysis_html_output = f"<p>{ai_text}</p>" # Gemini 回應已包含 <br>
        else:
            gemini_error_output = ai_text or "AI 分析未知錯誤。"
            logger.warning(f"Gemini API 潤飾失敗或返回錯誤訊息: {gemini_error_output}")
    else:
        logger.info("跳過 AI 文字潤飾 (未啟用或缺少 Gemini API 金鑰)。")
        if report_output_cfg.get('include_ai_analysis', False) and not gemini_api_key:
             gemini_error_output = "Gemini API 金鑰未在設定檔中提供。"

    # 6.3 準備報告上下文
    report_context = {
        "report_title": config.get('report_title', "一級交易商壓力指數分析報告"),
        "generation_time": current_run_time.strftime('%Y-%m-%d %H:%M:%S %Z'),
        "data_range_start": start_date_str,
        "data_range_end": end_date_str,
        "latest_data_date": text_analysis_content["latest_data_date"],
        "latest_stress_index_value": text_analysis_content["latest_stress_index_value"],
        "historical_comparison_html": text_analysis_content["historical_comparison_html"],
        "scenario_analysis_html": text_analysis_content["scenario_analysis_html"],
        "gemini_analysis_html": gemini_analysis_html_output,
        "gemini_error": gemini_error_output,
        # 圖表標題等可從 config 獲取傳入 reporter.py 或 compile_html_report
    }

    # 6.4 編譯並儲存 HTML 報告
    html_report_file_path = compile_html_report(
        report_context,
        plotly_figs_for_report, # 傳遞 Plotly Figure 物件字典
        config, # reporter 可能需要 config 中的其他設定
        output_dir_path,
        report_filename_base
    )

    if html_report_file_path:
        logger.info(f"最終 HTML 報告已成功生成於: {html_report_file_path}")
    else:
        logger.error("錯誤：最終 HTML 報告生成失敗。")

    logger.info(f"===== '21_generate_dealer_stress_report' App (v3.0) 執行完畢 =====")
    logger.info(f"所有輸出檔案位於: {output_dir_path}")


if __name__ == "__main__":
    # 模擬事件參數用於直接測試 run_app
    mock_event_params = {
        # "start_date": "2023-01-01", # 可選，用於覆寫日期
        # "end_date": "2023-06-30",   # 可選
        "output_format": "full_html_report" # 示例參數
    }
    logger.info("以 __main__ 方式執行 run_app.py (v3.0，用於測試)...")
    run_app(event_params=mock_event_params)
