# -*- coding: utf-8 -*-
"""
標準化整合測試腳本 for 21_generate_dealer_stress_report App
"""

# --- BEGIN HERMETIC PATH CORRECTION ---
# 動態路徑校正，確保無論從何處執行此腳本，都能正確解析專案模組。
# 核心原則：向上查找 .git 目錄，將其所在的目錄（專案根目錄）添加到 sys.path 的最前端。
import sys
import os

def find_project_root(marker_file_or_dir=".git"):
    """向上查找包含標記檔案或目錄的專案根目錄。"""
    current_path = os.path.abspath(os.path.dirname(__file__))
    while True:
        if os.path.exists(os.path.join(current_path, marker_file_or_dir)):
            return current_path
        parent_path = os.path.dirname(current_path)
        if parent_path == current_path: # 到達檔案系統根目錄
            raise RuntimeError(f"無法找到專案根目錄 (標記: {marker_file_or_dir})")
        current_path = parent_path

_project_root_dir = find_project_root()

if _project_root_dir not in sys.path:
    sys.path.insert(0, _project_root_dir)
# --- END HERMETIC PATH CORRECTION ---

# 現在，由於專案根目錄已經在 sys.path 中，可以直接使用絕對導入
from apps.stress_report_app.run import run_app


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
