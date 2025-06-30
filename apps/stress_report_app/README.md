# README - 壓力指數報告應用程式 (stress_report_app)

本應用程式 (`stress_report_app`) 是一個基於「應用程式容器化 (App-in-a-Box)」開發範式（遵循 SOP v3.0 標準）構建的微型應用，專門用於生成市場壓力指數報告。其核心設計理念是確保模組間接口的明確性、數據流的穩定性與可驗證性。

## 架構概述

`stress_report_app` 遵循 SOP v3.0 的核心思想，將自身視為一個標準化的「貨櫃」。開發和測試的重點在於確保此貨櫃擁有標準的外部接口（如何啟動它）和清晰、可驗證的內部數據流（數據如何在其內部各組件間傳遞）。

### 核心特性：

*   **數據合約 (Data Contracts)**：應用程式內部流動的數據結構通過 `schemas.py` 中定義的 Pydantic 模型進行嚴格的「合約」定義。這確保了數據在各個處理階段都具有可被強制驗證的「形狀」，從而在早期暴露數據不匹配問題。
*   **應用容器 (App Container)**：`app.py` 作為此應用的唯一標準化外部執行入口。它負責解析命令列參數，並以「流水線 (Pipeline)」的方式依序調用各個功能模組。
*   **獨立驗收 (Test Probe)**：`_test_harness.py` 提供了一個外部驗收工具，能夠模擬真實的執行環境，對應用容器進行端到端的測試。

## 數據合約 (`schemas.py`)

`schemas.py` 文件是本應用程式數據流的「唯一真相來源 (Single Source of Truth)」。它使用 Pydantic 的 `BaseModel` 定義了流水線中每個關鍵節點的數據輸入和輸出結構。

### 主要數據模型：

*   **`AppConfig`**: 定義了應用程式所需的完整設定資訊（從 `config/project_config.yaml` 載入並驗證），包括數據獲取參數、計算參數、報告設定、視覺化設定等。
*   **`FetchedData`**: 代表數據獲取階段 (`data_fetcher.py`) 的輸出。包含合併後的原始數據 (`merged_df`) 和傳遞下來的 `AppConfig`。
*   **`CalculatedData`**: 代表指標計算階段 (`calculator.py`) 的輸出。包含計算了各種指標後的數據 (`final_df`) 和傳遞下來的 `AppConfig`。
*   **`VisualizationData`**: 代表視覺化階段 (`visualizer.py`) 的輸出。包含生成的 Plotly 圖表物件 (`plotly_fig`)、計算後的數據 (`final_df`，繼續傳遞) 和 `AppConfig`。
*   **`ReportData`**: 代表報告數據準備階段 (`reporter.py`) 的輸出，也是最終報告編譯所需的全部數據。包含最終數據 (`final_df`)、Plotly 圖表物件 (`plotly_fig`)、生成的文字分析 (`text_analysis`) 和 `AppConfig`。

## 數據處理流水線

應用程式的執行由 `app.py` 中的主函式進行協調，遵循以下標準化的流水線作業：

1.  **命令列參數解析與設定檔載入**：
    *   `app.py` 解析如 `--start-date`, `--end-date`, `--output-format`, `--config-path` 等命令列參數。
    *   根據 `--config-path` 指定的路徑載入 YAML 設定檔，並使用 `schemas.AppConfig` 模型進行驗證。

2.  **數據獲取 (`data_fetcher.py`)**：
    *   **輸入**：報告的開始日期、結束日期，以及經過驗證的 `AppConfig` Pydantic 物件（特別是 `config.data_fetching` 部分）和 FRED API 金鑰。
    *   **處理**：調用內部函式從 FRED、Yahoo Finance、NY Fed 等來源獲取數據，並進行初步合併。
    *   **輸出**：返回一個 `schemas.FetchedData` 物件，其中包含 `merged_df` (一個 Pandas DataFrame) 和完整的 `AppConfig`。

3.  **指標計算 (`calculator.py`)**：
    *   **輸入**：一個 `schemas.FetchedData` 物件。
    *   **處理**：基於 `FetchedData.merged_df` 和 `FetchedData.config.calculation_params` 中的參數，計算各種衍生指標、壓力指數、MACD 動能等。
    *   **輸出**：返回一個 `schemas.CalculatedData` 物件，其中包含 `final_df` (包含所有計算指標的 Pandas DataFrame) 和完整的 `AppConfig`。

4.  **視覺化 (`visualizer.py`)**：
    *   **輸入**：一個 `schemas.CalculatedData` 物件。
    *   **處理**：如果命令列參數未禁用圖表生成，則基於 `CalculatedData.final_df` 和 `CalculatedData.config.visualization_params` 中的參數，使用 Plotly 生成互動式圖表。
    *   **輸出**：返回一個 `schemas.VisualizationData` 物件，其中包含可能為 `None` 的 `plotly_fig` (Plotly Figure 物件)、繼續傳遞的 `final_df` 和完整的 `AppConfig`。

5.  **報告生成 (`reporter.py`)**：
    *   此階段分為兩部分：
        1.  **數據準備 (`prepare_report_data` 函式)**：
            *   **輸入**：一個 `schemas.VisualizationData` 物件，以及是否生成文字、是否使用 AI 潤飾的布林標誌和 Gemini API 金鑰。
            *   **處理**：基於 `VisualizationData.final_df` 和 `VisualizationData.config` (特別是 `report_settings` 和 `gemini_config`) 生成文字分析內容。
            *   **輸出**：返回一個 `schemas.ReportData` 物件，包含所有最終報告編譯所需的數據。
        2.  **編譯與儲存 (`compile_and_save_report` 函式)**：
            *   **輸入**：一個 `schemas.ReportData` 物件，以及輸出格式 (`html` 或 `md`)、輸出目錄和基礎檔案名。
            *   **處理**：根據指定的格式，使用模板（目前主要針對 HTML）將 `ReportData` 中的數據（包括文字分析和 Plotly 圖表的 HTML 表示）編譯成最終的報告檔案。
            *   **輸出**：在指定的輸出目錄中生成報告檔案。

## 獨立執行與驗收

本應用程式容器可以通過 `_test_harness.py` 腳本進行獨立的端到端驗收測試。此測試探針模擬了外部調用環境，並驗證應用程式是否能按照 SOP v3.0 的標準接口正確執行。

**執行指令**：

在專案根目錄下，執行以下命令：

```bash
python -m apps.stress_report_app._test_harness
```

**執行前提**：

*   確保您的環境中已安裝所有必要的依賴（參考下文「主要依賴」）。
*   確保 FRED API 金鑰已設置在環境變數 `API_KEY_FRED` 中。如果未設置，測試探針會使用一個虛擬金鑰，這將導致數據獲取階段的 FRED API 調用失敗 (返回空數據或警告)，但測試流程仍會繼續以驗證後續的數據處理邏輯。
*   如果希望測試 AI 潤飾功能 (目前 `_test_harness.py` 未強制啟用此功能)，需將 Gemini API 金鑰設置在環境變數 `API_KEY_GEMINI` 中。

測試成功時，將會打印出「✅ [SOP v3.0] 應用程式容器化測試成功！」的訊息。

## 主要依賴

*   **Python 3.11+**：本應用程式建議在此版本的 Python 環境中運行。
*   **Pydantic (`pydantic`)**: 用於定義和強制執行內部數據流的數據合約 (`schemas.py`)，確保各模組間的接口穩定性和數據驗證。
*   **Pandas (`pandas`)**: 主要用於時間序列數據的處理、操作和分析。
*   **Plotly (`plotly`)**: 負責生成報告中所需的互動式數據視覺化圖表。
*   **PyYAML (`PyYAML`)**: 用於載入和解析 YAML 格式的設定檔 (`config/project_config.yaml`)。
*   **Requests (`requests`)**: 用於從網路獲取數據 (例如 NY Fed 的 Excel 檔案)。
*   **FredAPI (`fredapi`)**: 用於與 FRED (Federal Reserve Economic Data) API 交互以獲取經濟數據。
*   **yfinance (`yfinance`)**: 用於從 Yahoo Finance API 獲取市場數據。
*   **Openpyxl**: 作為 Pandas 讀取 `.xlsx` 檔案的底層引擎 (未直接在 `requirements.txt` 中列出，但 Pandas 會需要它)。

詳細的依賴列表請參見專案根目錄下的 `requirements.txt` 文件。
