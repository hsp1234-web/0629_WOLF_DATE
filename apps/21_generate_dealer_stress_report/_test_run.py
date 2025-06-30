# -*- coding: utf-8 -*-
"""
標準化整合測試腳本 for 21_generate_dealer_stress_report App
"""

# --- BEGIN HERMETIC PATH CORRECTION ---
# 動態路徑校正，確保無論從何處執行此腳本，都能正確解析專案模組。
# 核心原則：將專案根目錄 (即 'apps' 資料夾的父目錄) 添加到 sys.path 的最前端。
import sys
import os

# 取得此腳本檔案的絕對路徑
# __file__ 是 Python 中一個內建變數，代表當前腳本的檔案名稱
# os.path.abspath() 將其轉換為絕對路徑，以應對相對路徑執行的情況
_current_file_path = os.path.abspath(__file__)

# 計算出 'apps/x21_generate_dealer_stress_report' 目錄的路徑
# os.path.dirname() 用於獲取路徑中的目錄部分
_test_script_dir = os.path.dirname(_current_file_path)

# 計算出 'apps' 目錄的路徑
_apps_dir = os.path.dirname(_test_script_dir)

# 計算出專案根目錄 (project_root) 的路徑，即 'apps' 的父目錄
_project_root_dir = os.path.dirname(_apps_dir)

# 將專案根目錄添加到 sys.path 的最前面 (index 0)
# 這確保了 Python 在解析 import 語句時，會優先搜尋專案根目錄下的模組。
# 如此一來，無論腳本是從專案根目錄執行、從子目錄執行，
# 或是通過其他方式 (如 CI/CD 系統) 執行，都能以一致的方式找到專案內的模組。
if _project_root_dir not in sys.path:
    sys.path.insert(0, _project_root_dir)
# --- END HERMETIC PATH CORRECTION ---

# 原有的 import 語句現在可以安全地假設專案根目錄已在 sys.path 中
# import sys # 這個 import sys 已經在上面出現，可以移除或保留，Python 會處理重複導入
# import os # 這個 import os 已經在上面出現

# 將父目錄添加到 sys.path 以便導入 run_app (這段舊的邏輯可以被上面的通用邏輯取代)
# # 假設 _test_run.py 和 run.py 在同一個目錄下，
# # run.py 中的 from .data_fetcher import ... 這樣的相對導入需要正確的套件上下文
# # 我們需要將 apps 目錄加入到 sys.path
# current_dir = os.path.dirname(os.path.abspath(__file__))
# apps_dir = os.path.dirname(current_dir) # apps/21_generate_dealer_stress_report/ -> apps/
# if apps_dir not in sys.path:
#     sys.path.insert(0, apps_dir) # 這個 insert(0, apps_dir) 也可能導致問題，應該是 project_root

# 現在可以從 apps.x21_generate_dealer_stress_report.run 導入
# 但 run.py 本身是作為腳本設計的，其導入方式是 from .data_fetcher
# 如果直接從外部呼叫 run_app，需要確保 Python 執行時能解析這些相對導入
# 最簡單的方式是確保執行 _test_run.py 時，其父目錄 (apps) 在 PYTHONPATH 或 sys.path 中
# 並且 python -m apps.x21_generate_dealer_stress_report._test_run 這樣執行
# 或者，在 _test_run.py 中模擬這種環境

# 現在，由於專案根目錄已經在 sys.path 中，可以直接使用絕對導入
from apps.x21_generate_dealer_stress_report.run import run_app


def main():
    """執行原子化測試"""
    test_event_params = {
        "start_date": "2024-01-01",
        "end_date": "2024-01-15",
        "output_format": "test_run" # 標記為測試執行，可能用於 run_app 內部邏輯
    }

    print(f"[*] 開始執行原子化測試，日期範圍: {test_event_params['start_date']} 至 {test_event_params['end_date']}")

    try:
        # 呼叫核心應用程式邏輯
        run_app(event_params=test_event_params)
        print("✅ [SOP-COMPLIANT TEST] Stress report module executed successfully.")
    except Exception as e:
        print(f"❌ [SOP-COMPLIANT TEST] Stress report module execution failed: {e}")
        # 可以選擇在這裡重新拋出異常，如果測試框架需要捕捉
        # raise

if __name__ == "__main__":
    # 設定 FRED API 金鑰環境變數 (僅為測試目的，實際部署時應由外部設定)
    # 重要：實際測試時，需要確保 API_KEY_FRED 環境變數已設定
    # 此處僅為範例，不應將實際金鑰硬編碼於此
    if "API_KEY_FRED" not in os.environ:
        print("[WARNING] 環境變數 API_KEY_FRED 未設定。測試可能因缺少 API 金鑰而失敗。")
        print("          請使用以下方式執行測試: API_KEY_FRED=\"YOUR_KEY_HERE\" python apps/x21_generate_dealer_stress_report/_test_run.py")
        # 或者，如果有一個測試用的假金鑰或模擬器，可以在這裡設定
        # os.environ["API_KEY_FRED"] = "TEST_KEY_ONLY_FOR_CI" # 僅作示例

    main()
