# TAIFEX 數據供應鏈專案

本專案旨在建立一套高效、穩健的臺灣期貨交易所 (TAIFEX) 數據供應鏈，用於「全景市場分析儀」。它由一系列微應用程式組成，負責數據的偵察、採集、精煉與儲存。

## 專案結構

主要應用程式位於 `apps/` 目錄下：

*   `apps/taifex_data_prospector`：數據偵察兵，用於快速探勘檔案格式與健康狀況。
*   `apps/taifex_data_downloader`：數據採集官，負責從期交所網站批量下載數據。
*   `apps/taifex_data_pipeline`：數據精煉廠，執行 ETL 作業並將數據載入 DuckDB 資料庫。

每個應用程式都包含：
*   `run.py`：主要的執行入口。
*   `_test_run.py`：單元/整合測試腳本。
*   `requirements.txt`：該應用所需的 Python 依賴套件。

## 版本歷史與主要功能 (截至 v7.1 / v18.0 階段二)

本節記錄了專案自初始整合以來的主要開發迭代和功能演進。版本號對應內部開發里程碑。

### **初始整合 (對應 v14.0 BE Master Spec)**

*   **目標**：建立由三個獨立微應用程式組成的 TAIFEX 數據供應鏈基礎架構。
*   **核心交付**：
    *   **`taifex_data_prospector` (偵察兵) v1.0**：
        *   實現對單一檔案的快速格式探勘與健康檢查。
        *   能夠接收 `--file-path` 參數。
        *   輸出 JSON 格式的結構化報告，包含檔案元數據（路徑、大小、修改時間）、偵測編碼（支援 UTF-8, MS950 等）以及內容預覽（前五行）。
        *   新增對 ZIP 檔案的處理，能夠列出 ZIP 內的成員作為預覽。
        *   包含 `_test_run.py`，驗證對文字檔、空檔案、不存在檔案及 ZIP 檔案的探勘。
    *   **`taifex_data_downloader` (採集官) v1.0 (同步)**：
        *   基於 `臺灣期交所(TAIFEX)數據中心 - v3.0` Colab 腳本改編。
        *   實現參數化入口，可接收日期範圍、輸出路徑及多種數據類型開關。
        *   下載的檔案儲存到指定輸出路徑下的分類子目錄。
        *   `_test_run.py` 設計為嘗試下載少量真實數據，並調用「偵察兵」進行初步驗證。加入了對沙箱環境網路限制的考量，在無法穩定下載時能適當跳過部分驗證。
    *   **`taifex_data_pipeline` (精煉廠) v1.0 (批次)**：
        *   基於 `高適應性期交所數據整合管道 v8.0` Colab 腳本改編。
        *   實現參數化入口，接收輸入目錄和資料庫輸出目錄。
        *   執行 ETL 作業：掃描輸入目錄中的檔案（含解壓縮 ZIP），動態判斷檔案格式與解析配方，執行數據清洗與轉換，最終將高品質結構化數據載入 DuckDB 資料庫。
        *   支持多種 CSV 格式（包括有表頭、無表頭、不同分隔符的舊格式）。
        *   `format_map.json` 用於記錄和複用檔案內容雜湊與解析配方的對應關係。
        *   `_test_run.py` 包含使用範例數據壓縮檔進行端到端 ETL 流程驗證，檢查最終資料庫中的記錄數和抽樣數據。
    *   **SOP 合規性**：
        *   所有應用均實現「原子化腳本執行」，可由單一 `python` 指令完成操作。
        *   每個應用均提供 `run.py` 作為標準執行入口。
        *   所有 `run.py` 與 `_test_run.py` 均內建「路徑自我校正」樣板碼。

### **Hotfix (對應 v14.2-BE-Task-Fix-01)**

*   **目標**：修正 `taifex_data_pipeline` 在處理真實期貨成交紀錄 (`Daily_*.zip`) 時的解析錯誤。
*   **核心修正**：
    *   **`taifex_data_pipeline/run.py`**：
        *   更新 `pipeline_tick_data` 函式中的欄位對應邏輯 (`rename_map`)，確保能正確處理真實數據中成交量欄位名稱為 `成交數量(B+S)` 的情況，將其對應到內部 `volume` 欄位。
        *   移除了 `SimpleLogger` 中不被支援的 `exc_info=True` 參數（在 `logger.error()` 調用處），確保日誌記錄的穩定性。

### **架構演進：管線化混合並行 (v18.0)**

#### **階段一：重構 I/O 層 - `apps/taifex_data_downloader` 升級為非同步下載器**

*   **目標**：將下載器升級為非同步應用，以提高 I/O 效率。
*   **核心變更 (`apps/taifex_data_downloader/run.py`)**：
    *   **引入 `asyncio` 和 `aiohttp`**：核心下載邏輯重構為非同步。
    *   實現 `download_single_file_async` 和 `download_data_async` 函式。
    *   **本地優先寫入**：下載的檔案數據流直接寫入 Colab 本地的暫存目錄 (由 `--output-path` 指定)。
    *   保持 `Content-Type` 檢查以避免下載無效的 HTML 錯誤頁面。
    *   `main()` 函式調整為可獨立執行非同步下載流程。
    *   `download_data_async` 設計為可選接收一個任務佇列 (`task_queue`)，成功下載的檔案本地路徑可被放入此佇列，為階段三的協調器做準備。
    *   **`_test_run.py` 更新**：
        *   驗證非同步下載的正確性和本地寫入能力。
        *   能夠解析 `run.py` 的 `stdout` 日誌，以判斷下載狀態，並在外部資源不可靠（如返回 HTML）時正確跳過測試。

#### **階段二：重構 CPU 層 - `apps/taifex_data_pipeline` 升級為佇列驅動**

*   **目標**：將數據精煉廠從檔案系統掃描模式升級為從任務佇列接收工作的數據處理引擎。
*   **核心變更 (`apps/taifex_data_pipeline/run.py`)**：
    *   **佇列驅動處理**：
        *   移除了原有的檔案系統掃描 (`discover_files_recursively`) 和批次解析 (`run_parsing_stage`) 邏輯。
        *   新增 `process_single_file_entry` 函式，負責處理從佇列接收到的單個檔案條目。此函式包含：
            *   處理 ZIP 檔案（解壓縮並處理內部成員）。
            *   讀取檔案內容，獲取/更新 `format_map` 中的解析配方。
            *   解析數據，應用清洗管線。
            *   將清洗後的 DataFrame **直接寫入**到一個共享的本地 DuckDB 資料庫實例中（包含去重邏輯）。
        *   新增 `run_pipeline_from_queue` 主執行函式，負責：
            *   初始化 DuckDB 連接及資料庫結構（表格、序列、索引）。
            *   從傳入的任務佇列中循環獲取檔案路徑（或包含路徑的字典），直到收到哨兵值。
            *   調用 `process_single_file_entry` 處理每個檔案。
            *   在所有任務完成後，儲存 `format_map.json` 並關閉 DuckDB 連接。
    *   **接口變更**：
        *   `main()` 函式調整為主要用於獨立測試（模擬佇列填充並調用 `run_pipeline_from_queue`）。
        *   移除了 `--input-dir` 命令列參數。`--db-output-dir` 用於指定本地 DuckDB 和 `format_map.json` 的輸出目錄。
    *   **`_test_run.py` 更新**：
        *   不再使用 `subprocess` 執行 `run.py`。
        *   直接導入並調用 `run_pipeline_from_queue` 進行測試。
        *   在測試中模擬 `queue.Queue`，填入範例檔案路徑和哨兵值。
        *   驗證最終在本地生成的 DuckDB 內容和 `format_map.json` 的正確性。

#### **階段三：建立 v18.0 主協調器 (Orchestrator) - 待實現**
*   此階段將在 Colab 筆記本中實現，負責創建共享任務佇列 (`multiprocessing.Manager().Queue`)，並使用 `ProcessPoolExecutor` 分別啟動和管理「非同步下載器進程池」（I/O 層）和「佇列驅動管線處理進程池」（CPU 層）。
*   協調器將實現背壓機制、監控、信號與關閉邏輯，並在所有處理完成後將最終的本地 DuckDB 檔案同步回 Google Drive。

## 如何執行 (v18.0 階段二及以前)

### 單獨執行應用程式

**1. 數據偵察兵 (`taifex_data_prospector`)**
```bash
python apps/taifex_data_prospector/run.py --file-path /path/to/your/file_or_zip
```

**2. 數據採集官 (`taifex_data_downloader`)**
```bash
python apps/taifex_data_downloader/run.py \
    --start-date YYYY-MM-DD \
    --end-date YYYY-MM-DD \
    --output-path /path/to/local_download_output_temp_dir \
    --futures-trades \
    --options-summary
    # ... (以及其他數據類型開關)
```
*注意：此採集官已升級為非同步下載至本地。*

**3. 數據精煉廠 (`taifex_data_pipeline`)**
*在 v18.0 階段二之後，`run.py` 的主要執行方式是通過 `run_pipeline_from_queue` 函式，由協調器（或測試腳本）調用。獨立的命令列執行主要用於測試。*
```bash
# 獨立測試模式 (會處理 --test-file-path 指定的檔案)
python apps/taifex_data_pipeline/run.py \
    --db-output-dir /path/to/local_db_and_format_map_dir \
    --db-name my_taifex_data.duckdb \
    --processing-temp-dir /path/to/local_processing_temp \
    --test-file-path /path/to/sample1.zip,/path/to/sample2.csv
    # (如果 --test-file-path 未提供，則僅初始化並等待空佇列)
```

### 執行測試
```bash
python apps/taifex_data_prospector/_test_run.py
python apps/taifex_data_downloader/_test_run.py
python apps/taifex_data_pipeline/_test_run.py
```

---
*此 README 最後更新對應開發里程碑：v18.0 階段二完成。*
