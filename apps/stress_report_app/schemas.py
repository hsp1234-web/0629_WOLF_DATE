# -*- coding: utf-8 -*-
"""
數據合約模組 (schemas.py) - SOP v3.0

定義應用程式內部數據流的 Pydantic 模型。
這些模型是各個處理階段輸入和輸出的「唯一真相來源」。
"""
from pydantic import BaseModel, Field, HttpUrl
from typing import Optional, List, Dict, Any
import pandas as pd
import plotly.graph_objects as go

# --- AppConfig 的子模型 ---
class FredSeriesMapConfig(BaseModel):
    """FRED 數據源系列對應關係設定"""
    SOFR: str = Field(..., description="美國隔夜融資利率 (Secured Overnight Financing Rate) 的 FRED 代碼")
    DGS10: str = Field(..., description="10年期美國國債殖利率的 FRED 代碼")
    DGS2: str = Field(..., description="2年期美國國債殖利率的 FRED 代碼")
    RRP: str = Field(..., description="隔夜逆回購協議 (Overnight Reverse Repurchase Agreements) 的 FRED 代碼")
    VIX_FRED: str = Field(..., description="CBOE 波動率指數 (VIX) 的 FRED 代碼 (備用)")
    Reserves: str = Field(..., description="銀行準備金總額的 FRED 代碼")
    DTB3: str = Field(..., description="3個月期美國國庫券利率的 FRED 代碼")
    # 可以根據實際 project_config.yaml 繼續擴充

class YahooTickersMapConfig(BaseModel):
    """Yahoo Finance Ticker 對應關係設定"""
    Volatility_Index: str = Field(..., description="波動率指數 (如 MOVE 指數) 在 Yahoo Finance 的 Ticker")
    VIX: str = Field(..., description="CBOE 波動率指數 (VIX) 在 Yahoo Finance 的 Ticker (主要)")
    TLT_Close: str = Field(..., description="iShares 20+ Year Treasury Bond ETF (TLT) 收盤價的 Ticker")
    # 可以根據實際 project_config.yaml 繼續擴充

class SbpColsConfig(BaseModel):
    """NY Fed SBP/SBN 數據加總欄位設定"""
    SBP: List[str] = Field(default_factory=list, description="適用於 SBP (Securities Held Outright by Primary Dealers) 檔案的加總欄位列表")
    SBN: List[str] = Field(default_factory=list, description="適用於 SBN (SOMA Holdings Net) 檔案的加總欄位列表 (通常 SBN 會自動檢測 PDPOSGSC-)")

class DataFetchingConfig(BaseModel):
    """數據獲取階段的詳細設定"""
    fred_series_map: FredSeriesMapConfig = Field(..., description="FRED 數據系列設定")
    fred_move_ticker: str = Field(..., description="MOVE 指數在 FRED 的 Ticker (備用)")
    yahoo_tickers_map: YahooTickersMapConfig = Field(..., description="Yahoo Finance Ticker 設定")
    nyfed_data_urls: List[HttpUrl] = Field(default_factory=list, description="NY Fed 持倉數據的 Excel 檔案 URL 列表")
    sbp_cols_config: SbpColsConfig = Field(..., description="NY Fed SBP/SBN 數據加總欄位設定")

class CalculationParamsConfig(BaseModel):
    """指標計算階段的參數設定"""
    # 根據 calculator.py 和 project_config.yaml 中實際使用的參數來定義
    # 範例:
    stress_index_weights: Dict[str, float] = Field(..., description="壓力指數各成分的權重")
    macd_fast_period: int = Field(12, description="MACD 快線週期")
    macd_slow_period: int = Field(26, description="MACD 慢線週期")
    macd_signal_period: int = Field(9, description="MACD 信號線週期")
    # ... 其他計算參數
    rolling_window_days: int = Field(252, description="計算百分位排名時的滾動窗口天數")
    smoothing_window_stress_index: int = Field(5, description="壓力指數的平滑窗口天數")
    threshold_ratio_color: float = Field(90.0, description="Pos/Res Ratio 超過此值時，在壓力指數計算中給予更高關注的閾值") # 原為整數，改為 float
    enable_macd_momentum_plot: bool = Field(False, description="是否啟用並繪製 MACD 動能指標")
    # weights 和 macd_params 結構較複雜，可以考慮拆分成更小的模型
    weights: Dict[str, float] = Field(..., description="壓力指數各成分的原始權重")
    macd_params: Dict[str, int] = Field(default_factory=lambda: {"fast": 12, "slow": 26, "signal": 9}, description="MACD 指標參數")
    macd_colors: Dict[str, str] = Field(default_factory=lambda: {"blue": "#6495ED", "green": "#3CB371", "red": "#B22222"}, description="MACD 圖表顏色")


class ReportSettingsConfig(BaseModel):
    """報告生成相關設定"""
    report_title: str = Field("交易商壓力指數報告", description="報告的主標題")
    # ... 其他報告設定

class GeminiConfig(BaseModel):
    """Gemini API 相關設定"""
    model_name: str = Field("gemini-pro", description="使用的 Gemini 模型名稱")
    temperature: float = Field(0.7, ge=0.0, le=1.0, description="Gemini API 的溫度參數")
    # safety_settings: Optional[List[Dict[str, Any]]] = Field(None, description="Gemini 安全設定") # 實際結構更複雜
    # top_p: Optional[float] = Field(None, description="Gemini top_p 參數")
    # top_k: Optional[int] = Field(None, description="Gemini top_k 參數")


class VisualizationChartParamsConfig(BaseModel):
    """圖表視覺化參數，例如圖表中文標題"""
    dashboard_main_name_html: str = Field("主儀表板與趨勢圖", description="主儀表板圖表的 HTML 標題")
    main_plot_titles: Dict[str, str] = Field(default_factory=dict, description="主要時間序列子圖的標題 (key 為 plot_key, value 為中文標題)")
    # ... 其他圖表的中文名稱設定

class VisualizationPlotsConfig(BaseModel):
    """控制視覺化模組中哪些子圖被啟用的設定"""
    SOFR: bool = Field(True, description="是否顯示 SOFR 圖表")
    Spread: bool = Field(True, description="是否顯示利差圖表")
    MOVE: bool = Field(True, description="是否顯示 MOVE 指數圖表")
    VIX: bool = Field(True, description="是否顯示 VIX 指數圖表")
    DealerPos: bool = Field(False, description="是否顯示交易商持倉圖表") # 假設預設關閉
    Reserves: bool = Field(False, description="是否顯示準備金圖表") # 假設預設關閉
    StressIndex: bool = Field(True, description="是否顯示壓力指數時間序列圖表")
    Ratio: bool = Field(False, description="是否顯示 Pos/Res Ratio 圖表") # 假設預設關閉
    MACD: bool = Field(True, description="是否顯示 MACD 動能指標圖表 (如果 calculation_params 中也啟用)")
    ETF: bool = Field(False, description="是否顯示 ETF 圖表 (例如 TLT)") # 假設預設關閉

class VisualizationGaugeThresholdsConfig(BaseModel):
    """儀表板 (Gauge) 的顏色閾值設定"""
    high: int = Field(80, description="高壓力閾值 (紅色區域開始)")
    medium: int = Field(60, description="中等壓力閾值 (橙色區域開始)")
    low: int = Field(30, description="低壓力閾值 (黃色區域開始，綠色在此之下)")


class VisualizationParamsConfig(BaseModel):
    """視覺化階段的參數設定"""
    plots_config: VisualizationPlotsConfig = Field(..., description="各子圖啟用設定")
    chart_params: VisualizationChartParamsConfig = Field(..., description="圖表通用參數 (如標題)")
    gauge_thresholds: VisualizationGaugeThresholdsConfig = Field(..., description="儀表板顏色閾值")
    # trend_thresholds: Dict[str, Any] = Field(default_factory=dict, description="主要趨勢圖顏色閾值 (暫未詳細定義)")


class AppConfig(BaseModel):
    """
    定義 config.yaml 的結構合約。
    所有應用程式級別的設定都應在此定義。
    """
    data_fetching: DataFetchingConfig = Field(..., description="數據獲取相關設定")
    calculation_params: CalculationParamsConfig = Field(..., description="指標計算相關參數")
    report_settings: ReportSettingsConfig = Field(..., description="報告生成相關設定")
    gemini_config: Optional[GeminiConfig] = Field(None, description="Gemini API 相關設定 (可選)")
    visualization_params: VisualizationParamsConfig = Field(..., description="視覺化相關參數")

    class Config:
        arbitrary_types_allowed = True # 允許如 pd.DataFrame 等任意類型 (雖然在這個模型中不直接使用)
        extra = 'forbid' # 不允許未定義的額外欄位，確保設定檔的嚴謹性

# --- 流水線數據合約 ---

class FetchedData(BaseModel):
    """數據獲取階段 (data_fetcher.fetch_all_data) 的產出合約"""
    merged_df: pd.DataFrame = Field(..., description="合併後的主要數據框，包含所有獲取的原始時間序列數據")
    config: AppConfig = Field(..., description="經過驗證的應用程式設定物件")

    class Config:
        arbitrary_types_allowed = True

class CalculatedData(BaseModel):
    """指標計算階段 (calculator.calculate_all_indicators) 的產出合約"""
    final_df: pd.DataFrame = Field(..., description="經過所有指標計算後的最終數據框")
    config: AppConfig = Field(..., description="經過驗證的應用程式設定物件 (繼續傳遞)")

    class Config:
        arbitrary_types_allowed = True

class VisualizationData(BaseModel):
    """視覺化階段 (visualizer.create_all_visuals) 的產出合約"""
    plotly_fig: Optional[go.Figure] = Field(None, description="生成的 Plotly 圖表物件 (如果生成)")
    final_df: pd.DataFrame = Field(..., description="指標計算後的數據框 (繼續傳遞，供報告階段使用)")
    config: AppConfig = Field(..., description="經過驗證的應用程式設定物件 (繼續傳遞)")

    class Config:
        arbitrary_types_allowed = True

class ReportData(BaseModel):
    """報告生成數據準備階段 (reporter.prepare_report_data) 的最終數據合約"""
    final_df: pd.DataFrame = Field(..., description="最終數據框 (來自 CalculatedData 或 VisualizationData)")
    plotly_fig: Optional[go.Figure] = Field(None, description="Plotly 圖表物件 (來自 VisualizationData)")
    text_analysis: str = Field("", description="生成的文字分析內容 (可能包含 HTML 標籤)")
    config: AppConfig = Field(..., description="經過驗證的應用程式設定物件 (繼續傳遞)")

    class Config:
        arbitrary_types_allowed = True

# 可以在此處加入一個簡單的 main 區塊來驗證 Pydantic 模型是否能被正確解析
if __name__ == "__main__":
    # 創建一個 AppConfig 的模擬字典 (僅用於本地測試 schemas.py)
    sample_config_dict = {
        "data_fetching": {
            "fred_series_map": {
                "SOFR": "SOFR", "DGS10": "DGS10", "DGS2": "DGS2",
                "RRP": "RRPONTSYD", "VIX_FRED": "VIXCLS",
                "Reserves": "WRESBAL", "DTB3": "DTB3"
            },
            "fred_move_ticker": "MOVEIX",
            "yahoo_tickers_map": {
                "Volatility_Index": "^MOVE", "VIX": "^VIX", "TLT_Close": "TLT"
            },
            "nyfed_data_urls": ["http://example.com/data.xlsx"],
            "sbp_cols_config": {"SBP": ["col1"], "SBN": []}
        },
        "calculation_params": {
            "stress_index_weights": {"param1": 0.5, "param2": 0.5},
            "macd_fast_period": 12,
            "macd_slow_period": 26,
            "macd_signal_period": 9
        },
        "report_settings": {
            "report_title": "每日壓力監控"
        },
        "gemini_config": {
            "model_name": "gemini-1.5-flash",
            "temperature": 0.6
        },
        "visualization_params": {
            "chart_params": {
                "dashboard_main_name_html": "綜合市場壓力儀表板"
            }
        }
    }

    try:
        app_config_instance = AppConfig(**sample_config_dict)
        print("AppConfig 模型成功解析範例數據:")
        print(app_config_instance.model_dump_json(indent=2, ensure_ascii=False))

        # 測試 FetchedData (需要一個假的 DataFrame)
        sample_df = pd.DataFrame({'A': [1, 2], 'B': [3, 4]})
        fetched_data_instance = FetchedData(merged_df=sample_df, config=app_config_instance)
        print("\nFetchedData 模型成功解析範例數據。")
        # print(fetched_data_instance.model_dump_json(indent=2)) # DataFrame 無法直接 JSON 序列化

    except Exception as e:
        print(f"Pydantic 模型解析或驗證失敗: {e}")

    print(f"\nplotly.graph_objects Figure type: {type(go.Figure)}")
    print(f"pandas DataFrame type: {type(pd.DataFrame)}")

"""
欄位說明更新指引：
- AppConfig.data_fetching.fred_series_map: 每個 key 都應有中文說明其代表的經濟數據。
- AppConfig.data_fetching.yahoo_tickers_map: 每個 key 都應有中文說明。
- AppConfig.calculation_params: 根據實際 calculator.py 中從 config 取用的參數來定義，並給予中文說明。
  例如 `stress_index_weights` 的 key 如果是 'Yield_Curve_Spread', 'Corp_Bond_Spread' 等，也應在 description 中說明。
- AppConfig.gemini_config: 若有其他如 safety_settings 等參數，也應加入並說明。
- AppConfig.visualization_params.chart_params: 若有多個圖表，應為每個圖表ID定義一個中文名稱欄位。
"""
