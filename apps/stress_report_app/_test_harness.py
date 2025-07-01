# -*- coding: utf-8 -*-
# 應用程式容器化測試探針 (_test_harness.py) - SOP v3.0
# 版本: 1.0
# 日期: 2025-06-30

import os
import sys
import subprocess

# ==============================================================================
# 標準路徑自我校正 (Path Self-Correction)
# 確保無論從何處執行此腳本，都能將專案根目錄加入系統路徑。
# ==============================================================================
def add_project_root_to_sys_path():
    """
    (標準樣板) 尋找專案根目錄 (包含 .git 的目錄) 並將其加入 sys.path 的最前面。
    """
    current_file_path = os.path.abspath(__file__) # /app/apps/stress_report_app/_test_harness.py

    # 預期專案根目錄是包含 apps/ 和 config/ 的目錄
    # _test_harness.py 位於 apps/stress_report_app/
    # 所以專案根目錄是 current_file_path 的上三層目錄

    project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_file_path))) # /app

    # 檢查是否真的找到了期望的專案根目錄結構
    is_expected_root = os.path.isdir(os.path.join(project_root, "apps")) and \
                       os.path.isdir(os.path.join(project_root, "config"))

    if not is_expected_root:
        # 如果基於固定層級的假設不成立，再嘗試 .git 查找法
        git_root_found = False
        current_dir_for_git = os.path.dirname(current_file_path)
        while current_dir_for_git != os.path.dirname(current_dir_for_git):
            if ".git" in os.listdir(current_dir_for_git):
                project_root = current_dir_for_git
                git_root_found = True
                break
            current_dir_for_git = os.path.dirname(current_dir_for_git)

        if git_root_found:
            print(f"✅ [路徑校正] 透過 .git 找到專案根目錄: '{project_root}'")
        else:
            print(f"⚠️ [路徑校正] 未找到 .git，且基於固定層級的專案根目錄 '{project_root}' 結構不符合預期。")
            print(f"   將繼續使用 '{project_root}' 作為假定的專案根目錄。")
            print(f"   請檢查 _test_harness.py 的位置是否為 apps/your_app_name/_test_harness.py")

    if project_root not in sys.path:
        sys.path.insert(0, project_root)
        print(f"✅ [路徑校正] 已將專案根目錄 '{project_root}' 加入 sys.path。")
    else:
        print(f"ℹ️ [路徑校正] 專案根目錄 '{project_root}' 已在 sys.path 中。")

    return project_root

# 在所有導入之前執行路徑校正
PROJECT_ROOT = add_project_root_to_sys_path()
# ==============================================================================

def test_app_container_execution():
    """
    測試應用程式容器 (app.py) 的標準化執行流程。
    """
    print(f"\n===== 🧪 [SOP v3.0] 開始執行應用程式容器化測試探針 =====")

    # 1. 路徑自我校正已在上方完成。PROJECT_ROOT 變數可供使用（如果需要）。

    # 2. 定位到我們要測試的標準入口 app.py
    # 假設 _test_harness.py 和 app.py 在同一個目錄下 (apps/stress_report_app/)
    app_script_dir = os.path.dirname(os.path.abspath(__file__))
    app_path = os.path.join(app_script_dir, 'app.py')

    if not os.path.exists(app_path):
        print(f"❌ 錯誤：找不到目標應用程式容器腳本 '{app_path}'。測試中止。")
        # 嘗試打印 sys.path 以幫助調試路徑問題
        # print("當前 sys.path:")
        # for p in sys.path: print(f"  - {p}")
        return False # 指示測試失敗

    print(f"  [*] 測試目標: {app_path}")

    # 3. 準備命令列參數 (根據 SOP v3.0 指示)
    # 使用 PROJECT_ROOT 來構建 config_path 的絕對路徑，以增加穩健性
    config_file_path = os.path.join(PROJECT_ROOT, 'config/project_config.yaml')
    if not os.path.exists(config_file_path):
        print(f"❌ 錯誤：找不到設定檔 '{config_file_path}'。測試中止。")
        return False

    cmd = [
        sys.executable,        # 使用當前 Python 解譯器
        app_path,              # 要執行的 app.py
        '--start-date', '2022-01-01', # 使用較長的日期範圍
        '--end-date', '2023-03-31',   # 使用較長的日期範圍
        '--output-format', 'html',    # 修改為生成 HTML 報告
        '--config-path', config_file_path # 使用絕對路徑確保找到設定檔
        # '--no-charts' # 根據需要添加
        # '--no-text'   # 根據需要添加
    ]
    print(f"  [*] 執行命令: {' '.join(cmd)}")

    # 4. 使用 subprocess 執行，完全模擬外部調度
    #    確保 API 金鑰等環境變數已在執行此測試探針的環境中設定。
    print(f"  [*] 提示：請確保 FRED API 金鑰已在環境變數 'API_KEY_FRED' 中設定。")
    if not os.getenv('API_KEY_FRED'):
        print(f"  ⚠️ 警告：環境變數 'API_KEY_FRED' 未設定，測試可能因數據獲取失敗而失敗。")

    # 準備環境變數給子程序，特別是 PYTHONPATH
    sub_env = os.environ.copy()
    # 將 PROJECT_ROOT 加入 PYTHONPATH，確保子程序能找到 apps 和 src 包
    # 如果 PYTHONPATH 已存在，則附加；否則創建新的
    if 'PYTHONPATH' in sub_env:
        sub_env['PYTHONPATH'] = f"{PROJECT_ROOT}{os.pathsep}{sub_env['PYTHONPATH']}"
    else:
        sub_env['PYTHONPATH'] = PROJECT_ROOT

    # 為測試設定虛擬 API Key，以使程式能繼續執行數據處理流程
    # 注意：這僅用於本地測試流程，實際 API 調用會失敗或返回空數據
    if 'API_KEY_FRED' not in sub_env:
        sub_env['API_KEY_FRED'] = 'TEST_ONLY_NO_REAL_CALLS'
        print(f"  ⚠️ 警告：為測試目的，已設定虛擬 API_KEY_FRED='TEST_ONLY_NO_REAL_CALLS'")

    print(f"  [*] 子程序 PYTHONPATH 將設定為: {sub_env['PYTHONPATH']}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True, # 捕獲 stdout 和 stderr
            text=True,           # 以文字模式處理輸出 (Python 3.7+)
            check=False,         # 設定為 False，這樣即使返回非零碼也不會拋出 CalledProcessError，我們可以手動檢查
            encoding='utf-8',    # 確保正確解碼中文輸出
            env=sub_env          # 傳遞修改後的環境變數
        )

        # 5. 驗證：檢查返回碼是否為 0，並打印輸出
        print("\n--- 應用程式標準輸出 (STDOUT) ---")
        print(result.stdout if result.stdout.strip() else "<無標準輸出>")
        print("--- 應用程式標準錯誤 (STDERR) ---")
        print(result.stderr if result.stderr.strip() else "<無標準錯誤輸出>")

        if result.returncode == 0:
            # 當 output-format 為 html 時，主要驗證返回碼和之後的檔案生成
            # 可以檢查 stdout 是否包含報告生成成功的訊息（如果 app.py 中有定義）
            # 例如: expected_success_msg = "報告已成功生成於"
            # if expected_success_msg in result.stdout:
            print("\n✅ [SOP v3.0] 應用程式容器化測試執行成功 (返回碼 0)。")
            print("   下一步將檢查報告檔案是否生成。")
            return True
            # else:
            #     print("\n⚠️ [SOP v3.0] 應用程式返回碼為 0，但未檢測到預期的報告生成成功訊息。")
            #     print(f"   請檢查 STDOUT 確認報告路徑。")
            #     return True # 仍然返回 True，因為返回碼是0，檔案檢查是下一步
        else:
            print(f"\n❌ [SOP v3.0] 測試失敗！應用程式容器執行失敗！")
            print(f"   返回碼: {result.returncode}")
            # 這裡的 stdout 和 stderr 已經打印過了
            return False

    except FileNotFoundError:
        # 這通常意味著 sys.executable 或 app_path 有問題
        print(f"❌ 錯誤：找不到 Python 解譯器 '{sys.executable}' 或應用程式腳本 '{app_path}'。")
        return False
    except Exception as e:
        # 捕獲 subprocess.run 可能的其他例外
        print(f"❌ 測試探針執行期間發生未預期錯誤: {e}")
        return False

if __name__ == "__main__":
    # 執行測試函式
    test_successful = test_app_container_execution()

# (所有 import 語句在檔案頂部)
# ... (PROJECT_ROOT 設定代碼) ...
# ... (test_app_container_execution 函數定義) ...

def test_core_modules():
    """
    獨立測試 data_fetcher.py 和 calculator.py 的核心功能。
    """
    # 這些 import 對 test_core_modules 是局部的，或者可以移到檔案頂部如果它們也被其他地方使用
    from datetime import datetime
    import yaml
    import pandas as pd
    from apps.stress_report_app.schemas import AppConfig # 假設 AppConfig 已被正確導入
    from apps.stress_report_app import data_fetcher, calculator # 假設這些模組已可導入
    from src.utils.logger import setup_logger # 假設 setup_logger 已可導入

    print(f"\n===== 🧪 開始執行核心模組測試 (data_fetcher & calculator) =====")
    logger = setup_logger("CoreModulesTest", level="INFO")

    # --- 準備參數 ---
    start_date_str = "2022-01-01" # 修改開始日期以獲取更多數據點
    end_date_str = "2023-03-31"   # 範例日期
    try:
        start_date_dt = datetime.strptime(start_date_str, '%Y-%m-%d')
        end_date_dt = datetime.strptime(end_date_str, '%Y-%m-%d')
        logger.info(f"測試日期範圍: {start_date_str} 至 {end_date_str}")
    except ValueError:
        logger.error(f"錯誤：測試用的日期格式不正確 ({start_date_str}, {end_date_str})。請使用 YYYY-MM-DD。測試中止。")
        return False

    config_path = os.path.join(PROJECT_ROOT, 'config/project_config.yaml')
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            raw_config = yaml.safe_load(f)
        app_config = AppConfig(**raw_config)
        logger.info(f"設定檔 '{config_path}' 載入並驗證成功。")
    except Exception as e:
        logger.error(f"錯誤：載入或驗證設定檔 '{config_path}' 失敗: {e}。測試中止。", exc_info=True)
        return False

    fred_api_key = os.getenv('API_KEY_FRED')
    if not fred_api_key:
        logger.warning("警告：環境變數 'API_KEY_FRED' 未設定。FRED 數據抓取將使用虛假金鑰，預期會失敗或返回空數據。")
        fred_api_key = "TEST_ONLY_NO_REAL_CALLS_CORE_MODULE_TEST"
    else:
        logger.info("成功讀取 'API_KEY_FRED' 環境變數。")

    # --- 測試 data_fetcher.fetch_all_data ---
    logger.info("\n--- [測試 Data Fetcher] ---")
    fetched_data = None # 初始化
    data_fetcher_success = False
    try:
        fetched_data = data_fetcher.fetch_all_data(
            start_date_dt,
            end_date_dt,
            app_config,
            fred_api_key,
            logger_instance=logger
        )
        logger.info(f"data_fetcher.fetch_all_data 執行完畢。")
        if fetched_data and hasattr(fetched_data, 'merged_df') and isinstance(fetched_data.merged_df, pd.DataFrame):
            logger.info("返回的 fetched_data 物件有效，merged_df 是 DataFrame。")
            logger.info(f"merged_df 維度: {fetched_data.merged_df.shape}")
            if not fetched_data.merged_df.empty:
                logger.info("merged_df 非空。部分欄位預覽 (前5行):")
                print(fetched_data.merged_df.head().to_string())
                non_empty_cols = fetched_data.merged_df.dropna(axis=1, how='all').columns
                if not non_empty_cols.empty:
                    logger.info(f"merged_df 中至少包含以下非空欄位: {list(non_empty_cols)}")
                else:
                    logger.warning("merged_df 中所有欄位都完全是 NaN。")
            else:
                logger.warning("merged_df 為空。")
            data_fetcher_success = True # 標記成功
        else:
            logger.error("data_fetcher.fetch_all_data 返回無效或 merged_df 不是 DataFrame。")
            # data_fetcher_success 保持 False

    except Exception as e:
        logger.error(f"執行 data_fetcher.fetch_all_data 時發生錯誤: {e}", exc_info=True)
        # data_fetcher_success 保持 False

    # --- 測試 calculator.calculate_all_indicators ---
    logger.info("\n--- [測試 Calculator] ---")
    calculator_success = False
    if not data_fetcher_success:
        logger.error("由於 data_fetcher 測試失敗或未返回有效數據，跳過 calculator 測試。")
    elif fetched_data is None or not hasattr(fetched_data, 'merged_df') or not isinstance(fetched_data.merged_df, pd.DataFrame):
        logger.error("fetched_data.merged_df 不是有效的 DataFrame (可能為 None 或類型錯誤)，無法傳遞給 calculator。")
    else:
        try:
            calculated_data = calculator.calculate_all_indicators(fetched_data, logger_instance=logger)
            logger.info(f"calculator.calculate_all_indicators 執行完畢。")
            if calculated_data and hasattr(calculated_data, 'final_df') and isinstance(calculated_data.final_df, pd.DataFrame):
                logger.info("返回的 calculated_data 物件有效，final_df 是 DataFrame。")
                logger.info(f"final_df 維度: {calculated_data.final_df.shape}")
                if not calculated_data.final_df.empty:
                    logger.info("final_df 非空。部分欄位預覽 (前5行):")
                    print(calculated_data.final_df.head().to_string())
                    expected_cols = ['Spread_10Y2Y', 'SOFR_Dev', 'Dealer_Stress_Index']
                    missing_cols = [col for col in expected_cols if col not in calculated_data.final_df.columns]
                    if not missing_cols:
                        logger.info(f"預期的計算欄位 {expected_cols} 均存在於 final_df。")
                    else:
                        logger.warning(f"部分預期計算欄位缺失: {missing_cols}。")
                else:
                    logger.warning("final_df 為空。")
                calculator_success = True # 標記成功
            else:
                logger.error("calculator.calculate_all_indicators 返回無效或 final_df 不是 DataFrame。")
                # calculator_success 保持 False

        except Exception as e:
            logger.error(f"執行 calculator.calculate_all_indicators 時發生錯誤: {e}", exc_info=True)
            # calculator_success 保持 False

    if data_fetcher_success and calculator_success:
        logger.info("\n✅ 核心模組 (data_fetcher & calculator) 測試執行完畢。請檢查日誌。")
        return True
    else:
        logger.error("\n❌ 核心模組測試失敗或部分失敗。請檢查上述日誌。")
        return False

if __name__ == "__main__":
    # 確保所有頂層需要的導入都在這裡或更早
    # from datetime import datetime # 已在 test_core_modules 內部
    # import yaml # 已在 test_core_modules 內部
    # import pandas as pd # 已在 test_core_modules 內部
    # from apps.stress_report_app.schemas import AppConfig # 已在 test_core_modules 內部
    # from apps.stress_report_app import data_fetcher, calculator # 已在 test_core_modules 內部
    # from src.utils.logger import setup_logger # 已在 test_core_modules 內部

    # 決定要執行哪個測試
    test_to_run = test_app_container_execution  # 改回執行容器化流程測試
    # test_to_run = test_core_modules

    successful = test_to_run()

    # 在 test_app_container_execution 成功後，檢查報告是否生成
    if successful and test_to_run == test_app_container_execution:
        # 假設 app.py 會將報告輸出到 data_workspace/output/reports/ stress_report_YYYY-MM-DD_HHMMSS_html/stress_report_YYYY-MM-DD_HHMMSS.html
        # 我們需要找到最新的報告目錄
        reports_base_dir = os.path.join(PROJECT_ROOT, "data_workspace", "output", "reports")
        if os.path.isdir(reports_base_dir):
            all_report_dirs = [d for d in os.listdir(reports_base_dir) if os.path.isdir(os.path.join(reports_base_dir, d)) and d.startswith("stress_report_")]
            if all_report_dirs:
                all_report_dirs.sort()
                latest_report_dir_name = all_report_dirs[-1]
                # 預期檔名與目錄名中的時間戳和格式部分一致
                # e.g., dir: stress_report_2023-10-27_103000_html -> file: stress_report_2023-10-27_103000.html
                expected_report_filename = latest_report_dir_name.replace("_html", ".html")
                report_file_path = os.path.join(reports_base_dir, latest_report_dir_name, expected_report_filename)

                if os.path.exists(report_file_path):
                    print(f"✅ 報告檔案已成功生成於: {report_file_path}")
                else:
                    print(f"❌ 報告檔案未找到於預期路徑: {report_file_path}")
                    successful = False # 標記為失敗如果報告未生成
            else:
                print(f"❌ 在 '{reports_base_dir}' 中未找到任何 'stress_report_' 開頭的報告目錄。")
                successful = False
        else:
            print(f"❌ 報告基礎目錄 '{reports_base_dir}' 不存在。")
            successful = False

    if successful:
        sys.exit(0)
    else:
        sys.exit(1)
