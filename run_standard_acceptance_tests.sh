#!/bin/bash

# ==============================================================================
#                 全景市場分析儀 - 標準化驗收測試 v2.1
#
#  本腳本將為每一個核心應用程式建立獨立的 Python 虛擬環境，
#  安裝其專屬的依賴，然後執行標準化的煙霧測試。
#  這旨在模擬一個乾淨、獨立的工業級部署與測試流程。
# ==============================================================================

# --- 通用函式 ---
run_test_for_app() {
    local app_path=$1
    local app_name=$(basename "$app_path")
    local venv_path="${app_path}/.venv"
    local requirements_path="${app_path}/requirements.txt"
    local test_script_path="${app_path}/_test_run.py"

    echo "============================================================"
    echo "🔵 開始測試應用程式: ${app_name}"
    echo "============================================================"

    # --- 步驟 0: 檢查 _test_run.py 是否存在 ---
    if [ ! -f "$test_script_path" ]; then
        echo "❌ 失敗: 在 ${app_path} 中找不到 _test_run.py。"
        echo "請為此應用程式提供標準化的煙霧測試腳本。"
        return 1
    fi
    echo "  [0/5] 標準測試腳本 (_test_run.py) 存在。"


    # --- 步驟 1: 檢查 requirements.txt 是否存在 ---
    if [ ! -f "$requirements_path" ]; then
        echo "❌ 失敗: 在 ${app_path} 中找不到 requirements.txt。"
        echo "請為此應用程式定義其所需的依賴套件。"
        return 1
    fi
    echo "  [1/5] 依賴定義檔案 (requirements.txt) 存在。"

    # --- 步驟 2: 建立獨立的 Python 虛擬環境 ---
    echo "  [2/5] 正在於 ${venv_path} 建立虛擬環境..."
    python3 -m venv "$venv_path"
    if [ $? -ne 0 ]; then
        echo "❌ 失敗: 無法建立虛擬環境。"
        return 1
    fi
    echo "      ✅ 虛擬環境建立成功。"

    # --- 步驟 3: 在虛擬環境中安裝依賴 ---
    echo "  [3/5] 正在安裝應用程式依賴..."
    source "${venv_path}/bin/activate"
    pip install -r "$requirements_path"
    if [ $? -ne 0 ]; then
        echo "❌ 失敗: 依賴套件安裝失敗。"
        deactivate
        # 保留虛擬環境以便調試，或可選擇刪除： rm -rf "$venv_path"
        return 1
    fi
    deactivate
    echo "      ✅ 所有依賴套件安裝成功。"

    # --- 步驟 4: 執行煙霧測試 ---
    echo "  [4/5] 正在執行模組連結性煙霧測試 (_test_run.py)..."
    # 我們使用虛擬環境中的 python 解譯器來執行腳本
    "${venv_path}/bin/python" "$test_script_path"
    local test_exit_code=$?

    # --- 步驟 5: 清理虛擬環境 ---
    echo "  [5/5] 正在清理虛擬環境 ${venv_path}..."
    rm -rf "$venv_path"
    echo "      ✅ 虛擬環境已刪除。"


    if [ $test_exit_code -ne 0 ]; then
        echo "❌ 測試失敗: ${app_name} 的 _test_run.py 執行時發生錯誤 (返回碼: ${test_exit_code})。"
        return 1
    fi

    echo "✅✅✅ 應用程式 ${app_name} 通過所有測試！"
    echo ""
    return 0
}

# --- 為三大核心應用定義依賴 ---

# 數據供應鏈引擎的依賴
mkdir -p apps/taifex_data_pipeline
cat << EOF > apps/taifex_data_pipeline/requirements.txt
pandas
pyarrow
duckdb
tqdm
EOF

# 市場宏觀內視引擎的依賴
mkdir -p apps/daily_market_analyzer
cat << EOF > apps/daily_market_analyzer/requirements.txt
pandas
yfinance
pytz
psutil
tqdm
EOF

# 選擇權深度報告引擎的依賴
mkdir -p apps/taifex_data_prospector
cat << EOF > apps/taifex_data_prospector/requirements.txt
pandas
pyarrow
duckdb
tqdm
EOF

# --- 執行所有測試 ---
overall_test_status=0

run_test_for_app "apps/taifex_data_pipeline"
if [ $? -ne 0 ]; then overall_test_status=1; fi

run_test_for_app "apps/daily_market_analyzer"
if [ $? -ne 0 ]; then overall_test_status=1; fi

run_test_for_app "apps/taifex_data_prospector"
if [ $? -ne 0 ]; then overall_test_status=1; fi

echo "============================================================"
if [ $overall_test_status -eq 0 ]; then
    echo "🎉🎉🎉 所有應用程式均已成功通過標準化驗收流程！ 🎉🎉🎉"
else
    echo "🔥🔥🔥 部分應用程式未能通過標準化驗收流程。請檢查上面的日誌。 🔥🔥🔥"
fi
echo "============================================================"

exit $overall_test_status
