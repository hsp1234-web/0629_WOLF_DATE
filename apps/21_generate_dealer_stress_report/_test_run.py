# -*- coding: utf-8 -*-
"""
標準化整合測試腳本 for 21_generate_dealer_stress_report App
"""

import sys
import os

# 將父目錄添加到 sys.path 以便導入 run_app
# 假設 _test_run.py 和 run.py 在同一個目錄下，
# run.py 中的 from .data_fetcher import ... 這樣的相對導入需要正確的套件上下文
# 我們需要將 apps 目錄加入到 sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
apps_dir = os.path.dirname(current_dir) # apps/21_generate_dealer_stress_report/ -> apps/
if apps_dir not in sys.path:
    sys.path.insert(0, apps_dir)

# 現在可以從 apps.x21_generate_dealer_stress_report.run 導入
# 但 run.py 本身是作為腳本設計的，其導入方式是 from .data_fetcher
# 如果直接從外部呼叫 run_app，需要確保 Python 執行時能解析這些相對導入
# 最簡單的方式是確保執行 _test_run.py 時，其父目錄 (apps) 在 PYTHONPATH 或 sys.path 中
# 並且 python -m apps.x21_generate_dealer_stress_report._test_run 這樣執行
# 或者，在 _test_run.py 中模擬這種環境

try:
    from x21_generate_dealer_stress_report.run import run_app
except ModuleNotFoundError:
    # 如果上述導入失敗，嘗試另一種方式，假設 `apps` 是頂層套件
    # 這通常在執行 `python apps/x21_generate_dealer_stress_report/_test_run.py` 時需要
    grandparent_dir = os.path.dirname(apps_dir) # apps/ -> ./ (專案根目錄)
    if grandparent_dir not in sys.path:
        sys.path.insert(0, grandparent_dir)
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
