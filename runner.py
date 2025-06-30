# runner.py - 彈性並行驗收平台
# 版本: 2.0 (根據行動方案 v2.0 進行修改)
# 開發者: Jules (AI 軟體工程師)
# 最後修改日期: {datetime.datetime.now().strftime('%Y-%m-%d')}
#
# 功能描述:
# 此腳本作為一個智慧驗收調度中心，旨在實現以下功能：
# 1. 自動掃描 'apps/' 目錄，發現所有符合「應用容器」標準的應用程式。
#    「應用容器」標準：'apps/' 目錄下的第一層子目錄，且該目錄必須直接包含一個 'app.py' 檔案。
# 2. 對於每個發現的應用程式，使用 'python -m apps.app_name.app' 的方式執行其 'app.py'。
# 3. 實現「彈性重試與跳過」機制：
#    - 首次執行應用程式。
#    - 若失敗，則自動重試一次。
#    - 若重試仍失敗，則記錄詳細錯誤資訊至主控台，並將該應用標記為 SKIPPED，然後繼續測試下一個應用。
# 4. 所有輸出、日誌和註解均使用繁體中文。

import os
import subprocess
import time # 用於時間戳記和可能的延遲
import datetime
import logging
# import json # 已停用，不再從 JSON 檔案讀取任務
# import shutil # 已停用，不再移動任務檔案

# --- 組態設定 (Configuration) ---
APPS_DIR = "apps"  # 應用程式所在的根目錄
LOG_DIR = "logs"   # 日誌檔案存放目錄
PYTHON_EXECUTABLE = "python"  # 或者指定 python3 的完整路徑 (例如 /usr/bin/python3)

# --- 設定日誌 (Setup Logging) ---
# 確保日誌目錄存在
os.makedirs(LOG_DIR, exist_ok=True)
# 使用繁體中文命名日誌檔案，並包含時間戳記
# 將日誌檔名完全固定，以排除動態檔名導致沙箱回滾的可能性
log_filename = os.path.join(LOG_DIR, "jules_驗收平台固定日誌.log")


# 設定日誌記錄器
logging.basicConfig(
    level=logging.INFO,  # 可調整為 logging.DEBUG 以獲取更詳細的內部流程資訊
    format="%(asctime)s [%(levelname)s] %(message)s", # 日誌格式
    handlers=[
        logging.FileHandler(log_filename, encoding='utf-8'), # 寫入日誌檔案，使用 UTF-8 編碼
        logging.StreamHandler()  # 同時輸出到控制台 (stdout)
    ]
)

def discover_apps() -> list[str]:
    """
    掃描 APPS_DIR 目錄，尋找所有符合「應用容器」標準的應用程式。

    「應用容器」標準定義：
    - 必須是 APPS_DIR 下的第一層子目錄。
    - 該子目錄內必須直接存在一個名為 'app.py' 的檔案。

    返回:
        list[str]: 一個包含所有有效應用程式名稱 (即子目錄名稱) 的列表。如果未找到，則返回空列表。
    """
    discovered_apps = []
    logging.info(f"開始在 '{APPS_DIR}/' 目錄下掃描應用程式...")
    if not os.path.isdir(APPS_DIR):
        logging.warning(f"指定的應用程式目錄 '{APPS_DIR}' 不存在或不是一個目錄。無法發現任何應用程式。")
        return discovered_apps

    for item in os.listdir(APPS_DIR):
        item_path = os.path.join(APPS_DIR, item)
        # 判斷是否為目錄 (即潛在的應用程式容器)
        if os.path.isdir(item_path):
            # 檢查此子目錄內是否直接包含 'app.py'
            app_py_path = os.path.join(item_path, "app.py")
            if os.path.isfile(app_py_path):
                logging.info(f"  [✓] 發現符合標準的應用程式：'{item}' (位於 {item_path})")
                discovered_apps.append(item)
            else:
                logging.debug(f"  [✗] 目錄 '{item}' 不包含 'app.py'，已跳過。")
        else:
            logging.debug(f"  [i] 項目 '{item}' 不是目錄，已跳過。")

    if not discovered_apps:
        logging.info(f"在 '{APPS_DIR}/' 目錄下未發現任何符合「應用容器」標準的應用程式。")
    else:
        logging.info(f"應用程式掃描完成。共發現 {len(discovered_apps)} 個應用程式。")
    return discovered_apps

def _execute_app_attempt(app_name: str, command: list[str], attempt_number: int) -> tuple[int, str, str, datetime.datetime]:
    """
    執行一次應用程式的測試嘗試，並捕獲其輸出。

    參數:
        app_name (str): 正在測試的應用程式名稱 (用於日誌記錄)。
        command (list[str]): 要執行的完整命令列表 (例如 ['python', '-m', 'apps.app_name.app'])。
        attempt_number (int): 當前是第幾次嘗試 (例如 1 或 2)。

    返回:
        tuple[int, str, str, datetime.datetime]: 一個包含以下內容的元組：
            - int: 應用程式執行的返回碼。
            - str: 應用程式的標準輸出 (stdout)。
            - str: 應用程式的標準錯誤 (stderr)。
            - datetime.datetime: 本次嘗試開始執行的時間點。
    """
    logging.info(f"應用程式 '{app_name}'：開始第 {attempt_number} 次執行嘗試。")
    logging.debug(f"  詳細指令：{' '.join(command)}")

    execution_start_time = datetime.datetime.now()

    # 設定執行環境，確保專案根目錄在 PYTHONPATH 中，以便模組導入
    # API 金鑰等敏感資訊應由執行 runner.py 的環境提供 (例如 CI/CD 環境變數)
    env = os.environ.copy()
    env["PYTHONPATH"] = "."

    captured_stdout_lines = []
    captured_stderr_lines = []

    try:
        # 使用 subprocess.Popen 執行應用程式
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,  # 捕獲標準輸出
            stderr=subprocess.PIPE,  # 捕獲標準錯誤
            text=True,               # 以文字模式處理輸出 (自動解碼)
            encoding='utf-8',        # 明確指定 UTF-8 編碼，以處理繁體中文等字元
            env=env                  # 傳遞修改後的環境變數
        )

        # 即時讀取並記錄 stdout
        if process.stdout:
            for line in iter(process.stdout.readline, ''): #逐行讀取直到EOF
                line_stripped = line.strip()
                if line_stripped: # 避免記錄空行
                    logging.info(f"  [STDOUT - {app_name}] {line_stripped}")
                    captured_stdout_lines.append(line_stripped)
            process.stdout.close() # 關閉 stdout 流

        # 即時讀取並記錄 stderr
        if process.stderr:
            for line in iter(process.stderr.readline, ''): #逐行讀取直到EOF
                line_stripped = line.strip()
                if line_stripped: # 避免記錄空行
                    logging.error(f"  [STDERR - {app_name}] {line_stripped}") # stderr 通常表示錯誤或警告
                    captured_stderr_lines.append(line_stripped)
            process.stderr.close() # 關閉 stderr 流

        process.wait() # 等待子行程執行完成
        return_code = process.returncode # 獲取返回碼

    except FileNotFoundError:
        # 如果 PYTHON_EXECUTABLE 或目標模組路徑不正確，可能會發生此錯誤
        error_msg = f"執行指令 '{' '.join(command)}' 失敗：找不到 '{PYTHON_EXECUTABLE}' 或指定的應用程式模組。請檢查路徑和環境設定。"
        logging.error(error_msg)
        # 返回一個特殊的非零返回碼和錯誤訊息，以區別於應用程式自身的失敗
        return -99, "", error_msg, execution_start_time
    except Exception as e:
        # 捕獲其他所有在 Popen 或通訊過程中可能發生的例外
        logging.error(f"執行應用程式 '{app_name}' (第 {attempt_number} 次嘗試) 期間發生未預期的系統錯誤: {e}", exc_info=True)
        # 返回一個特殊的非零返回碼，並將例外訊息放入 stderr
        return -98, "\n".join(captured_stdout_lines), f"執行期間發生系統錯誤: {e}\n" + "\n".join(captured_stderr_lines), execution_start_time

    logging.info(f"應用程式 '{app_name}' 第 {attempt_number} 次嘗試執行完成。返回碼: {return_code}")
    return return_code, "\n".join(captured_stdout_lines), "\n".join(captured_stderr_lines), execution_start_time


def run_application_test(app_name: str):
    """
    執行指定應用程式的驗收測試，包含完整的「首次嘗試、失敗則重試、再失敗則跳過並記錄」的邏輯。

    參數:
        app_name (str): 要測試的應用程式的名稱 (即其在 'apps/' 下的子目錄名稱)。
    """
    logging.info(f"--- [開始處理應用程式：{app_name}] ---")
    module_path = f"apps.{app_name}.app" # 構建用於 'python -m' 的模組路徑

    # 準備應用程式執行時所需的參數
    # 此處使用動態日期作為範例：結束日期為當前日期，開始日期為7天前。
    # 這些參數可以根據需求進行擴展，或從外部設定檔中讀取。
    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=7)

    default_params = {
        "start-date": start_date.strftime('%Y-%m-%d'),
        "end-date": end_date.strftime('%Y-%m-%d'),
        "output-format": "test_run", # 指示應用程式以測試模式運行，避免產生實際報告檔案，從而加快測試速度
        # "config-path": "config/project_config.yaml" # 若 app.py 中有此參數的預設值，則此處可選
    }
    logging.debug(f"  為 '{app_name}' 準備的執行參數：{default_params}")

    # 構建完整的執行命令
    command = [PYTHON_EXECUTABLE, "-m", module_path]
    for param_key, param_value in default_params.items():
        command.append(f"--{param_key}") # e.g., --start-date
        command.append(str(param_value)) # e.g., 2023-01-01

    # === 首次執行嘗試 ===
    return_code_att1, stdout_att1, stderr_att1, time_att1 = _execute_app_attempt(app_name, command, 1)

    if return_code_att1 == 0:
        logging.info(f"✅ [成功] 應用程式 '{app_name}' 在首次嘗試中成功執行完畢。")
        logging.info(f"--- [應用程式處理完畢：{app_name}] ---")
        return # 成功則直接返回

    # === 如果首次失敗，則進行重試 ===
    logging.warning(f"⚠️ [首次失敗] 應用程式 '{app_name}' 首次執行失敗 (返回碼: {return_code_att1})。即將進行第二次嘗試...")

    # (可選) 在重試前可以加入短暫延遲
    # time.sleep(2)

    return_code_att2, stdout_att2, stderr_att2, time_att2 = _execute_app_attempt(app_name, command, 2)

    if return_code_att2 == 0:
        logging.info(f"✅ [重試成功] 應用程式 '{app_name}' 在第二次嘗試中成功執行完畢。")
    else:
        # 重試依然失敗，記錄詳細錯誤並跳過
        logging.error(f"🔥 [重試失敗] 應用程式 '{app_name}' 第二次嘗試依然失敗 (返回碼: {return_code_att2})。該應用將被標記為已跳過。")

        # 決定最終顯示哪個錯誤日誌：優先使用第二次嘗試的 stderr，如果為空，則用第一次的。
        # 這是因為第二次的錯誤通常更能反映最終的失敗狀態。
        final_error_log_output = stderr_att2.strip() if stderr_att2.strip() else stderr_att1.strip()

        # 按照使用者指定的格式，在主控台 (stdout) 輸出結構化的錯誤報告
        # 注意：日誌系統 (logging) 已記錄了詳細的 stdout/stderr，此處是為了滿足特定的控制台輸出格式要求。
        skipped_app_error_report = f"""
======================================================================
🔥 測試失敗 (已跳過): {app_name}
----------------------------------------------------------------------
* 首次失敗時間: {time_att1.strftime('%Y-%m-%d %H:%M:%S')}
* 重試失敗時間: {time_att2.strftime('%Y-%m-%d %H:%M:%S')}
* 觸發指令: {' '.join(command)}
* 返回碼 (重試後): {return_code_att2}
* 最後日誌輸出 (Traceback):
    ```
{final_error_log_output if final_error_log_output else "無可用的錯誤日誌輸出。"}
    ```
======================================================================
"""
        print(skipped_app_error_report) # 直接輸出到主控台
        logging.info(f"已為應用程式 '{app_name}' 產生並於主控台輸出了詳細的「已跳過」報告。")

    logging.info(f"--- [應用程式處理完畢：{app_name}] ---")


def controlled_test_run():
    """
    受控的測試執行函數，包含主要的驗收邏輯。
    此函數旨在可以從腳本直接執行或被外部調用。
    """
    logging.info("===== 智慧驗收調度中心開始運行 (版本 2.0) =====")

    applications_to_test = discover_apps() # 獲取待測試應用列表

    if not applications_to_test:
        logging.info("未發現任何符合條件的應用程式。驗收流程結束。")
        logging.info("===== 智慧驗收調度中心運行完畢 =====")
        return False # 表示沒有應用被測試

    logging.info(f"已排定將依序測試以下 {len(applications_to_test)} 個應用程式：[{', '.join(applications_to_test)}]")

    all_tests_passed_or_skipped_correctly = True
    # 依序執行每個已發現應用程式的測試流程
    for app_name in applications_to_test:
        try:
            run_application_test(app_name)
            # 在每個應用測試後可以加入一個換行日誌，使主日誌更清晰
            logging.info("-" * 70)
        except Exception as e:
            logging.error(f"處理應用程式 '{app_name}' 時發生未預期錯誤: {e}", exc_info=True)
            all_tests_passed_or_skipped_correctly = False
            # 即使單個應用處理出錯，也應繼續處理下一個，此處只是標記

    logging.info(f"===== 所有 {len(applications_to_test)} 個已發現應用程式的驗收流程均已處理完畢 =====")
    logging.info("===== 智慧驗收調度中心運行完畢 =====")
    return all_tests_passed_or_skipped_correctly

def test_entry_point():
    """
    從外部 (例如 python -c) 調用的入口點。
    """
    try:
        controlled_test_run()
    except KeyboardInterrupt:
        logging.info("\n(test_entry_point) 驗收平台執行被使用者手動中斷 (KeyboardInterrupt)。正在關閉...")
    except Exception as e:
        logging.critical(f"(test_entry_point) 驗收平台遭遇無法處理的嚴重錯誤並意外終止：{e}", exc_info=True)
    finally:
        logging.info("(test_entry_point) 驗收平台已關閉。")

if __name__ == "__main__":
    try:
        controlled_test_run()
    except KeyboardInterrupt:
        # 處理使用者手動中斷 (Ctrl+C)
        logging.info("\n(直接執行) 驗收平台執行被使用者手動中斷 (KeyboardInterrupt)。正在關閉...")
    except Exception as e:
        # 捕獲在 controlled_test_run 自身執行期間可能發生的任何未預期頂層錯誤
        logging.critical(f"(直接執行) 驗收平台遭遇無法處理的嚴重錯誤並意外終止：{e}", exc_info=True)
    finally:
        logging.info("(直接執行) 驗收平台已關閉。")
