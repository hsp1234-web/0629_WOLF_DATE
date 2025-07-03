#!/bin/bash

# ==============================================================================
#           全景市場分析儀 - 最終修復與整合驗收計畫 v2.2
#
#  本腳本是專案的「黃金標準」測試流程。
#  它將自動為每個應用建立隔離環境、安裝依賴，並執行驗證。
#  此版本內建了對 'daily_market_analyzer' 依賴遺漏的自動修復邏輯。
# ==============================================================================

# --- 通用函式 ---
run_test_for_app() {
    local app_path=$1
    local app_name=$(basename "$app_path")
    local venv_path="${app_path}/.venv"
    local requirements_path="${app_path}/requirements.txt"
    local test_script_path="${app_path}/_test_run.py"

    echo "============================================================"
    echo "🔵 開始驗收應用程式: ${app_name}"
    echo "============================================================"

    # --- 步驟 0: 檢查 _test_run.py 是否存在 ---
    if [ ! -f "$test_script_path" ]; then
        echo "❌ [0/5] 失敗: 在 ${app_path} 中找不到標準測試腳本 _test_run.py。"
        return 1
    fi
    echo "  [0/5] 標準測試腳本 (_test_run.py) 存在。"

    # --- 步驟 1: 檢查並修復 requirements.txt ---
    if [ ! -f "$requirements_path" ]; then
        echo "❌ [1/5] 失敗: 在 ${app_path} 中找不到 requirements.txt。"
        return 1
    fi
    # **核心修復邏輯**
    if [[ "$app_name" == "daily_market_analyzer" ]] && ! grep -q "duckdb" "$requirements_path"; then
        echo "  🟡 [1/5] 警告: 在 ${app_name} 的依賴中發現遺漏 'duckdb'。正在自動修復..."
        # 確保 requirements.txt 最後一行有換行符，避免 duckdb 直接附加到最後一行
        if [ -n "$(tail -c1 "$requirements_path")" ]; then
            echo "" >> "$requirements_path" # Add newline if file doesn't end with one
        fi
        echo "duckdb" >> "$requirements_path"
        echo "      ✅ 已將 'duckdb' 添加至 ${requirements_path}"
    else
        echo "  [1/5] 依賴定義檔案 (requirements.txt) 完整。"
    fi

    # --- 步驟 2: 建立虛擬環境 ---
    echo "  [2/5] 正在於 ${venv_path} 建立虛擬環境..."
    python3 -m venv "$venv_path"
    if [ $? -ne 0 ]; then echo "❌ 失敗: 無法建立虛擬環境。"; rm -rf "$venv_path"; return 1; fi
    echo "      ✅ 虛擬環境建立成功。"

    # --- 步驟 3: 安裝依賴 ---
    echo "  [3/5] 正在安裝應用程式依賴..."
    source "${venv_path}/bin/activate"
    pip install -r "$requirements_path" > /dev/null 2>&1 # 將詳細輸出重定向，保持日誌乾淨
    local pip_status=$?
    deactivate
    if [ $pip_status -ne 0 ]; then echo "❌ 失敗: 依賴套件安裝失敗。"; rm -rf "$venv_path"; return 1; fi
    echo "      ✅ 所有依賴套件安裝成功。"

    # --- 步驟 4: 執行煙霧測試 ---
    echo "  [4/5] 正在執行模組連結性煙霧測試 (_test_run.py)..."
    "${venv_path}/bin/python" "$test_script_path"
    local test_status=$?
    if [ $test_status -ne 0 ]; then
        echo "❌ 測試失敗: ${app_name} 的 _test_run.py 執行時發生錯誤 (返回碼: $test_status)。"
        rm -rf "$venv_path" # 清理失敗的虛擬環境
        return 1
    fi

    # --- 步驟 5: 清理環境 ---
    echo "  [5/5] 正在清理虛擬環境 ${venv_path}..."
    rm -rf "$venv_path"
    echo "      ✅ 虛擬環境已刪除。"

    echo "✅✅✅ 應用程式 ${app_name} 已成功通過所有驗收標準！"
    echo ""
    return 0
}

# --- 為三大核心應用預先準備好 requirements.txt (如果不存在或需要初始化) ---
# 確保目標目錄存在
mkdir -p apps/taifex_data_pipeline
mkdir -p apps/daily_market_analyzer
mkdir -p apps/taifex_data_prospector

# 數據供應鏈引擎的依賴
cat << EOF > apps/taifex_data_pipeline/requirements.txt
pandas
pyarrow
duckdb
tqdm
EOF

# 市場宏觀內視引擎的依賴
cat << EOF > apps/daily_market_analyzer/requirements.txt
pandas
yfinance
pytz
psutil
tqdm
EOF

# 選擇權深度報告引擎的依賴
cat << EOF > apps/taifex_data_prospector/requirements.txt
pandas
pyarrow
duckdb
tqdm
EOF

# --- 執行所有應用的驗收流程 ---
overall_test_status=0

run_test_for_app "apps/taifex_data_pipeline"
if [ $? -ne 0 ]; then overall_test_status=1; fi

run_test_for_app "apps/daily_market_analyzer"
if [ $? -ne 0 ]; then overall_test_status=1; fi

run_test_for_app "apps/taifex_data_prospector"
if [ $? -ne 0 ]; then overall_test_status=1; fi

echo "============================================================"
if [ $overall_test_status -eq 0 ]; then
    echo "🎉🎉🎉 恭喜！所有應用程式均已通過最終整合驗收！ 🎉🎉🎉"
else
    echo "🔥🔥🔥 部分應用程式未能通過最終整合驗收。請檢查上面的日誌。 🔥🔥🔥"
fi
echo "============================================================"

exit $overall_test_status
