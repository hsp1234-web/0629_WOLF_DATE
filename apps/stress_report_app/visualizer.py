# -*- coding: utf-8 -*-
"""
視覺化模組 (visualizer.py) - v3.0

功能：
- 使用 Plotly 繪製互動式、支援中文的組合式儀表板，包含：
    - 壓力指數儀表板 (Gauge)
    - 關鍵指標迷你趨勢圖 (Sparklines)
    - 主要時間序列子圖
- 支援將圖表匯出為 HTML 檔案、可嵌入的 HTML div 字串或 Base64 編碼的 HTML。
"""

import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import io
import base64
import logging
from typing import Dict, Any, Optional, Tuple # Tuple 已在此，Optional 也在此
from datetime import datetime # 修正 NameError: name 'datetime' is not defined

# 導入 schemas 中的類型給類型提示，使用 TYPE_CHECKING 防止循環導入
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .schemas import CalculatedData, VisualizationData, AppConfig

logger = logging.getLogger(__name__)

def create_stress_dashboard_plotly(
    final_df: pd.DataFrame,
    config: Dict[str, Any],
    date_range: Tuple[datetime, datetime] # (start_datetime_obj, end_datetime_obj)
) -> Optional[go.Figure]:
    """
    使用 Plotly 創建一個組合式壓力儀表板。
    包含壓力指數儀表板 (Gauge)、迷你趨勢圖 (Sparklines) 和主要時間序列子圖。

    Args:
        final_df (pd.DataFrame): 包含所有計算指標的最終 DataFrame。
        config (dict): 包含視覺化參數的字典，例如：
                       'plots_config', 'chart_params', 'gauge_thresholds', 'trend_thresholds'。
        date_range (tuple): 圖表繪製的開始和結束日期 (datetime 物件)。

    Returns:
        go.Figure or None: Plotly Figure 物件，如果無法生成則返回 None。
    """
    func_name = 'create_stress_dashboard_plotly'
    logger.info(f"[{func_name}] 開始生成 Plotly 組合式儀表板...")

    if final_df.empty:
        logger.warning(f"[{func_name}] final_df 為空，無法生成圖表。")
        return None

    viz_params = config.get('visualization_params', {})
    plots_config = viz_params.get('plots_config', {}) # 子圖啟用設定
    chart_params = viz_params.get('chart_params', {}) # 通用圖表參數
    gauge_thresholds = viz_params.get('gauge_thresholds', {'high': 80, 'medium': 60, 'low': 30})
    # trend_thresholds = viz_params.get('trend_thresholds', {}) # 用於主要趨勢圖的顏色

    # --- 1. 創建子圖佈局 ---
    # 頂部：儀表板 + 迷你趨勢圖
    # 底部：主要時間序列圖 (多個)
    # 根據要顯示的圖表數量動態調整佈局可能比較複雜，我們先用一個固定的網格，然後根據數據填充

    # 我們可以這樣設計：
    # Row 1: Gauge (佔比如 0.3 高度), Sparkline1, Sparkline2 (各佔 0.15 高度，與 Gauge 並排或下方)
    # Row 2: Main Plot 1, Main Plot 2 (各佔 0.35 高度)
    # Row 3: Main Plot 3, Main Plot 4
    # ...
    # 這裡簡化：一個主儀表板，下方是可選的多個時間序列子圖

    # 計算需要多少行給主要時間序列圖
    main_plot_keys = ['SOFR', 'Spread', 'MOVE', 'VIX', 'DealerPos', 'Reserves', 'StressIndex', 'Ratio', 'MACD', 'ETF']
    active_main_plots = [key for key in main_plot_keys if plots_config.get(key, False)]
    num_main_plots = len(active_main_plots)

    # 每行放2個主圖
    main_plot_rows = (num_main_plots + 1) // 2
    total_rows = 1 + main_plot_rows # 1行給儀表板和迷你圖，其餘給主圖

    # 創建帶有規格的子圖，第一行特殊處理
    cols_layout = 2 # 預設兩欄
    actual_subplot_titles = []

    if main_plot_rows == 0: # 只有儀表板
        specs = [[{"type": "indicator"}]] # 儀表板占滿
        cols_layout = 1
        actual_subplot_titles.append("壓力儀表板")
        row_heights_layout = [1.0]
        total_rows_layout = 1
    else: # 有儀表板和主圖
        total_rows_layout = 1 + main_plot_rows
        # 儀表板占第一行，橫跨兩列
        specs = [[{"type": "indicator", "colspan": 2, "rowspan":1}, None]]
        actual_subplot_titles.append("壓力儀表板") # 儀表板的標題

        main_plot_titles_map = chart_params.get("main_plot_titles", {})

        current_plot_title_idx = 0
        for i in range(main_plot_rows):
            row_spec = []
            # 左邊的圖
            if current_plot_title_idx < num_main_plots:
                row_spec.append({"type": "xy"})
                title_key = active_main_plots[current_plot_title_idx]
                actual_subplot_titles.append(main_plot_titles_map.get(title_key, title_key))
                current_plot_title_idx += 1
            else:
                row_spec.append(None) # 如果沒有足夠的圖填滿左邊

            # 右邊的圖 (或合併的圖)
            if current_plot_title_idx < num_main_plots:
                 # 如果是最後一行，且這是該行第一個圖，且總圖數是奇數，則此圖colspan=2
                if i == main_plot_rows - 1 and (num_main_plots - (current_plot_title_idx -1)) % 2 == 1 and len(row_spec) == 1:
                    row_spec = [{"type": "xy", "colspan": 2}, None]
                    # 標題已在上面添加，這裡不需要再添加標題給None的部分
                else:
                    row_spec.append({"type": "xy"})
                    title_key = active_main_plots[current_plot_title_idx]
                    actual_subplot_titles.append(main_plot_titles_map.get(title_key, title_key))
                current_plot_title_idx += 1
            else:
                # 如果左邊有圖，但右邊沒圖了 (只有在偶數圖時，且左邊是最後一個圖的情況)
                # 實際上這種情況不應該發生，因為上面colspan會處理奇數個圖的情況
                if row_spec[0] is not None: # 確保左邊有圖
                     row_spec.append(None)
                # else: # 如果左邊也是None (例如總圖數為0，但main_plot_rows > 0，理論上不會)
                     # specs.append([None, None]) #不需要，外層循環會處理

            specs.append(row_spec)

        row_heights_layout = [0.3] + [(0.7 / main_plot_rows)] * main_plot_rows


    try:
        fig = make_subplots(
            rows=total_rows_layout,
            cols=cols_layout,
            specs=specs,
            vertical_spacing=0.15 if main_plot_rows > 0 else 0.02,
            horizontal_spacing=0.08,
            row_heights=row_heights_layout,
            subplot_titles=actual_subplot_titles # 使用精確計算的標題列表
        )
    except Exception as e:
        logger.error(f"[{func_name}] 創建 Plotly subplots 時出錯: {e}", exc_info=True)
        return None

    # --- 2. 繪製壓力儀表板 (Gauge) ---
    latest_stress_value = np.nan
    if 'Dealer_Stress_Index' in final_df and final_df['Dealer_Stress_Index'].notna().any():
        latest_stress_value = final_df['Dealer_Stress_Index'].dropna().iloc[-1]

    if pd.notna(latest_stress_value):
        fig.add_trace(go.Indicator(
            mode="gauge+number",
            value=latest_stress_value,
            title={'text': "當前壓力指數", 'font': {'size': 18}},
            gauge={
                'axis': {'range': [0, 100], 'tickwidth': 1, 'tickcolor': "darkblue"},
                'bar': {'color': "darkblue"},
                'bgcolor': "white",
                'borderwidth': 2,
                'bordercolor': "gray",
                'steps': [
                    {'range': [0, gauge_thresholds.get('low',30)], 'color': "green"},
                    {'range': [gauge_thresholds.get('low',30), gauge_thresholds.get('medium',60)], 'color': "yellow"},
                    {'range': [gauge_thresholds.get('medium',60), gauge_thresholds.get('high',80)], 'color': "orange"},
                    {'range': [gauge_thresholds.get('high',80), 100], 'color': "red"}],
                'threshold': {
                    'line': {'color': "black", 'width': 4},
                    'thickness': 0.75,
                    'value': latest_stress_value
                }
            }
        ), row=1, col=1) # 假設儀表板在 (1,1)
    else:
        logger.warning(f"[{func_name}] 無法獲取最新壓力指數值，儀表板將不顯示。")
        # 可以選擇在 (1,1) 處顯示一條訊息

    # --- 3. 繪製主要時間序列子圖 ---
    # 這裡需要將 'plot_results_summary' 的邏輯遷移過來，並使用 Plotly
    # 這部分會比較長，因為每個子圖都需要單獨添加 trace

    # 簡易的時間序列圖繪製邏輯 (需要根據 plot_results_summary 的詳細邏輯擴展)
    # plot_map = { 'SOFR': {'col':'SOFR', 'name':'SOFR (%)'}, ... }
    current_main_plot_idx = 0
    for plot_key in main_plot_keys:
        if plots_config.get(plot_key, False) and plot_key in final_df.columns and final_df[plot_key].notna().any():
            if current_main_plot_idx >= num_main_plots: break # 避免超出預設的子圖數量

            row = 2 + current_main_plot_idx // 2
            col = 1 + current_main_plot_idx % 2

            # 特殊處理 colspan for 奇數圖
            actual_col = col
            if num_main_plots % 2 == 1 and current_main_plot_idx == num_main_plots -1 : #最後一個圖且是奇數
                 actual_col = 1 # 它會佔據第1和第2列

            series_data = final_df[plot_key]
            plot_name = chart_params.get(f'{plot_key}_name', plot_key) # 從設定檔獲取中文名

            fig.add_trace(go.Scatter(
                x=series_data.index,
                y=series_data,
                mode='lines',
                name=plot_name
            ), row=row, col=actual_col)

            # 更新子圖標題 (如果 subplot_titles 數量不足，Plotly 會報錯)
            # make_subplots 已經創建了 subplot_titles，這裡可以更新
            # fig.layout.annotations[1 + current_main_plot_idx].text = plot_name # 標題從第2個開始 (儀表板是第1個)

            current_main_plot_idx += 1

    # --- 4. 更新整體佈局 ---
    fig.update_layout(
        title_text='一級交易商壓力分析總覽儀表板',
        title_x=0.5,
        height=400 + (main_plot_rows * 250), # 動態調整高度
        showlegend=True, # 可以為每個子圖設定 showlegend=False，然後創建一個統一的圖例
        template="plotly_white", # Plotly 的內建主題
        font=dict(family="Arial, Noto Sans CJK TC, Microsoft JhengHei, sans-serif", size=12, color="black") # 設定字體
    )
    # 可以在此處遍歷 fig.layout.annotations 更新子圖標題的字體等

    logger.info(f"[{func_name}] Plotly 組合式儀表板生成完畢。")
    return fig


def export_plotly_fig(
    fig: go.Figure,
    output_format: str, # 'html_file', 'base64', 'div_embed'
    output_path: Optional[str] = None # 主要用於 'html_file'
) -> Optional[str]:
    """
    將 Plotly Figure 物件匯出為指定格式。
    """
    func_name = 'export_plotly_fig'
    logger.info(f"[{func_name}] 準備將 Plotly 圖表匯出為 {output_format}...")

    if not isinstance(fig, go.Figure):
        logger.error(f"[{func_name}] 輸入的不是有效的 Plotly Figure 物件。")
        return None

    try:
        if output_format == 'html_file':
            if not output_path:
                logger.error(f"[{func_name}] 匯出為 'html_file' 需要提供 output_path。")
                return None
            fig.write_html(output_path, include_plotlyjs='cdn')
            logger.info(f"[{func_name}] Plotly 圖表已儲存為 HTML 檔案: {output_path}")
            return output_path # 返回路徑

        elif output_format == 'base64':
            html_str = fig.to_html(full_html=False, include_plotlyjs='cdn')
            base64_str = base64.b64encode(html_str.encode('utf-8')).decode('utf-8')
            logger.info(f"[{func_name}] Plotly 圖表已轉換為 Base64 編碼的 HTML。")
            return f"data:text/html;base64,{base64_str}"

        elif output_format == 'div_embed':
            # 產生不含 Plotly.js 的 HTML div，適用於已載入 Plotly.js 的頁面
            div_str = fig.to_html(full_html=False, include_plotlyjs=False)
            logger.info(f"[{func_name}] Plotly 圖表已轉換為可嵌入的 HTML div。")
            return div_str

        else:
            logger.warning(f"[{func_name}] 不支援的輸出格式: {output_format}。")
            return None

    except Exception as e:
        logger.error(f"[{func_name}] 匯出 Plotly 圖表時發生錯誤: {e}", exc_info=True)
        return None


if __name__ == '__main__':
    # 簡單的測試代碼
    logging.basicConfig(level=logging.INFO)
    logger.info("visualizer.py 模組被直接執行 (用於測試)。")

    # 創建一個假的 DataFrame
    sample_dates = pd.to_datetime(['2023-01-01', '2023-01-02', '2023-01-03', '2023-01-04', '2023-01-05'])
    sample_data = {
        'Dealer_Stress_Index': [30, 35, 65, 85, 50],
        'SOFR': [1.0, 1.1, 1.05, 1.12, 1.15],
        'Spread': [-0.1, -0.05, 0.05, 0.1, 0.02],
        # 可以添加更多列以測試多子圖
    }
    sample_final_df = pd.DataFrame(sample_data, index=sample_dates)

    mock_config_viz = {
        "visualization_params": {
            "plots_config": { # 控制顯示哪些子圖
                "SOFR": True,
                "Spread": True,
                "StressIndex": True, # 確保儀表板和主圖都有壓力指數數據
                # 其他圖表預設為 False 或不包含在此處
            },
            "chart_params": {
                "SOFR_name": "SOFR 利率 (%)", # 假設的中文名
                "Spread_name": "利差 (10Y-2Y BPS)",
                "StressIndex_name": "壓力指數"
            },
            "gauge_thresholds": {'high': 80, 'medium': 60, 'low': 30},
        }
    }

    fig_dashboard = create_stress_dashboard_plotly(
        sample_final_df,
        mock_config_viz,
        date_range=(sample_dates.min(), sample_dates.max())
    )

    if fig_dashboard:
        logger.info("測試儀表板 Figure 已生成。")

        # 測試匯出
        export_plotly_fig(fig_dashboard, 'html_file', 'test_dashboard.html')

        base64_output = export_plotly_fig(fig_dashboard, 'base64')
        if base64_output:
            logger.info(f"Base64 輸出長度: {len(base64_output)}")
            # print(base64_output[:200] + "...") # 打印部分預覽

        div_output = export_plotly_fig(fig_dashboard, 'div_embed')
        if div_output:
            logger.info(f"Div embed 輸出長度: {len(div_output)}")
            # print(div_output) # 打印 div
    else:
        logger.error("測試儀表板 Figure 未能生成。")

# --- 主函式包裝器 (SOP v3.0 要求) ---
def create_all_visuals(
    data: 'CalculatedData', # 使用引號避免循環導入，實際應為 schemas.CalculatedData
    logger_instance: Optional[logging.Logger] = None
) -> 'VisualizationData': # 使用引號避免循環導入，實際應為 schemas.VisualizationData
    """
    視覺化階段的主函式。
    依照 SOP v3.0，此函式接收 CalculatedData，生成 Plotly 圖表，
    並返回一個符合 VisualizationData 合約的 Pydantic 模型實例。

    Args:
        data (CalculatedData): 包含 final_df 和 AppConfig 的 Pydantic 模型實例。
        logger_instance (Optional[logging.Logger]): 日誌記錄器實例。

    Returns:
        VisualizationData: 包含 plotly_fig, final_df 和 AppConfig 的 Pydantic 模型實例。
    """
    current_logger = logger_instance if logger_instance else logging.getLogger(__name__)
    current_logger.info("進入 create_all_visuals 主函式...")

    final_df_input = data.final_df
    app_config = data.config # Pydantic AppConfig 實例

    # 從 AppConfig 提取視覺化參數 (Pydantic 模型)
    visualization_specific_config_model = app_config.visualization_params
    # 將 Pydantic 模型轉換為字典以兼容現有函式簽名
    visualization_specific_config_dict = visualization_specific_config_model.model_dump()

    # 獲取繪圖所需的日期範圍，這裡假設 final_df 的索引是有效的日期時間索引
    # 如果 app.py 中有解析 start_date, end_date，理論上應該從 app_config 中獲取
    # 或從 final_df 的索引推斷。為簡化，先從 final_df 推斷。
    # 實際應用中，日期範圍應由 app.py 傳遞或包含在 AppConfig 中。
    # 根據 SOP，date_range 是傳給 create_stress_dashboard_plotly 的，
    # 但 create_all_visuals 的輸入是 CalculatedData，不直接包含原始 start/end date。
    # 我們需要確保 AppConfig 包含這些信息，或者 final_df 的時間範圍是正確的。
    # 假設 AppConfig 包含 start_date 和 end_date 字符串 (需在 schemas.py 中添加)
    # 或從 final_df_input.index 推斷

    # 為了符合 create_stress_dashboard_plotly 的簽名，我們需要 start_date_dt, end_date_dt
    # 這些應該在 app_config 中，或者從 final_df_input 的索引中獲取
    # 暫時從 final_df_input 的索引獲取，但這不是最穩健的做法
    if not final_df_input.empty and isinstance(final_df_input.index, pd.DatetimeIndex):
        plot_start_date = final_df_input.index.min()
        plot_end_date = final_df_input.index.max()
        current_logger.info(f"從 final_df 推斷的繪圖日期範圍: {plot_start_date.strftime('%Y-%m-%d')} 至 {plot_end_date.strftime('%Y-%m-%d')}")
    else:
        # 如果無法推斷，可能需要一個預設或報錯
        current_logger.warning("無法從 final_df 推斷繪圖日期範圍，圖表可能不準確。")
        # 使用一個預設的較大範圍或依賴 create_stress_dashboard_plotly 內部的處理
        # 這裡假設，如果 final_df 為空，create_stress_dashboard_plotly 會返回 None
        plot_start_date = datetime.now() - pd.Timedelta(days=365) # 預設過去一年
        plot_end_date = datetime.now()

    # --- 呼叫核心繪圖函式 ---
    plotly_figure_output: Optional[go.Figure] = None
    try:
        plotly_figure_output = create_stress_dashboard_plotly(
            final_df_input,
            visualization_specific_config_dict, # 傳遞字典
            date_range=(plot_start_date, plot_end_date)
        )
        if plotly_figure_output:
            current_logger.info("核心繪圖函式 create_stress_dashboard_plotly 執行成功，已生成 Plotly Figure。")
        else:
            current_logger.warning("核心繪圖函式 create_stress_dashboard_plotly 返回 None，未能生成圖表。")
    except Exception as e:
        current_logger.error(f"執行 create_stress_dashboard_plotly 時發生錯誤: {e}", exc_info=True)
        plotly_figure_output = None # 確保出錯時返回 None

    # 導入 VisualizationData 模型
    from .schemas import VisualizationData

    # 封裝到 VisualizationData Pydantic 模型
    visualization_data_output = VisualizationData(
        plotly_fig=plotly_figure_output,
        final_df=final_df_input, # 將數據繼續往下傳遞
        config=app_config      # 繼續傳遞完整的 AppConfig 實例
    )

    current_logger.info("create_all_visuals 主函式執行完畢。")
    return visualization_data_output
