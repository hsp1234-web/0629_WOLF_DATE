#!/bin/bash
# =================================================================
# 規格書 1: 原子化品質保證檢查 (Atomic QA Check)
# 目的: 驗證所有獨立模組的功能正確性
# =================================================================

# 步驟 1/5: 啟用嚴格模式
# 確保任何指令失敗都會立刻中止，避免產生無效的測試結果。
set -e

echo "⚪ [Phase 1/2, Step 1/5] 開始執行品質保證檢查..."

# 步驟 2/5: 安裝專案依賴
# 根據 requirements.txt 一次性安裝，確保測試環境的純淨與完整。
echo "⚪ [Phase 1/2, Step 2/5] 正在安裝專案依賴..."
pip install -r requirements.txt
echo "✅ [Phase 1/2, Step 2/5] 依賴安裝完畢。"

# 步驟 3/5: 設定 Python 模組導入路徑
# 這是駕馭沙箱環境、解決 ModuleNotFoundError 的關鍵策略。
export PYTHONPATH=$(pwd)
echo "⚪ [Phase 1/2, Step 3/5] 設定 PYTHONPATH 以識別專案模組。"
echo "✅ [Phase 1/2, Step 3/5] PYTHONPATH 已設定為: ${PYTHONPATH}"

# 步驟 4/5: 執行單元測試
# 以標準化、模組化的方式，自動發現並運行 `tests` 目錄下的所有測試案例。
# 我們將使用 pytest 進行測試
echo "⚪ [Phase 1/2, Step 4/5] 正在啟動自動化單元測試 (pytest)..."
python -m pytest tests
echo "✅ [Phase 1/2, Step 4/5] 所有單元測試執行完畢。"

# 步驟 5/5: 完成報告
echo "✅ [Phase 1/2, Step 5/5] 品質保證檢查流程成功結束。"
