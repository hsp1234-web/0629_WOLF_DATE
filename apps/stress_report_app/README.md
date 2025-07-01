# README - 壓力指數報告應用程式 (stress_report_app)

本應用程式 (`stress_report_app`) 是一個基於「應用程式容器化 (App-in-a-Box)」開發範式（遵循 SOP v3.0 標準）構建的微型應用，專門用於生成市場壓力指數報告。其核心設計理念是確保模組間接口的明確性、數據流的穩定性與可驗證性。

## 架構概述

`stress_report_app` 遵循 SOP v3.0 的核心思想，將自身視為一個標準化的「貨櫃」。開發和測試的重點在於確保此貨櫃擁有標準的外部接口（如何啟動它）和清晰、可驗證的內部數據流（數據如何在其內部各組件間傳遞）。

### 核心特性：

*   **數據合約 (Data Contracts)**：應用程式內部流動的數據結構通過 `schemas.py` 中定義的 Pydantic 模型進行嚴格的「合約」定義。這確保了數據在各個處理階段都具有可被強制驗證的「形狀」，從 Daunting在早期暴露數據不匹配問題。
*   **應用容器 (App Container)**：`app.py` 作為此應用的唯一標準化外部執行入口。它負責解析命令列參數，並以「流水線 (Pipeline)」的方式依序調用各個功能模組。
*   **獨立驗收 (Test Probe)**：`_test_harness.py` 提供了一個外部驗收工具，能夠模擬真實的執行環境，對應用容器進行端到端的測試。

## 數據合約 (`schemas.py`)

`schemas.py` 文件是本應用程式數據流的「唯一真相來源 (Single Source of Truth)」。它使用 Pydantic 的 `BaseModel` 定義了流水線中每個關鍵節點的數據輸入和輸出結構。

### 主要數據模型：

*   **`AppConfig`**: 定義了應用程式所需的完整設定資訊（從 `config/project_config.yaml` 載入並驗證），包括數據獲取參數、計算參數、報告設定、視覺化設定等。
*   **`FetchedData`**: 代表數據獲取階段 (`data_fetcher.py`) 的輸出。包含合併後的原始數據 (`merged_df`) 和傳遞下來的 `AppConfig`。
*   **`CalculatedData`**: 代表指標計算階段 (`calculator.py`) 的輸出。包含計算了各種指標後的數據 (`final_df`) 和傳遞下來的 `AppConfig`。
*   **`VisualizationData`**: 代表視覺化階段 (`visualizer.py`) 的輸出。包含生成的 Plotly 圖表物件 (`plotly_fig`)、計算後的數據 (`final_df`，繼續傳遞) 和 `AppConfig`。
*   **`ReportData`**: 代表報告數據準備階段 (`reporter.py`) 的最終數據合約，也是最終報告編譯所需的全部數據。包含最終數據 (`final_df`)、Plotly 圖表物件 (`plotly_fig`)、生成的文字分析 (`text_analysis`) 和 `AppConfig`。

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
# 或者，如果 _test_harness.py 的 if __name__ == "__main__": 部分被配置為執行 test_app_container_execution()
#可以直接執行:
# API_KEY_FRED="YOUR_KEY" python apps/stress_report_app/_test_harness.py
```

**執行前提**：

*   確保您的環境中已安裝所有必要的依賴（參考下文「主要依賴」）。
*   確保 FRED API 金鑰已設置在環境變數 `API_KEY_FRED` 中。如果未設置，測試探針會使用一個虛擬金鑰，這將導致數據獲取階段的 FRED API 調用失敗 (返回空數據或警告)，但測試流程仍會繼續以驗證後續的數據處理邏輯。
*   如果希望測試 AI 潤飾功能 (目前 `_test_harness.py` 未強制啟用此功能)，需將 Gemini API 金鑰設置在環境變數 `API_KEY_GEMINI` 中。

測試成功時，將會打印出「✅ [SOP v3.0] 應用程式容器化測試成功！」的訊息，並且如果測試的是報告生成流程，還會提示報告檔案的生成路徑。

## 主要依賴

*   **Python 3.11+**：本應用程式建議在此版本的 Python 環境中運行。
*   **Pydantic (`pydantic`)**: 用於定義和強制執行內部數據流的數據合約 (`schemas.py`)，確保各模組間的接口穩定性和數據驗證。
*   **Pandas (`pandas`)**: 主要用於時間序列數據的處理、操作和分析。
*   **Plotly (`plotly`)**: 負責生成報告中所需的互動式數據視覺化圖表。
*   **PyYAML (`PyYAML`)**: 用於載入和解析 YAML 格式的設定檔 (`config/project_config.yaml`)。
*   **Requests (`requests`)**: 用於從網路獲取數據 (例如 NY Fed 的 Excel 檔案)。
*   **FredAPI (`fredapi`)**: 用於與 FRED (Federal Reserve Economic Data) API 交互以獲取經濟數據。
*   **yfinance (`yfinance`)**: 用於從 Yahoo Finance API 獲取市場數據。
*   **Openpyxl**: 作為 Pandas 讀取 `.xlsx` 檔案的底層引擎。
*   **curl_cffi**: 用於模擬瀏覽器行為進行 HTTP 請求，特別是在嘗試解決 NY Fed 數據下載問題時引入。

詳細的依賴列表請參見專案根目錄下的 `requirements.txt` 文件。

## 變更日誌與疊代過程 (SOP4 驗證 - Jules - 2025-07-01)

本次疊代主要目標是根據 SOP4 指導原則，在 Jules 沙箱中獨立驗證 `stress_report_app` 的核心模組 (`data_fetcher.py`, `calculator.py`) 以及 `app.py` 的端到端流程。

### 主要疊代步驟與發現：

1.  **環境與依賴準備**：
    *   初始執行 `_test_harness.py` 時發現缺少 `pandas` 依賴。通過安裝根目錄 `requirements.txt` 解決。
    *   後續發現解析 NY Fed Excel 檔案時缺少 `openpyxl` 依賴。將其添加到 `requirements.txt` 並安裝後解決。
    *   為嘗試改進 NY Fed 數據獲取，引入 `curl_cffi` 並加入 `requirements.txt`。

2.  **Pydantic 配置與錯誤處理修正**：
    *   `app.py` 在載入 `project_config.yaml` 時，由於 `AppConfig` Pydantic 模型預設不允許額外欄位 (`extra='forbid'`)，而配置文件中存在 `runner_settings`，導致 `ValidationError`。已修改 `schemas.py` 中的 `AppConfig.Config`，將 `extra` 設置為 `'ignore'` 以兼容。
    *   `app.py` 中處理 `ValidationError` 時，`e.json(ensure_ascii=False)` 語法與當前 Pydantic 版本不兼容，導致 `TypeError`。已修正為 `e.json()`。
    *   Colab 驗收平台傳遞了未被 `app.py` argparse 定義的 `--debug` 參數，導致 `unrecognized arguments` 錯誤。已在 `app.py` 中添加對 `--debug` 參數的定義以兼容。

3.  **`_test_harness.py` 增強**：
    *   原 `_test_harness.py` 主要測試 `app.py` 的容器化執行（輸出為 `test_run`）。
    *   新增 `test_core_modules()` 函數，用於獨立調用 `data_fetcher.fetch_all_data()` 和 `calculator.calculate_all_indicators()`，以便更細緻地驗證這兩個核心模組的功能。
    *   修改 `test_app_container_execution()` 函數，使其請求 `html` 格式的報告輸出，並在執行成功後檢查實際報告檔案是否生成在 `data_workspace/output/reports/` 目錄下。
    *   調整了 `_test_harness.py` 的主執行邏輯，以方便切換執行 `test_core_modules` 或 `test_app_container_execution`。

4.  **`data_fetcher.py` 修正與驗證**：
    *   **FRED API 金鑰處理**：確認 API 金鑰通過環境變數 `API_KEY_FRED` 傳遞，並在 `_test_harness.py` 中使用真實金鑰進行了測試。
    *   **FRED 基礎數據配置讀取錯誤**：發現 `get_fred_base_data` 等函數在從傳入的 `config` 字典（實際上是 `app_config.data_fetching.model_dump()` 的結果）中讀取更深層次的配置（如 `fred_series_map`）時，路徑不正確。已修正此邏輯，確保正確讀取配置。修正後，所有基礎 FRED 數據（SOFR, DGS10, Reserves 等）均能成功抓取。
    *   **VIX 欄位名統一**：`get_vix_index` 可能返回名為 `VIX_Index` 或 `VIX_Yahoo` 的 Series，但 `calculator.py` 期望的是 `VIX`。已在 `fetch_all_data` 的合併邏輯中將 VIX 指數的欄位名統一為 `VIX`。
    *   **NY Fed Excel 數據獲取問題**：
        *   最初 `fetch_nyfed_data` 中的 `NameError` (變數 `url` 未定義) 已修正。
        *   改進了 Excel 解析邏輯，嘗試基於 URL 關鍵字 ("sbn", "sbp") 指定表頭行號，並擴大了自動檢測表頭的備案邏輯。
        *   通過在 `fetch_nyfed_data` 中添加日誌打印下載內容的 `Content-Type` 和內容預覽，確認了從 NY Fed URL 下載到的是 HTML 頁面而非 Excel 檔案。
        *   嘗試使用 `curl_cffi.requests` 並模擬 Chrome 瀏覽器 (`impersonate="chrome110"`) 來下載 NY Fed Excel 檔案。儘管 `curl_cffi` 執行了請求，但返回的內容依然是 HTML 頁面，表明 NY Fed 的防護機制較為複雜，簡單的 User-Agent 模擬不足以獲取直接的檔案流。
        *   **目前狀態**：NY Fed 數據仍然無法通過直接 HTTP(S) 請求成功解析，但程式能夠優雅處理此錯誤（返回空 Series，不崩潰）。

5.  **`calculator.py` 驗證**：
    *   **衍生指標**：在 FRED 數據可用的情況下，`Spread_10Y2Y` 和 `SOFR_Dev` 等指標計算正常。`Pos_Res_Ratio` 因依賴的 NY Fed 持有量數據缺失而無法計算。
    *   **壓力指數與 MACD**：
        *   最初由於測試數據時間範圍過短（約3個月），導致滾動百分位排名無法計算（數據點少於 `min_periods_rank`）。
        *   將 `_test_harness.py` 中 `test_core_modules` 的測試日期範圍擴大到約1年3個月後，`sofr_dev`, `spread_inv`, `move`, `vix` 的滾動排名均能成功計算。
        *   因此，`Dealer_Stress_Index` 和 `Stress_Index_MACD_Hist` 也成功計算出來（儘管壓力指數仍缺少 NY Fed 相關的兩個成分）。
    *   **結論**：`calculator.py` 的核心計算邏輯在獲得有效輸入數據（且數據量足夠進行滾動計算）時是正確的。

6.  **`app.py` 端到端流程驗證**：
    *   在解決了上述依賴、配置和核心模組的數據問題後，通過修改 `_test_harness.py` 中的 `test_app_container_execution` 函數（使其請求 HTML 輸出並使用擴展日期範圍），成功模擬了 `app.py` 的完整執行流程。
    *   `app.py` 能夠正確接收命令列參數，協調 `data_fetcher`（獲取 FRED 和 Yahoo 數據）、`calculator`（計算指標）、`visualizer`（生成圖表）和 `reporter`（編譯報告），最終成功在 `data_workspace/output/reports/` 目錄下生成 HTML 格式的壓力報告。

### 總結與後續建議：

*   `stress_report_app` 的核心數據處理和計算模組 (`data_fetcher.py`, `calculator.py`) 以及應用主流程 (`app.py`) 在本次 SOP4 驗證後，功能更加健全和可靠（除 NY Fed 數據源直接下載問題外）。
*   `_test_harness.py` 已被大幅增強，可以作為一個有效的工具來獨立驗證核心模組功能和模擬完整的應用執行。
*   **最主要的遺留問題是 NY Fed Excel 檔案的獲取**。當前 URL 返回 HTML 頁面，即使嘗試使用 `curl_cffi` 模擬瀏覽器也未能直接獲取到 Excel 檔案。建議後續：
    1.  人工深入分析 NY Fed 網站，嘗試找到穩定的直接下載連結或理解其下載機制（可能涉及 JavaScript 或特定請求標頭/流程）。
    2.  **務實的替代方案**：考慮將這些歷史性的 NY Fed Excel 檔案預先下載，並存儲在專案的版本控制系統可訪問的路徑下（例如 `data_workspace/input/nyfed_excel/`）。然後修改 `fetch_nyfed_data` 函數，使其優先從本地檔案系統讀取這些預存的檔案。如果本地檔案不存在，再嘗試（可能仍然失敗的）網路下載作為備案。這將極大提高數據獲取的穩定性和測試的可靠性。
*   確保所有 API 金鑰都通過環境變數管理，程式碼中沒有硬編碼。本次檢查確認了這一點。

---
