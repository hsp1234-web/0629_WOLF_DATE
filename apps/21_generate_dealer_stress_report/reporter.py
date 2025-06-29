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
from typing import Dict, Any, Optional, List
import plotly.graph_objects as go

# 嘗試導入 Gemini API 相關套件
try:
    import google.generativeai as genai
    GOOGLE_GENERATIVEAI_AVAILABLE = True
except ImportError:
    GOOGLE_GENERATIVEAI_AVAILABLE = False
    genai = None

logger = logging.getLogger(__name__)

DEFAULT_REPORT_TEMPLATE_HTML = """
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ report_title }}</title>
    <style>
        body {{ font-family: 'Arial', 'Noto Sans CJK TC', sans-serif; margin: 20px; line-height: 1.6; }}
        h1, h2, h3 {{ color: #333; }}
        .container {{ max-width: 1000px; margin: auto; background: #f9f9f9; padding: 20px; border-radius: 8px; box-shadow: 0 0 10px rgba(0,0,0,0.1); }}
        .chart-container {{ margin-bottom: 30px; }}
        .section {{ margin-bottom: 20px; }}
        .gemini-analysis {{ background-color: #eef7ff; border-left: 5px solid #2196F3; padding: 15px; margin-top: 15px; }}
        .error-message {{ color: red; font-style: italic; }}
        table {{ border-collapse: collapse; width: 100%; margin-bottom: 20px; }}
        th, td {{ text-align: left; padding: 8px; border-bottom: 1px solid #ddd; }}
        th {{ background-color: #f2f2f2; }}
    </style>
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

# 簡易的模板渲染函式 (不依賴 Jinja2)
def render_template_simple(template_str: str, context: Dict[str, Any]) -> str:
    """簡易的模板渲染"""
    for key, value in context.items():
        # 處理 {{ key }}
        template_str = template_str.replace(f"{{{{ {key} }}}}", str(value))
        # 處理 {{ key | safe }}
        template_str = template_str.replace(f"{{{{ {key} | safe }}}}", str(value))

    # 簡易的 for 循環處理 (僅支持一層 charts_html.items())
    import re
    loop_match = re.search(r"{% for (\w+), (\w+) in charts_html.items() %}(.*?){% endfor %}", template_str, re.DOTALL)
    if loop_match:
        item_name_key = loop_match.group(1) # chart_name
        item_html_key = loop_match.group(2) # chart_html
        loop_content_template = loop_match.group(3)

        charts_html_items = context.get('charts_html', {})
        all_looped_content = []
        for idx, (chart_name_val, chart_html_val) in enumerate(charts_html_items.items()):
            loop_item_context = {
                item_name_key: chart_name_val,
                item_html_key: chart_html_val,
                'loop.index': idx + 1 # 模擬 loop.index
            }
            # 渲染循環內部
            rendered_loop_content = loop_content_template
            for item_key, item_val in loop_item_context.items():
                 rendered_loop_content = rendered_loop_content.replace(f"{{{{ {item_key} }}}}", str(item_val))
                 rendered_loop_content = rendered_loop_content.replace(f"{{{{ {item_key} | safe }}}}", str(item_val))

            # 處理 chart_titles.get(chart_name, chart_name)
            chart_titles = context.get('chart_titles', {})
            actual_chart_title = chart_titles.get(chart_name_val, chart_name_val)
            rendered_loop_content = rendered_loop_content.replace(f"{{{{ chart_titles.get({item_name_key}, {item_name_key}) }}}}", actual_chart_title)

            all_looped_content.append(rendered_loop_content)

        template_str = template_str.replace(loop_match.group(0), "".join(all_looped_content))

    # 簡易的 if 條件處理
    if_gemini_match = re.search(r"{% if gemini_analysis_html %}(.*?){% elif gemini_error %}(.*?){% endif %}", template_str, re.DOTALL)
    if if_gemini_match:
        gemini_html_content = context.get('gemini_analysis_html')
        gemini_error_content = context.get('gemini_error')
        if gemini_html_content:
            template_str = template_str.replace(if_gemini_match.group(0), if_gemini_match.group(1))
        elif gemini_error_content:
            template_str = template_str.replace(if_gemini_match.group(0), if_gemini_match.group(2))
        else: # 都沒有，則移除整個 if 結構
            template_str = template_str.replace(if_gemini_match.group(0), "")

    return template_str


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
        # 簡易模板渲染
        rendered_html = render_template_simple(html_template_str, final_context)

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
