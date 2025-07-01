# -*- coding: utf-8 -*-
"""
報告生成模組 (reporter.py) - v3.0

功能：
- 生成基於規則的文字分析。
- (可選) 呼叫 Gemini API 對分析內容進行潤飾。
- 將文字分析和 Plotly 圖表編譯成最終的 HTML 報告。
- 提供 Markdown 報告作為備選方案。
"""

import pandas as pd
import numpy as np
import logging
import os
from datetime import datetime
from typing import Dict, Any, Optional, List # Optional, List 已在此
import plotly.graph_objects as go

# 導入 schemas 中的類型給類型提示，使用 TYPE_CHECKING 防止循環導入
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .schemas import VisualizationData, ReportData, AppConfig, CalculatedData

# 嘗試導入 Gemini API 相關套件
try:
    import google.generativeai as genai
    GOOGLE_GENERATIVEAI_AVAILABLE = True
except ImportError:
    GOOGLE_GENERATIVEAI_AVAILABLE = False
    genai = None

# 導入 Jinja2
from jinja2 import Environment, select_autoescape, Template

logger = logging.getLogger(__name__)

DEFAULT_REPORT_TEMPLATE_HTML = """
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ report_title }}</title>
    {% raw %}
    <style>
        body { font-family: 'Arial', 'Noto Sans CJK TC', sans-serif; margin: 20px; line-height: 1.6; }
        h1, h2, h3 { color: #333; }
        .container { max-width: 1000px; margin: auto; background: #f9f9f9; padding: 20px; border-radius: 8px; box-shadow: 0 0 10px rgba(0,0,0,0.1); }
        .chart-container { margin-bottom: 30px; }
        .section { margin-bottom: 20px; }
        .gemini-analysis { background-color: #eef7ff; border-left: 5px solid #2196F3; padding: 15px; margin-top: 15px; }
        .error-message { color: red; font-style: italic; }
        table { border-collapse: collapse; width: 100%; margin-bottom: 20px; }
        th, td { text-align: left; padding: 8px; border-bottom: 1px solid #ddd; }
        th { background-color: #f2f2f2; }
    </style>
    {% endraw %}
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
</head>
<body>
    <div class="container">
        <h1>{{ report_title }}</h1>
        <p><strong>報告生成時間：</strong> {{ generation_time }}</p>
        <p><strong>分析數據範圍：</strong> {{ data_range_start }} 至 {{ data_range_end }}</p>

        <div class="section" id="text-analysis">
            <h2>一、市場壓力概覽與歷史對比</h2>
            <p><strong>當前壓力指數 ({{ latest_data_date }})：</strong> {{ latest_stress_index_value }}</p>
            {{ historical_comparison_html | safe }}
        </div>

        {% if gemini_analysis_html %}
        <div class="section gemini-analysis" id="gemini-analysis">
            <h2>二、AI 輔助市場綜合解讀與展望</h2>
            {{ gemini_analysis_html | safe }}
        </div>
        {% elif gemini_error %}
        <div class="section gemini-analysis" id="gemini-analysis-error">
            <h2>二、AI 輔助市場綜合解讀與展望</h2>
            <p class="error-message">未能成功獲取 AI 輔助分析：{{ gemini_error }}</p>
        </div>
        {% endif %}

        <div class="section" id="charts">
            <h2>三、詳細圖表分析</h2>
            {% for chart_name, chart_html in charts_html.items() %}
            <div class="chart-container" id="chart-{{ loop.index }}">
                <h3>{{ chart_titles.get(chart_name, chart_name) }}</h3>
                {{ chart_html | safe }}
            </div>
            {% endfor %}
        </div>

        <div class="section" id="scenario-analysis">
            <h2>四、市場壓力情境分析與一般性考量 (僅供參考)</h2>
            {{ scenario_analysis_html | safe }}
             <p><small><strong>免責聲明：</strong>以上內容基於壓力指數的假設性變動，提供一般性的市場觀察和原則性考量，不構成任何形式的投資建議。市場實際表現受多重複雜因素影響，任何投資決策請務必諮詢合格的專業財務顧問，並進行獨立判斷。</small></p>
        </div>
    </div>
</body>
</html>
"""

# 移除了 render_template_simple 函數，將使用 Jinja2


def generate_text_analysis(
    final_df: pd.DataFrame,
    config: Dict[str, Any]
) -> Dict[str, Any]:
    """
    生成基於規則的文字分析和準備給 Gemini 的數據。
    """
    logger.info("開始生成文字分析...")
    analysis_output = {
        "latest_data_date": "N/A",
        "latest_stress_index_value": "N/A",
        "historical_comparison_html": "<p>無法進行歷史比較。</p>",
        "scenario_analysis_html": "<p>無法生成情境分析。</p>",
        "gemini_input_data": {} # 儲存傳給 Gemini 的結構化數據
    }

    if final_df.empty or 'Dealer_Stress_Index' not in final_df.columns:
        logger.warning("final_df 為空或缺少壓力指數，無法生成詳細文字分析。")
        return analysis_output

    stress_series = final_df['Dealer_Stress_Index'].dropna()
    if stress_series.empty:
        logger.warning("壓力指數序列為空，無法生成詳細文字分析。")
        return analysis_output

    latest_stress_value = stress_series.iloc[-1]
    latest_stress_date = stress_series.index[-1]
    analysis_output["latest_data_date"] = latest_stress_date.strftime('%Y年%m月%d日')
    analysis_output["latest_stress_index_value"] = f"{latest_stress_value:.2f}"

    # 歷史對比
    # ... (此處省略 `一級交易pro.py` Cell 11 中的歷史對比邏輯，可後續加入) ...
    # 簡單示例：
    hist_comp_lines = ["<ul>"]
    if latest_stress_value > 80:
        hist_comp_lines.append("<li><span style='color:red;'>警告：</span>目前壓力水平極高 (超過80)。</li>")
    elif latest_stress_value > 60:
        hist_comp_lines.append("<li><span style='color:orange;'>注意：</span>目前壓力水平較高 (60-80)。</li>")
    else:
        hist_comp_lines.append("<li>目前壓力水平相對溫和 (低於60)。</li>")
    hist_comp_lines.append("</ul>")
    analysis_output["historical_comparison_html"] = "\n".join(hist_comp_lines)

    # 情境分析 (靜態文字，可從設定檔或模板讀取)
    # ... (此處省略 `一級交易pro.py` Cell 11 中的情境分析文字，可後續加入) ...
    analysis_output["scenario_analysis_html"] = """
        <p><strong>若壓力指數持續上升：</strong>通常伴隨市場波動加劇、避險情緒升溫...</p>
        <p><strong>若壓力指數持續下降：</strong>通常表示市場風險偏好改善、流動性充裕...</p>
    """

    # 準備 Gemini 輸入數據
    gemini_data = {
        "latest_stress_index": latest_stress_value,
        "latest_stress_date": latest_stress_date.strftime('%Y-%m-%d'),
        "stress_index_wow_change": None, # 週變動
        "move_index_value": None,
        "move_index_source": "N/A",
    }
    if len(stress_series) >= 6: # 至少需要6個數據點 (5個交易日+前一週的同一天) 來計算周變動
        gemini_data["stress_index_wow_change"] = latest_stress_value - stress_series.iloc[-6]

    if 'MOVE_Index' in final_df and final_df['MOVE_Index'].notna().any():
        gemini_data["move_index_value"] = final_df['MOVE_Index'].dropna().iloc[-1]
        # MOVE 來源需要從 data_fetcher 的日誌或狀態中獲取，這裡暫時簡化
        gemini_data["move_index_source"] = config.get('data_fetching',{}).get('fred_move_ticker', 'MOVEIX') # 假設優先FRED

    analysis_output["gemini_input_data"] = gemini_data
    logger.info("文字分析和 Gemini 輸入數據準備完成。")
    return analysis_output

def call_gemini_api(
    prompt_data: Dict[str, Any],
    api_key: str,
    gemini_config_params: Dict[str, Any]
) -> Optional[str]:
    """
    呼叫 Gemini API 進行文字潤飾。
    """
    if not GOOGLE_GENERATIVEAI_AVAILABLE:
        logger.warning("google.generativeai 套件未安裝，無法呼叫 Gemini API。")
        return "Gemini API 套件未安裝。" # 返回錯誤訊息而非 None
    if not api_key:
        logger.warning("Gemini API 金鑰未提供，跳過 AI 潤飾。")
        return "Gemini API 金鑰未設定。"

    logger.info("嘗試呼叫 Gemini API 進行文字潤飾...")
    genai.configure(api_key=api_key)

    model_name = gemini_config_params.get("model_name", "gemini-pro")
    generation_config = genai.types.GenerationConfig(
        candidate_count=1,
        temperature=gemini_config_params.get("temperature", 0.7),
        # top_p=gemini_config_params.get("top_p", 1.0), # 可選
        # top_k=gemini_config_params.get("top_k", 40), # 可選
    )
    safety_settings = gemini_config_params.get("safety_settings", None) # 例如 [{'category': HarmCategory..., 'threshold': HarmBlockThreshold...}]

    # 構造 Prompt
    # (此處 Prompt 需要精心設計以獲得最佳結果)
    prompt_lines = [
        "您是一位專業的金融市場分析師。請根據以下提供的最新市場數據，撰寫一段約150字的「市場綜合解讀與展望」。",
        "數據摘要：",
        f"- 最新壓力指數 ({prompt_data.get('latest_stress_date', 'N/A')}): {prompt_data.get('latest_stress_index', 'N/A'):.2f}",
    ]
    if prompt_data.get('stress_index_wow_change') is not None:
        prompt_lines.append(f"- 壓力指數週變動: {prompt_data.get('stress_index_wow_change'):.2f}")
    if prompt_data.get('move_index_value') is not None:
        prompt_lines.append(f"- MOVE 指數 ({prompt_data.get('move_index_source', 'N/A')}): {prompt_data.get('move_index_value'):.2f}")
    # 可以加入更多關鍵指標
    prompt_lines.append("\n請著重分析當前市場的總體風險水平、主要驅動因素，並對短期後市提供展望。風格需專業、簡潔、客觀。")
    full_prompt = "\n".join(prompt_lines)
    logger.debug(f"Gemini API Prompt:\n{full_prompt}")

    try:
        model = genai.GenerativeModel(model_name)
        response = model.generate_content(
            full_prompt,
            generation_config=generation_config,
            safety_settings=safety_settings
            )

        if response.candidates and response.candidates[0].content.parts:
            ai_text = "".join(part.text for part in response.candidates[0].content.parts)
            logger.info("成功從 Gemini API 獲取潤飾文字。")
            # 將換行符轉換為 HTML <br>
            return ai_text.replace('\n', '<br>')
        else:
            logger.warning(f"Gemini API 未返回有效內容。回應: {response}")
            return "AI未能生成分析內容。"
    except Exception as e:
        logger.error(f"呼叫 Gemini API 時發生錯誤: {e}", exc_info=True)
        return f"AI分析生成失敗：{str(e)}"


def compile_html_report(
    report_context: Dict[str, Any],
    charts_plotly_figs: Dict[str, go.Figure], # key: chart_id, value: Plotly Figure
    config: Dict[str, Any],
    output_dir: str,
    base_filename: str
) -> Optional[str]:
    """
    編譯最終的 HTML 報告。
    """
    logger.info("開始編譯 HTML 報告...")
    html_report_path = os.path.join(output_dir, f"{base_filename}.html")

    # 將 Plotly 圖表轉換為嵌入式 HTML div
    charts_html_output = {}
    chart_titles_map = {} # 用於模板中顯示圖表標題

    viz_chart_params = config.get('visualization_params', {}).get('chart_params', {})

    for chart_id, fig_obj in charts_plotly_figs.items():
        if fig_obj is not None:
            # 從 fig.layout.title.text 獲取標題，如果沒有則用 chart_id
            chart_title = fig_obj.layout.title.text if fig_obj.layout and fig_obj.layout.title and fig_obj.layout.title.text else chart_id
            chart_titles_map[chart_id] = viz_chart_params.get(f"{chart_id}_name_html", chart_title) # 優先用設定檔中的中文名

            # 生成嵌入式 HTML，不包含 Plotly.js (假設模板中已引入 CDN)
            chart_div = fig_obj.to_html(full_html=False, include_plotlyjs=False)
            charts_html_output[chart_id] = chart_div
        else:
            charts_html_output[chart_id] = "<p class='error-message'>此圖表未能生成。</p>"
            chart_titles_map[chart_id] = viz_chart_params.get(f"{chart_id}_name_html", chart_id)

    final_context = {**report_context, "charts_html": charts_html_output, "chart_titles": chart_titles_map}

    # 使用內建的 HTML 模板
    html_template_str = DEFAULT_REPORT_TEMPLATE_HTML

    try:
        # 使用 Jinja2 進行模板渲染
        # 由於模板是字串，直接用 Template 類
        template = Template(html_template_str)
        rendered_html = template.render(final_context)

        with open(html_report_path, 'w', encoding='utf-8') as f:
            f.write(rendered_html)
        logger.info(f"HTML 報告已成功儲存至: {html_report_path}")
        return html_report_path
    except Exception as e:
        logger.error(f"編譯或儲存 HTML 報告時發生錯誤: {e}", exc_info=True)
        return None

if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    logger.info("reporter.py 模組被直接執行 (用於測試)。")

    # 創建模擬數據和配置
    mock_dates = pd.to_datetime([datetime.now() - pd.Timedelta(days=i) for i in range(5)][::-1]) # 修正 timedelta 導入
    mock_final_df = pd.DataFrame({
        'Dealer_Stress_Index': np.random.uniform(20, 80, 5),
        'MOVE_Index': np.random.uniform(50,150,5)
    }, index=mock_dates)

    mock_report_config = {
        "report_title": "測試壓力報告",
        "data_range_start": (datetime.now() - pd.Timedelta(days=5)).strftime('%Y-%m-%d'), # 修正 timedelta 導入
        "data_range_end": datetime.now().strftime('%Y-%m-%d'),
        "api_keys": {"gemini": "YOUR_GEMINI_API_KEY_IF_TESTING"}, # 測試時替換
        "gemini_config": {"model_name": "gemini-pro", "temperature": 0.5},
        "data_fetching": {"fred_move_ticker": "MOVEIX"}, # 模擬 MOVE 來源
        "visualization_params": {
            "chart_params": {
                "dashboard_main_name_html": "主儀表板與趨勢圖",
                "timeseries_detail_name_html": "詳細時間序列指標"
            }
        }
    }

    # 1. 測試文字分析生成
    text_analysis_res = generate_text_analysis(mock_final_df, mock_report_config)
    logger.info(f"文字分析結果: {text_analysis_res}")

    # 2. 測試 Gemini API 呼叫 (如果 GOOGLE_GENERATIVEAI_AVAILABLE 且金鑰有效)
    gemini_html_output = None
    gemini_error_msg = None
    # 修正 Gemini API Key 的檢查邏輯
    gemini_api_key_to_test = mock_report_config.get("api_keys", {}).get("gemini")
    if GOOGLE_GENERATIVEAI_AVAILABLE and gemini_api_key_to_test and gemini_api_key_to_test != "YOUR_GEMINI_API_KEY_IF_TESTING":
        gemini_text = call_gemini_api(
            text_analysis_res["gemini_input_data"],
            gemini_api_key_to_test, # 使用正確的金鑰變數
            mock_report_config.get("gemini_config", {}) # 確保傳入 gemini_config
        )
        if gemini_text and "失敗" not in gemini_text and "未能" not in gemini_text and "套件未安裝" not in gemini_text and "金鑰未設定" not in gemini_text: # 更全面的成功檢查
            gemini_html_output = f"<p>{gemini_text}</p>"
        else:
            gemini_error_msg = gemini_text or "未知 Gemini API 錯誤"
            logger.warning(f"Gemini API 測試呼叫未成功: {gemini_error_msg}")
    else:
        if not GOOGLE_GENERATIVEAI_AVAILABLE:
            gemini_error_msg = "Gemini API 套件未安裝。"
        elif not gemini_api_key_to_test or gemini_api_key_to_test == "YOUR_GEMINI_API_KEY_IF_TESTING":
            gemini_error_msg = "Gemini API 金鑰未設定或為占位符。"
        logger.warning(f"跳過 Gemini API 測試: {gemini_error_msg}")

    # 3. 準備 Plotly 圖表 (模擬 visualizer.py 的輸出)
    mock_plotly_figs = {}
    # 創建一個簡單的 Plotly figure 作為 dashboard 示例
    fig_dashboard_test = go.Figure(data=[go.Scatter(x=[1, 2, 3], y=[2, 1, 3])])
    fig_dashboard_test.update_layout(title_text="模擬儀表板圖")
    mock_plotly_figs['dashboard_main'] = fig_dashboard_test

    # 創建另一個簡單的 Plotly figure 作為 timeseries 示例
    fig_timeseries_test = go.Figure(data=[go.Bar(x=['A', 'B', 'C'], y=[5, 3, 7])])
    fig_timeseries_test.update_layout(title_text="模擬詳細時間序列圖")
    mock_plotly_figs['timeseries_detail'] = fig_timeseries_test


    # 4. 準備報告上下文
    report_context_test = {
        "report_title": mock_report_config["report_title"],
        "generation_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "data_range_start": mock_report_config["data_range_start"],
        "data_range_end": mock_report_config["data_range_end"],
        "latest_data_date": text_analysis_res["latest_data_date"],
        "latest_stress_index_value": text_analysis_res["latest_stress_index_value"],
        "historical_comparison_html": text_analysis_res["historical_comparison_html"],
        "scenario_analysis_html": text_analysis_res["scenario_analysis_html"],
        "gemini_analysis_html": gemini_html_output,
        "gemini_error": gemini_error_msg,
    }

    # 5. 編譯 HTML 報告
    test_output_dir = "test_reporter_output"
    os.makedirs(test_output_dir, exist_ok=True)
    html_file_path = compile_html_report(
        report_context_test,
        mock_plotly_figs,
        mock_report_config,
        test_output_dir,
        "test_stress_report_v3"
    )

    if html_file_path:
        logger.info(f"測試 HTML 報告已生成於: {html_file_path}")
    else:
        logger.error("測試 HTML 報告生成失敗。")

# --- 主函式包裝器 (SOP v3.0 要求) ---

def prepare_report_data(
    data: 'VisualizationData', # 實際應為 schemas.VisualizationData
    no_text: bool,
    use_ai_refine: bool,
    gemini_api_key: Optional[str], # 從環境變數或 app_config 傳入
    logger_instance: Optional[logging.Logger] = None
) -> 'ReportData': # 實際應為 schemas.ReportData
    """
    報告數據準備階段的主函式。
    依照 SOP v3.0，此函式接收 VisualizationData，準備文字分析內容，
    並返回一個符合 ReportData 合約的 Pydantic 模型實例。

    Args:
        data (VisualizationData): 包含 plotly_fig, final_df, AppConfig 的 Pydantic 模型。
        no_text (bool): 是否跳過文字分析生成。
        use_ai_refine (bool): 是否使用 AI 潤飾文字。
        gemini_api_key (Optional[str]): Gemini API 金鑰。
        logger_instance (Optional[logging.Logger]): 日誌記錄器實例。

    Returns:
        ReportData: 包含最終報告所需全部數據的 Pydantic 模型。
    """
    current_logger = logger_instance if logger_instance else logging.getLogger(__name__)
    current_logger.info("進入 prepare_report_data 主函式...")

    final_df_input = data.final_df
    plotly_fig_input = data.plotly_fig
    app_config = data.config # Pydantic AppConfig 實例

    # 提取 reporter 和 gemini 相關的設定 (Pydantic 模型)
    # report_settings_config_model = app_config.report_settings
    gemini_specific_config_model = app_config.gemini_config

    # 將 Pydantic 模型轉換為字典以兼容現有函式簽名
    # config_dict_for_generate_text = app_config.model_dump() # generate_text_analysis 可能需要整個 config
    gemini_config_dict_for_call = gemini_specific_config_model.model_dump() if gemini_specific_config_model else {}


    text_analysis_html_content = "" # 初始化文字分析內容
    gemini_html_output = None
    gemini_error_message = None

    if not no_text:
        current_logger.info("--- [子任務] 開始生成規則型文字分析 ---")
        # generate_text_analysis 需要 final_df 和 config (字典格式)
        # 注意：原始的 generate_text_analysis 返回的是一個包含多個 HTML 片段的字典
        # 我們需要將其組合成一個單一的 HTML 字串，或者修改 generate_text_analysis
        # 這裡假設我們將其組合成一個字串
        # 為了簡化，我們將 generate_text_analysis 的返回值處理邏輯放在這裡

        # generate_text_analysis 期望的是字典 config
        text_analysis_results_dict = generate_text_analysis(final_df_input, app_config.model_dump())

        # 將 text_analysis_results_dict 中的 HTML 片段組合成一個 text_analysis_html_content
        # 這裡需要根據 DEFAULT_REPORT_TEMPLATE_HTML 中 text-analysis section 的結構來組合
        # 為了符合 ReportData.text_analysis: str 的要求，這裡簡單拼接
        # 實際應用中可能需要更精細的模板或組合邏輯
        # 或者，ReportData.text_analysis 可以是 Dict[str, str]，然後 compile_html_report 處理
        # 暫時將主要部分拼接：
        temp_text_parts = [
            f"<p><strong>當前壓力指數 ({text_analysis_results_dict.get('latest_data_date', 'N/A')})：</strong> {text_analysis_results_dict.get('latest_stress_index_value', 'N/A')}</p>",
            text_analysis_results_dict.get('historical_comparison_html', "<p>無歷史比較數據。</p>")
        ]
        # 注意：舊的 run.py 中，gemini_input_data 是從 text_analysis_results 中獲取的
        # 情境分析是獨立的
        # scenario_analysis_html = text_analysis_results_dict.get("scenario_analysis_html", "<p>無情境分析數據。</p>")
        # 為了簡化，我們把 text_analysis 的核心部分放入 ReportData.text_analysis
        # 而 gemini 和 scenario 可以在 compile_html_report 時從 ReportData.config 或直接從 context 獲取

        text_analysis_html_content = "\n".join(temp_text_parts)
        current_logger.info(f"--- [子任務] 規則型文字分析生成完成。長度: {len(text_analysis_html_content)} ---")

        if use_ai_refine and text_analysis_results_dict.get("gemini_input_data"):
            current_logger.info("--- [子任務] 開始 AI 文字潤飾 ---")
            if gemini_api_key and gemini_specific_config_model: # 確保金鑰和設定都存在
                ai_text_raw = call_gemini_api(
                    text_analysis_results_dict["gemini_input_data"],
                    gemini_api_key,
                    gemini_config_dict_for_call # 傳遞 Gemini 專用設定字典
                )
                if ai_text_raw and "失敗" not in ai_text_raw and "未能" not in ai_text_raw and "套件未安裝" not in ai_text_raw and "金鑰未設定" not in ai_text_raw:
                    # call_gemini_api 內部已經將 \n 轉為 <br>
                    gemini_html_output = f"<p>{ai_text_raw}</p>" # 包裹在 p 標籤內
                    current_logger.info("--- [子任務] AI 文字潤飾成功。---")
                else:
                    gemini_error_message = ai_text_raw or "未知 Gemini API 錯誤"
                    current_logger.warning(f"--- [子任務] AI 文字潤飾失敗: {gemini_error_message} ---")
            else:
                gemini_error_message = "已請求 AI 潤飾，但缺少 Gemini API 金鑰或 Gemini 設定。"
                current_logger.warning(f"--- [子任務] 跳過 AI 文字潤飾: {gemini_error_message} ---")
        else:
            current_logger.info("--- [子任務] 跳過 AI 文字潤飾 (未啟用、缺少金鑰或無 Gemini 輸入數據)。---")
    else:
        current_logger.info("--- [子任務] 跳過文字分析生成 (因 --no-text 參數)。---")


    # 導入 ReportData 模型
    from .schemas import ReportData

    # 封裝到 ReportData Pydantic 模型
    # ReportData.text_analysis 應該是主要的文字分析內容
    # Gemini 的輸出和錯誤信息可以作為 ReportData 的可選欄位，或者在 compile_html_report 中處理
    # 為了簡化，我們將 Gemini 的結果也整合到 text_analysis 欄位中，或者 ReportData 需要擴展
    # 根據 SOP v3.0 ReportData 定義，text_analysis: str = ""
    # 我們可以決定 text_analysis 包含核心規則分析，Gemini 內容由 compile_html_report 處理
    # 或者，如果 ReportData 要包含所有文字，那麼它的 text_analysis 欄位需要更結構化

    # 暫定：ReportData.text_analysis 存儲核心文字分析。
    # Gemini 的內容和錯誤，以及情境分析，將由 compile_html_report 在渲染模板時
    # 從 ReportData.config 或其他地方獲取。
    # 或者，修改 ReportData schema 以包含這些可選欄位。
    # 根據當前 ReportData schema (text_analysis: str)，我們只傳入核心文字。
    # 這意味著 compile_html_report 需要重新獲取 gemini_input_data 等來構造上下文。
    # --> 修正：為了讓 compile_html_report 更獨立，ReportData 應該包含所有必要的文本片段。
    # --> 這需要修改 schemas.py 中的 ReportData 定義，增加如 gemini_analysis_html: Optional[str], scenario_analysis_html: Optional[str] 等。
    # --> 假設 schemas.py 中的 ReportData 已更新為包含這些 (或者 text_analysis 是一個更結構化的字典/模型)
    # --> 為了不修改 schemas.py (按當前步驟)，我們將所有文字內容合併到 text_analysis 欄位，用特殊標記分隔，
    #     然後 compile_html_report 再去解析。這比較hacky。
    # --> 更優的方案：讓 ReportData 包含所有最終渲染所需的數據。
    #     如果 ReportData.text_analysis 就是最終要插入模板的 HTML 字符串，
    #     那麼 prepare_report_data 需要負責組合好所有文字部分。

    # 折衷方案：text_analysis 存儲核心部分。Gemini 和情境分析的數據由 compile_html_report 從 config 和原始數據重新生成。
    # 這不是最優的，因為 compile_html_report 會重複一些邏輯。
    # 最優方案是 ReportData 包含所有渲染所需的最終文本。
    # 假設 text_analysis 就是最終的主要文本內容，不包含 Gemini。
    # Gemini 的部分由 compile_html_report 自己處理。

    report_data_output = ReportData(
        final_df=final_df_input,
        plotly_fig=plotly_fig_input,
        text_analysis=text_analysis_html_content, # 只包含核心文字分析
        config=app_config # 繼續傳遞完整的 AppConfig 實例
    )

    current_logger.info("prepare_report_data 主函式執行完畢。")
    return report_data_output


def compile_and_save_report(
    data: 'ReportData', # 實際應為 schemas.ReportData
    output_format: str,
    output_dir: str,
    base_filename: str,
    logger_instance: Optional[logging.Logger] = None
) -> Optional[str]:
    """
    編譯並儲存最終報告的主函式。
    依照 SOP v3.0，此函式接收 ReportData，根據 output_format 生成報告檔案。

    Args:
        data (ReportData): 包含所有報告所需數據的 Pydantic 模型。
        output_format (str): 'html' 或 'md'。
        output_dir (str): 報告輸出的目錄。
        base_filename (str): 報告檔案的基礎名稱 (不含副檔名)。
        logger_instance (Optional[logging.Logger]): 日誌記錄器實例。

    Returns:
        Optional[str]: 成功時返回報告檔案的完整路徑，否則返回 None。
    """
    current_logger = logger_instance if logger_instance else logging.getLogger(__name__)
    current_logger.info(f"進入 compile_and_save_report 主函式 (格式: {output_format})...")

    app_config = data.config # Pydantic AppConfig 實例
    final_df_for_report = data.final_df
    plotly_fig_for_report = data.plotly_fig
    core_text_analysis_html = data.text_analysis # 這是來自 prepare_report_data 的核心文字

    # 準備 HTML 報告的完整上下文 (與舊 run.py 邏輯類似，但數據源是 ReportData)
    if output_format == 'html':
        # 重新獲取 Gemini 輸入數據 (如果需要)
        # 注意：這部分邏輯與 prepare_report_data 重複，理想情況下 ReportData 應包含最終的 gemini_html_output
        # 為了演示，這裡重複一次邏輯
        gemini_html_output_final = None
        gemini_error_msg_final = None
        # 假設 app.py 傳遞了 use_ai_refine 和 gemini_api_key (可以考慮將它們也加入 ReportData.config 或 AppConfig)
        # 這裡我們從 AppConfig 的 gemini_config 和環境變數獲取
        gemini_api_key_env = os.getenv('API_KEY_GEMINI') # 再次獲取，確保是最新的

        # 重新執行 generate_text_analysis 以獲取 gemini_input_data (這是不理想的重複)
        # 更好的做法是讓 prepare_report_data 返回更完整的結構，或者 ReportData 包含 gemini_input_data
        # 假設我們能從 final_df_for_report 和 app_config.model_dump() 中獲取 gemini_input_data
        # 此處簡化：假設 prepare_report_data 已經將 gemini_html_output 和 error 存入某個地方
        # 或者，我們修改 ReportData schema 來包含它們。
        # 按照當前 ReportData schema，我們無法直接獲得 Gemini 內容。
        # 解決方案：在 compile_html_report 中重新調用 call_gemini_api。
        # 這需要 text_analysis_results_dict，它在 prepare_report_data 中生成。
        # 這表明 prepare_report_data 和 compile_and_save_report 的職責劃分需要更清晰。
        # 假設：我們需要重新生成 text_analysis_results_dict

        temp_text_analysis_results = generate_text_analysis(final_df_for_report, app_config.model_dump())

        if app_config.gemini_config and gemini_api_key_env and temp_text_analysis_results.get("gemini_input_data"): # 假設 use_ai_refine 由 app_config 控制
            ai_text_final_raw = call_gemini_api(
                temp_text_analysis_results["gemini_input_data"],
                gemini_api_key_env,
                app_config.gemini_config.model_dump()
            )
            if ai_text_final_raw and "失敗" not in ai_text_final_raw and "未能" not in ai_text_final_raw and "套件未安裝" not in ai_text_final_raw and "金鑰未設定" not in ai_text_final_raw:
                gemini_html_output_final = f"<p>{ai_text_final_raw}</p>"
            else:
                gemini_error_msg_final = ai_text_final_raw or "未知 Gemini API 錯誤"
        elif app_config.gemini_config and not gemini_api_key_env:
             gemini_error_msg_final = "已請求 AI 潤飾，但缺少 Gemini API 金鑰。"


        report_context_data = {
            "report_title": app_config.report_settings.report_title,
            "generation_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            # data_range_start/end 應該來自 app_config 或從 final_df 推斷
            "data_range_start": final_df_for_report.index.min().strftime('%Y-%m-%d') if not final_df_for_report.empty else "N/A",
            "data_range_end": final_df_for_report.index.max().strftime('%Y-%m-%d') if not final_df_for_report.empty else "N/A",

            # 以下來自 temp_text_analysis_results (這部分是重複邏輯)
            "latest_data_date": temp_text_analysis_results.get("latest_data_date", "N/A"),
            "latest_stress_index_value": temp_text_analysis_results.get("latest_stress_index_value", "N/A"),
            "historical_comparison_html": temp_text_analysis_results.get("historical_comparison_html", "<p>無歷史比較數據。</p>"),
            "scenario_analysis_html": temp_text_analysis_results.get("scenario_analysis_html", "<p>無情境分析數據。</p>"),

            "gemini_analysis_html": gemini_html_output_final,
            "gemini_error": gemini_error_msg_final,
        }

        charts_plotly_figs_dict = {}
        if plotly_fig_for_report:
            charts_plotly_figs_dict['main_dashboard'] = plotly_fig_for_report

        # 調用舊的 compile_html_report 輔助函式
        # 注意：舊的 compile_html_report 簽名是 (report_context, charts_plotly_figs, config_dict, output_dir, base_filename)
        # 我們需要傳遞 app_config.model_dump() 作為 config_dict
        html_report_path = compile_html_report( # 這是指 reporter.py 內部的同名輔助函式
            report_context=report_context_data,
            charts_plotly_figs=charts_plotly_figs_dict,
            config=app_config.model_dump(), # 傳遞整個 config 的字典形式
            output_dir=output_dir,
            base_filename=base_filename
        )
        if html_report_path:
            current_logger.info(f"HTML 報告已成功編譯並儲存於: {html_report_path}")
            return html_report_path
        else:
            current_logger.error("HTML 報告編譯或儲存失敗。")
            return None

    elif output_format == 'md':
        # Markdown 報告生成邏輯
        md_report_path = os.path.join(output_dir, f"{base_filename}.md")
        # 這裡需要實現 Markdown 內容的生成
        # 為了演示，創建一個簡單的佔位 Markdown
        md_content_lines = [
            f"# {app_config.report_settings.report_title}",
            f"報告生成時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"數據範圍：{final_df_for_report.index.min().strftime('%Y-%m-%d') if not final_df_for_report.empty else 'N/A'} 至 {final_df_for_report.index.max().strftime('%Y-%m-%d') if not final_df_for_report.empty else 'N/A'}",
            "\n## 文字分析摘要",
            "（此處應填入從 ReportData.text_analysis 轉換或提取的 Markdown 格式文字）",
            core_text_analysis_html.replace("<p>", "").replace("</p>", "\n").replace("<li>", "- ").replace("</li>", "").replace("<ul>", "").replace("</ul>",""), # 簡易 HTML 轉 MD
            "\n## 圖表",
            "（Markdown 格式不直接嵌入 Plotly 圖表，可考慮儲存為圖片並鏈接）"
        ]
        try:
            with open(md_report_path, 'w', encoding='utf-8') as f:
                f.write("\n".join(md_content_lines))
            current_logger.info(f"Markdown 報告（佔位）已成功生成於: {md_report_path}")
            return md_report_path
        except Exception as e:
            current_logger.error(f"儲存 Markdown 報告時發生錯誤: {e}", exc_info=True)
            return None
    else:
        current_logger.warning(f"不支援的報告輸出格式: {output_format}。跳過編譯與儲存。")
        return None

    current_logger.info("compile_and_save_report 主函式執行完畢。")
    # 此處應該有返回值，但原始碼中 compile_html_report 返回路徑或 None
    # compile_and_save_report 也應該遵循此模式
    return None # 如果沒有進入 html 或 md 分支
