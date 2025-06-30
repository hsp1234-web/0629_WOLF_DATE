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

# 導入標準模組
import subprocess # 用於執行子程序

# 注意：不再需要從 .run 導入，因為我們將透過 subprocess 執行

def test_app_execution_as_subprocess():
    """
    SOP v2.0: 測試 run.py 是否可以作為一個獨立的子程序成功執行，
    模擬 Colab 或其他外部環境的調用方式。
    """
    print("[*] 開始執行 SOP v2.0 整合測試：模擬外部調用 run.py")

    # 步驟一：路徑自我校正已在檔案頂部完成，此處確保 _project_root_dir 可用
    #         並定位目標腳本 run.py
    if '_project_root_dir' not in globals():
        print("錯誤：_project_root_dir 未定義。路徑校正可能失敗。")
        assert False, "專案根目錄未找到，無法定位 run.py"

    # apps/stress_report_app/run.py 的相對路徑
    # 我們預期 _test_run.py 和 run.py 在同一個目錄下
    target_script = os.path.join(
        os.path.dirname(__file__), 'run.py'
    )

    if not os.path.exists(target_script):
        print(f"錯誤：目標腳本 run.py 未在預期路徑找到: {target_script}")
        assert False, f"目標腳本 run.py 未在預期路徑找到: {target_script}"

    print(f"[*] 目標腳本路徑: {target_script}")

    # 步驟二：定義測試參數 (擴大日期範圍以獲取更多數據點)
    start_date = "2023-01-01" # 修改開始日期
    end_date = "2024-01-15"   # 結束日期不變，確保超過一年
    # 使用 'test_run' output_format，這在 run.py 中通常意味著不實際寫入檔案，
    # 或執行簡化的輸出，適合測試。
    output_format = "test_run"

    print(f"[*] 測試參數：start_date={start_date}, end_date={end_date}, output_format={output_format}")

    # 步驟三：建構 subprocess 命令
    cmd = [
        sys.executable,  # 使用目前的 Python 解譯器
        target_script,
        '--start-date', start_date,
        '--end-date', end_date,
        '--output-format', output_format
        # 如有需要，可以加入 '--no-charts', '--no-text' 等其他 run.py 支援的參數
    ]

    print(f"[*] 執行命令: {' '.join(cmd)}")

    # 步驟四：設定環境變數 (例如 API 金鑰)
    # 非常重要：確保測試環境中設定了必要的 API 金鑰
    # 這裡我們複製現有環境變數，並確保測試用的 API 金鑰存在
    env = os.environ.copy()
    if "API_KEY_FRED" not in env:
        print("[警告] 環境變數 API_KEY_FRED 未在系統環境中設定。")
        print("         為本次測試設定一個臨時的測試金鑰 'test_key_12345'。")
        print("         在實際 CI/CD 或生產環境中，應由該環境正確提供此金鑰。")
        env["API_KEY_FRED"] = "test_key_12345" # 使用一個假的或測試專用的金鑰
    else:
        print(f"[*] 使用現有環境變數中的 API_KEY_FRED: {env['API_KEY_FRED'][:5]}... (已遮蔽)")


    # 步驟五：執行子程序
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,  # 捕獲 stdout 和 stderr
            text=True,            # 以文字模式處理輸出 (Python 3.7+)
            env=env,              # 傳遞修改後的環境變數
            check=False           # 改為 False，自行檢查 returncode
        )
    except Exception as e:
        print(f"❌ subprocess.run 執行期間發生未預期錯誤: {e}")
        assert False, f"subprocess.run 執行失敗: {e}"

    # 步驟六：驗證結果
    print(f"[*] run.py 子程序執行完畢。返回碼: {result.returncode}")

    if result.stdout:
        print("--- run.py STDOUT START ---")
        print(result.stdout)
        print("--- run.py STDOUT END ---")

    if result.stderr:
        print("--- run.py STDERR START ---")
        print(result.stderr)
        print("--- run.py STDERR END ---")

    assert result.returncode == 0, \
        f"❌ [SOP v2.0 TEST] 腳本執行失敗！返回碼: {result.returncode}\n" \
        f"詳細標準輸出 (stdout):\n{result.stdout}\n" \
        f"詳細標準錯誤 (stderr):\n{result.stderr}"

    print("✅ [SOP v2.0 TEST] 應用程式 run.py 作為子程序成功執行。外部調用模擬測試通過！")


if __name__ == "__main__":
    # 確保路徑校正已執行 (通常在檔案頂部)
    if '_project_root_dir' not in globals() or not _project_root_dir:
        print("緊急錯誤：專案根目錄未能成功設定。請檢查檔案頂部的路徑校正代碼。")
        sys.exit(1) # 嚴重錯誤，終止執行

    print(f"[*] _test_run.py: 專案根目錄已設定為: {_project_root_dir}")
    print(f"[*] _test_run.py: 目前 Python sys.path[0]: {sys.path[0]}")

    # 執行新的測試函式
    try:
        test_app_execution_as_subprocess()
    except AssertionError as ae:
        print(f"❌ 測試斷言失敗: {ae}")
        sys.exit(1) # 測試失敗，以非零狀態碼退出
    except Exception as e:
        print(f"❌ 測試執行期間發生未預期錯誤: {e}")
        sys.exit(1) # 測試失敗，以非零狀態碼退出

    print("🎉 所有測試執行完畢。")
