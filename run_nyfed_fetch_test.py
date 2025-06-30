# 檔案: run_nyfed_fetch_test.py
# 目的: 遵循 Cell 6 的邏輯，測試從紐約聯儲網站抓取、合併並清洗多個歷史一級交易商持倉數據的 Excel 檔案。

import os
import sys
import logging
import pandas as pd
import numpy as np
from datetime import datetime
import requests
import io

# --- 路徑自我校正 ---
try:
    project_root = os.path.dirname(os.path.abspath(__file__))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from src.utils.logger import setup_logger
    print("[Setup] NY Fed fetch test modules imported successfully.")
except ImportError as e:
    print(f"[FATAL] Failed to import modules: {e}")
    sys.exit(1)

# --- 從 PROJECT_CONFIG 獲取的配置 (模擬) ---
# 這些是根據使用者提供的 Cell 1 程式碼片段硬編碼的
SBP2013_COLS_TO_SUM = ['PDPUSGCS3LNOP', 'PDPUSGCS36NOP', 'PDPUSGCS611NOP', 'PDPUSGCSM11NOP']
SBP2001_COLS_TO_SUM = ['PDPUSGCS5LNOP', 'PDPUSGCS5MNOP']
NY_FED_URLS = [
    "https://markets.newyorkfed.org/api/pd/get/SBN2024/timeseries/"
    "PDPOSGSC-L2_PDPOSGSC-G2L3_PDPOSGSC-G3L6_PDPOSGSC-G6L7_"
    "PDPOSGSC-G7L11_PDPOSGSC-G11L21_PDPOSGSC-G21.xlsx",
    "https://markets.newyorkfed.org/api/pd/get/SBN2022/timeseries/"
    "PDPOSGSC-L2_PDPOSGSC-G2L3_PDPOSGSC-G3L6_PDPOSGSC-G6L7_"
    "PDPOSGSC-G7L11_PDPOSGSC-G11L21_PDPOSGSC-G21.xlsx",
    "https://markets.newyorkfed.org/api/pd/get/SBN2015/timeseries/"
    "PDPOSGSC-L2_PDPOSGSC-G2L3_PDPOSGSC-G3L6_PDPOSGSC-G6L7_"
    "PDPOSGSC-G7L11_PDPOSGSC-G11.xlsx",
    "https://markets.newyorkfed.org/api/pd/get/SBN2013/timeseries/"
    "PDPOSGSC-L2_PDPOSGSC-G2L3_PDPOSGSC-G3L6_PDPOSGSC-G6L7_"
    "PDPOSGSC-G7L11_PDPOSGSC-G11.xlsx",
    "https://markets.newyorkfed.org/api/pd/get/SBP2013/timeseries/"
    "PDPUSGCS3LNOP_PDPUSGCS36NOP_PDPUSGCS611NOP_PDPUSGCSM11NOP.xlsx",
    "https://markets.newyorkfed.org/api/pd/get/SBP2001/timeseries/"
    "PDPUSGCS5LNOP_PDPUSGCS5MNOP.xlsx"
]

def run_nyfed_test(logger_obj):
    """執行紐約聯儲數據抓取、解析和加總測試"""
    logger_obj.info("="*50)
    logger_obj.info("Starting NY Fed Primary Dealer Data Fetch Test (Cell 6 Logic)")
    logger_obj.info("="*50)
    logger_obj.warning("*** 警告：此步驟將合併定義可能不同的 SBN(Gross?) 和 SBP(Net?) 數據！解釋需謹慎！ ***")

    all_positions_data_series = [] # 儲存從各個文件讀取的 Series

    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    })

    processed_files_count = 0
    failed_files_details = []
    first_file_saved_for_debug = False # 新增偵錯標記

    for i, url in enumerate(NY_FED_URLS):
        file_source_name = url.split('/')[-3] if len(url.split('/')) > 2 else f"File_{i+1}"
        logger_obj.info(f"Processing file {i+1}/{len(NY_FED_URLS)}: {url} ({file_source_name})")

        try:
            # --- 1a. 下載 Excel 文件 ---
            logger_obj.debug(f"Downloading {url}...")
            response_excel = session.get(url, timeout=120)
            response_excel.raise_for_status()
            excel_content_bytes = response_excel.content # 獲取原始 bytes
            excel_content = io.BytesIO(excel_content_bytes) # 用於 pandas 讀取
            logger_obj.debug(f"Download successful for {url}.")

            # --- 偵錯：保存第一個下載的 Excel 檔案 ---
            if not first_file_saved_for_debug:
                debug_dir = os.path.join(project_root, 'data_workspace', 'debug')
                os.makedirs(debug_dir, exist_ok=True)
                debug_file_path = os.path.join(debug_dir, "temp_excel_file.xlsx")
                with open(debug_file_path, 'wb') as f:
                    f.write(excel_content_bytes)
                logger_obj.info(f"DEBUG: Saved first Excel file to {debug_file_path}")
                first_file_saved_for_debug = True # 確保只保存一次
                logger_obj.info("DEBUG: Intentionally stopping after saving the first file for inspection.")
                return 2 # 返回特殊代碼表示偵錯停止

            # --- 1b. 解析 Excel (自動檢測表頭和關鍵列) ---
            logger_obj.debug(f"Attempting to parse Excel (auto-detect header) for {url}...")
            header_row_idx = None
            date_col_name = None
            data_df_long = None # 這將是包含 'date', 'Time Series', 'Value' 的長格式 DataFrame

            possible_headers = [3, 4, 0] # Cell 6 優先嘗試的表頭行號
            for h_try in possible_headers:
                try:
                    excel_content.seek(0) # 每次嘗試前重置 BytesIO 指針
                    df_peek = pd.read_excel(excel_content, header=h_try, nrows=5, engine='openpyxl')

                    cols_lower = [str(c).lower() for c in df_peek.columns]

                    # 查找日期列
                    temp_date_col = None
                    if 'effective date' in cols_lower:
                        temp_date_col = df_peek.columns[cols_lower.index('effective date')]
                    elif len(df_peek.columns) > 0: # 若無 'effective date'，嘗試第一列
                        temp_date_col = df_peek.columns[0]

                    # 查找 Time Series 列
                    temp_ts_col = None
                    if 'time series' in cols_lower:
                        temp_ts_col = df_peek.columns[cols_lower.index('time series')]

                    # 查找 Value 列
                    temp_val_col = None
                    if 'value (millions)' in cols_lower:
                        temp_val_col = df_peek.columns[cols_lower.index('value (millions)')]
                    elif 'value' in cols_lower: # 後備
                        temp_val_col = df_peek.columns[cols_lower.index('value')]

                    if temp_date_col is not None and temp_ts_col is not None and temp_val_col is not None:
                        header_row_idx = h_try
                        # 讀取完整文件
                        excel_content.seek(0)
                        data_df_full = pd.read_excel(excel_content, header=header_row_idx, engine='openpyxl')

                        # 重命名選中的列為標準名稱
                        data_df_full.rename(columns={
                            temp_date_col: 'date',
                            temp_ts_col: 'Time Series',
                            temp_val_col: 'Value'
                        }, inplace=True)

                        # 選取必要的列
                        data_df_long = data_df_full[['date', 'Time Series', 'Value']].copy()
                        logger_obj.info(f"Successfully parsed {url} with header at row {header_row_idx+1}. Date: '{temp_date_col}', TS: '{temp_ts_col}', Val: '{temp_val_col}'.")
                        break # 找到有效的表頭和列，跳出循環
                except Exception as peek_err:
                    logger_obj.debug(f"Attempting header={h_try} for {url} failed: {peek_err}")
                    continue

            if data_df_long is None:
                logger_obj.warning(f"Could not auto-detect valid header or required columns for {url}. Skipping.")
                failed_files_details.append({'url': url, 'reason': 'Header/Column detection failed'})
                continue

            # --- 1c. 清理長格式數據 ---
            logger_obj.debug(f"Cleaning long format data for {url}...")
            data_df_long['date'] = pd.to_datetime(data_df_long['date'], errors='coerce')
            data_df_long.dropna(subset=['date', 'Time Series', 'Value'], inplace=True)
            data_df_long['Value'] = pd.to_numeric(data_df_long['Value'], errors='coerce')
            data_df_long.dropna(subset=['Value'], inplace=True)

            if data_df_long.empty:
                logger_obj.warning(f"No valid data after cleaning for {url}. Skipping.")
                failed_files_details.append({'url': url, 'reason': 'Empty after cleaning'})
                continue

            # --- 1d. 轉換為寬格式 (Pivot) ---
            logger_obj.debug(f"Pivoting data to wide format for {url}...")
            try:
                # 處理可能的重複項 (同一天同一序列可能有多行) - Cell 6 的邏輯
                data_df_long = data_df_long.groupby(['date', 'Time Series'])['Value'].mean().reset_index()

                data_df_wide = pd.pivot_table(
                    data_df_long,
                    index='date',
                    columns='Time Series',
                    values='Value',
                    aggfunc='mean' # Cell 6 用 mean，理論上已處理重複
                )
            except Exception as e_pivot:
                logger_obj.error(f"Failed to pivot data for {url}: {e_pivot}", exc_info=True)
                failed_files_details.append({'url': url, 'reason': f'Pivot failed: {e_pivot}'})
                continue

            if data_df_wide.empty:
                logger_obj.warning(f"Data is empty after pivot for {url}. Skipping.")
                failed_files_details.append({'url': url, 'reason': 'Empty after pivot'})
                continue

            # --- 1e. 加總持有量 (根據 Cell 6 規則) ---
            target_cols_to_sum_names = []
            source_type = "Unknown"

            if 'SBN' in url.upper(): # SBN 類型
                source_type = "SBN"
                target_cols_to_sum_names = [c for c in data_df_wide.columns if isinstance(c, str) and c.startswith('PDPOSGSC-')]
            elif 'SBP2013' in url.upper(): # SBP2013 類型
                source_type = "SBP2013"
                target_cols_to_sum_names = SBP2013_COLS_TO_SUM
            elif 'SBP2001' in url.upper(): # SBP2001 類型
                source_type = "SBP2001"
                target_cols_to_sum_names = SBP2001_COLS_TO_SUM

            if not target_cols_to_sum_names:
                logger_obj.warning(f"No summation rule defined for {url} (type: {source_type}). Skipping summation for this file.")
                # 考慮是否要將 data_df_wide 直接加入，或完全跳過
                # Cell 6 的邏輯是如果沒有加總規則，這個檔案的數據就不會被加入最終的 series
                failed_files_details.append({'url': url, 'reason': f'No summation rule for type {source_type}'})
                continue

            actual_cols_present_for_sum = [c for c in target_cols_to_sum_names if c in data_df_wide.columns]

            if not actual_cols_present_for_sum:
                logger_obj.warning(f"None of the target columns for summation were found in the pivoted data for {url} (type: {source_type}). Target: {target_cols_to_sum_names}. Available: {list(data_df_wide.columns)}. Skipping summation.")
                failed_files_details.append({'url': url, 'reason': f'Target summation columns not found for type {source_type}'})
                continue

            if len(actual_cols_present_for_sum) < len(target_cols_to_sum_names):
                missing_cols = set(target_cols_to_sum_names) - set(actual_cols_present_for_sum)
                logger_obj.warning(f"Some target summation columns missing for {url} (type: {source_type}): {missing_cols}")

            logger_obj.info(f"Summing {len(actual_cols_present_for_sum)} columns for {url} (type: {source_type}). Columns: {actual_cols_present_for_sum}")

            # 確保參與加總的列是數值類型 (雖然 pivot 應該已經處理了)
            for col in actual_cols_present_for_sum:
                data_df_wide[col] = pd.to_numeric(data_df_wide[col], errors='coerce')

            daily_total_series = data_df_wide[actual_cols_present_for_sum].sum(axis=1, skipna=True)

            # 清理：移除 NaN 和 0 值 (Cell 6 邏輯)
            daily_total_series.dropna(inplace=True)
            daily_total_series = daily_total_series[daily_total_series != 0]

            if not daily_total_series.empty:
                all_positions_data_series.append(daily_total_series)
                logger_obj.info(f"Successfully processed and summed data for {url}. Got {len(daily_total_series)} valid data points.")
                processed_files_count += 1
            else:
                logger_obj.warning(f"Summation resulted in an empty or all-zero series for {url}. Not adding to final result.")
                failed_files_details.append({'url': url, 'reason': 'Empty/zero series after summation'})

        except requests.exceptions.RequestException as e_req:
            logger_obj.error(f"Download failed for {url}: {e_req}", exc_info=True)
            failed_files_details.append({'url': url, 'reason': f'Download failed: {e_req}'})
        except Exception as e_file:
            logger_obj.error(f"An unexpected error occurred while processing file {url}: {e_file}", exc_info=True)
            failed_files_details.append({'url': url, 'reason': f'Unexpected error: {e_file}'})

    logger_obj.info(f"File processing loop completed. Successfully processed {processed_files_count}/{len(NY_FED_URLS)} files.")
    if failed_files_details:
        logger_obj.warning(f"Details of failed files ({len(failed_files_details)}):")
        for item in failed_files_details:
            logger_obj.warning(f"  - URL: {item['url']}, Reason: {item['reason']}")


    # --- 2. 合併所有文件的持有量數據 ---
    if not all_positions_data_series:
        logger_obj.error("No data was successfully processed from any NY Fed URL. Final series will be empty.")
        # 創建一個空的 Series 返回，以便下游知道格式但沒有數據
        final_positions_series = pd.Series(dtype='float64', name='Total_NYFed_Positions_Millions')
    else:
        logger_obj.info(f"Concatenating data from {len(all_positions_data_series)} successfully processed files...")
        combined_positions = pd.concat(all_positions_data_series)

        logger_obj.info("Sorting and handling duplicates by taking the last entry for each date...")
        combined_positions.sort_index(inplace=True)
        final_positions_series = combined_positions.groupby(combined_positions.index).last() # Cell 6: groupby(level=0).last()
        final_positions_series.name = 'Total_NYFed_Positions_Millions'

        # 再次確保沒有 NaN 或 0 (Cell 6)
        final_positions_series.dropna(inplace=True)
        final_positions_series = final_positions_series[final_positions_series != 0]

        if final_positions_series.empty:
            logger_obj.warning("Final combined series is empty after all processing steps.")
        else:
            logger_obj.info(f"Final NY Fed positions series created with {len(final_positions_series)} data points, from {final_positions_series.index.min().date()} to {final_positions_series.index.max().date()}.")

    # --- 3. 將最終結果儲存 ---
    output_dir = os.path.join(project_root, 'data_workspace', 'raw_data')
    os.makedirs(output_dir, exist_ok=True)
    file_path = os.path.join(output_dir, "NY_FED_Primary_Dealer_Positions_Cell6_Logic.csv")

    try:
        final_positions_series.to_csv(file_path, header=True) # 保存 Series 時 header=True 會將 Series name 作為欄位名
        logger_obj.info(f"SUCCESS: Consolidated NY Fed data (Cell 6 logic) saved to {file_path}")
    except Exception as e_save:
        logger_obj.error(f"Failed to save the final consolidated data to {file_path}: {e_save}", exc_info=True)
        return 1 # 表示儲存失敗

    logger_obj.info("NY Fed Primary Dealer Data Fetch Test (Cell 6 Logic) Completed.")

    if processed_files_count == 0 and len(NY_FED_URLS) > 0:
        return 1 # 表示沒有文件成功處理
    if not final_positions_series.empty and processed_files_count > 0:
        return 0 # 成功
    else:
        return 1 # 部分成功或無數據


if __name__ == "__main__":
    log_file_path = os.path.join(project_root, "data_workspace", "logs", f"nyfed_fetch_cell6_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    main_logger = setup_logger("NYFedFetchCell6Test", log_file_path_str=log_file_path)

    exit_code = run_nyfed_test(main_logger)

    if exit_code == 0:
        print(f"\n[SUCCESS] NY Fed fetch test (Cell 6 logic) finished. Please check '{os.path.join('data_workspace', 'raw_data', 'NY_FED_Primary_Dealer_Positions_Cell6_Logic.csv')}' and logs.")
    elif exit_code == 2: # 特殊偵錯返回碼
        print(f"\n[DEBUG] NY Fed fetch test stopped after saving the first Excel file for inspection.")
        print(f"Please check '{os.path.join('data_workspace', 'debug', 'temp_excel_file.xlsx')}' and logs: {log_file_path}")
    else: # Handles exit_code == 1 or any other non-zero/non-2 code
        print(f"\n[WARNING/FAILURE] NY Fed fetch test (Cell 6 logic) finished with issues. Check logs: {log_file_path}")
    sys.exit(exit_code)
