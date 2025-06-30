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
        '--start-date', '2022-01-01',
        '--end-date', '2023-12-31',
        '--output-format', 'test_run', # 使用不產生檔案的模式進行測試
        '--config-path', config_file_path # 使用絕對路徑確保找到設定檔
        # '--no-charts' # 根據需要添加，test_run 模式下圖表通常不重要
        # '--no-text'   # test_run 模式下文字分析邏輯應執行
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
            # 額外檢查 STDOUT 是否包含成功訊息 (可選，但推薦)
            # 例如，app.py 在 test_run 成功時應打印特定訊息
            # 注意: 這裡的成功訊息應與 app.py 中定義的完全一致
            expected_success_msg = "應用程式容器 'test_run' 模式執行成功！" # 從 app.py 複製
            if expected_success_msg in result.stdout:
                print("\n✅ [SOP v3.0] 應用程式容器化測試成功！")
                print("   證明此 App 已具備標準接口，並可在任何環境下被正確調用。")
                return True
            else:
                print("\n❌ [SOP v3.0] 測試失敗！應用程式返回碼為 0，但未檢測到預期的成功訊息。")
                print(f"   應包含 \"{expected_success_msg}\"")
                return False
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

    # 根據測試結果設定退出碼，方便 CI/CD 工具判斷
    if test_successful:
        sys.exit(0) # 成功
    else:
        sys.exit(1) # 失敗
