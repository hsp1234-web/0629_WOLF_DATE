# -*- coding: utf-8 -*-
import argparse
import os
from prospector import run_prospector_core, log # 導入日誌記錄器以備不時之需

def main():
    parser = argparse.ArgumentParser(
        description="「偵察兵」微應用：掃描指定路徑下的檔案並產生報告。",
        formatter_class=argparse.RawTextHelpFormatter # 保持描述格式
    )
    parser.add_argument(
        "--file-path",
        required=True,
        help="必要參數：指定要進行探勘的目標資料夾或檔案路徑。"
    )

    args = parser.parse_args()

    target_path = args.file_path

    # 可以在此處添加對 target_path 的初步檢查，儘管 prospector_core 內部也有檢查
    if not os.path.exists(target_path):
        log.error(f"❌ 錯誤：提供的路徑 '{target_path}' 不存在。請檢查您的輸入。")
        return

    # if not os.path.isdir(target_path):
    #     log.error(f"❌ 錯誤：提供的路徑 '{target_path}' 不是一個有效的資料夾。目前僅支援資料夾探勘。")
    #     return

    # 調用核心探勘邏輯
    # run_prospector_core 函數會處理大部分的日誌輸出
    # 返回的 results 可以在此處被進一步處理，但目前計畫是直接由 core 打印報告
    results = run_prospector_core(target_path)

    # 根據 results 可以在此處添加額外的總結信息或退出碼處理
    if not results:
        log.info("ℹ️ 探勘任務未產生任何結果 (可能路徑下無檔案，或發生了初始化錯誤)。")
    elif any(r.get('type') == 'path_error' or r.get('type') == 'scan_error' for r in results):
        log.error("❌ 探勘任務因路徑或掃描錯誤而提前終止。請查看上方日誌獲取詳細信息。")
    else:
        log.info("✅ 「偵察兵」探勘任務執行完畢。")

if __name__ == "__main__":
    main()
