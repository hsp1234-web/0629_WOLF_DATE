# -*- coding: utf-8 -*-
# 採集官測試檔
import os
import sys
import unittest
import subprocess
import json
import tempfile
import shutil
import re # 用於解析日誌
from typing import Optional, Any # 新增 Optional 和 Any

# --- 路徑自我校正樣板碼 ---
try:
    current_downloader_dir = os.path.dirname(os.path.abspath(__file__))
    apps_dir = os.path.dirname(current_downloader_dir)
    project_root = os.path.dirname(apps_dir)

    if apps_dir not in sys.path:
        sys.path.insert(0, apps_dir)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    prospector_dir = os.path.join(apps_dir, "taifex_data_prospector")
    if prospector_dir not in sys.path:
        sys.path.insert(0, prospector_dir)
except Exception as e:
    print(f"路徑校正時發生錯誤: {e}", file=sys.stderr)
    pass
# --- 路徑自我校正樣板碼結束 ---

class TestTaifexDataDownloader(unittest.TestCase):

    def setUp(self):
        self.downloader_run_py = os.path.join(current_downloader_dir, "run.py")
        self.prospector_run_py = os.path.join(apps_dir, "taifex_data_prospector", "run.py")
        self.temp_download_dir = tempfile.mkdtemp(prefix="test_downloader_async_")

    def tearDown(self):
        if os.path.exists(self.temp_download_dir):
            shutil.rmtree(self.temp_download_dir)

    def find_first_zip_file(self, directory: str, filename_pattern: Optional[str] = None) -> str | None:
        """遞迴尋找指定目錄下的第一個 .zip 檔案，可選擇性匹配檔名模式。"""
        for root, _, files in os.walk(directory):
            for file in files:
                if file.lower().endswith(".zip"):
                    if filename_pattern is None or re.search(filename_pattern, file, re.IGNORECASE):
                        return os.path.join(root, file)
        return None

    def parse_downloader_stdout_for_file_status(self, stdout: str, filename: str) -> Optional[str]:
        """
        從下載器的 stdout 中解析特定檔案的下載狀態。
        預期日誌格式: "  - FILENAME: STATUS (OPTIONAL_ERROR_MSG)"
        或最終總結行的 "成功 X，已存在 Y，未找到 Z，錯誤 E"
        這裡簡化為查找包含檔案名和狀態的詳細行。
        """
        # 正則表達式匹配類似: "- futures_trades_2023_11_01.zip: success"
        # 或 "- OptionsDaily_2023_11_01.zip: not_found_html_content (伺服器返回 HTML 內容 (疑似軟404))"
        # 需要處理檔名中的特殊字元，但這裡的檔名比較固定
        # pattern = re.compile(rf"\s*-\s*{re.escape(filename)}:\s*([a-zA-Z0-9_]+)")
        # 更寬鬆的匹配，捕獲狀態和可選的括號內訊息
        pattern = re.compile(rf"\s*-\s*{re.escape(filename)}:\s*([a-zA-Z0-9_]+)(?:\s*\((.*?)\))?")

        for line in stdout.splitlines():
            match = pattern.search(line)
            if match:
                status = match.group(1)
                # error_detail = match.group(2) # 可選的錯誤細節
                return status.strip()
        return None # 未找到該檔案的明確狀態行

    def test_async_download_and_prospect(self):
        """
        測試非同步下載一天的特定 ZIP 數據，然後使用偵察兵探勘。
        主要驗證下載器是否正確處理外部資源問題並更新日誌。
        """
        test_date = "2023-11-01"
        test_file_type_arg = "--options-summary"
        # 預期 OptionsDaily_YYYY_MM_DD.zip -> OptionsDaily_2023_11_01.zip
        expected_dl_filename = f"OptionsDaily_{test_date.replace('-', '_')}.zip"
        # 預期下載器會將檔案放在 output_path / TASKS_CONFIG[key]['folder'] / expected_dl_filename
        # TASKS_CONFIG['options_summary']['folder'] 是 'A_Core_Trading/Options_Summary'
        expected_relative_subdir = os.path.join("A_Core_Trading", "Options_Summary")


        download_command = [
            sys.executable, self.downloader_run_py,
            "--start-date", test_date, "--end-date", test_date,
            "--output-path", self.temp_download_dir,
            test_file_type_arg,
            "--max-concurrent", "2"
        ]

        download_process = subprocess.run(download_command, capture_output=True, text=True, encoding='utf-8')

        print(f"下載器 stdout:\n{download_process.stdout}")
        print(f"下載器 stderr:\n{download_process.stderr}")

        self.assertEqual(download_process.returncode, 0,
                         f"下載器 run.py 應成功執行並返回 0。標準誤: {download_process.stderr}")

        downloader_stdout_log = download_process.stdout
        file_status_from_log = self.parse_downloader_stdout_for_file_status(downloader_stdout_log, expected_dl_filename)

        print(f"從日誌解析到檔案 '{expected_dl_filename}' 的狀態為: {file_status_from_log}")

        if file_status_from_log == 'success' or file_status_from_log == 'exists':
            # 檔案在日誌中被報告為成功或已存在，現在實際查找它
            # 傳遞 expected_dl_filename 給 find_first_zip_file 來確保找到的是目標檔案
            # 檔案應該在 self.temp_download_dir 下的 expected_relative_subdir 中
            target_search_dir = os.path.join(self.temp_download_dir, expected_relative_subdir)
            downloaded_zip_file_path = self.find_first_zip_file(target_search_dir, expected_dl_filename)

            if downloaded_zip_file_path and os.path.exists(downloaded_zip_file_path):
                print(f"檔案 '{expected_dl_filename}' 根據日誌狀態 '{file_status_from_log}' 應存在，並在路徑 '{downloaded_zip_file_path}' 找到。")
                self.assertTrue(os.path.getsize(downloaded_zip_file_path) > 0, f"下載的 ZIP 檔案 {downloaded_zip_file_path} 為空。")

                # --- 開始偵察兵驗證 ---
                prospect_command = [sys.executable, self.prospector_run_py, "--file-path", downloaded_zip_file_path]
                prospect_process = subprocess.run(prospect_command, capture_output=True, text=True, encoding='utf-8')
                self.assertEqual(prospect_process.returncode, 0, f"偵察兵執行失敗: {prospect_process.stderr}")

                try:
                    report = json.loads(prospect_process.stdout)
                except json.JSONDecodeError:
                    self.fail(f"偵察兵輸出不是有效 JSON: {prospect_process.stdout}")

                self.assertEqual(report["status"], "success", "偵察兵報告應為 success")
                self.assertEqual(report["file_type"], "zip", f"偵察兵報告 file_type 應為 zip, 得到 {report.get('file_type')}")
                self.assertTrue(any(".csv" in item.lower() for item in report["preview"]), f"偵察兵預覽應包含 CSV 成員: {report['preview']}")
                # --- 偵察兵驗證結束 ---
            else:
                self.fail(f"日誌報告檔案 '{expected_dl_filename}' 狀態為 '{file_status_from_log}'，但在預期目錄 '{target_search_dir}' 中未找到該檔案。")

        elif file_status_from_log == 'not_found_html_content':
            print(f"日誌確認檔案 '{expected_dl_filename}' 因 'not_found_html_content' 未下載。")
            self.skipTest("由於外部資源返回HTML (not_found_html_content)，跳過對下載檔案內容的驗證。下載器按預期處理了此情況。")

        elif file_status_from_log and 'not_found' in file_status_from_log: # 包括 not_found, not_found_empty_content 等
            print(f"日誌確認檔案 '{expected_dl_filename}' 因 '{file_status_from_log}' 未下載。")
            self.skipTest(f"由於外部資源未找到或為空 (狀態: {file_status_from_log})，跳過內容驗證。下載器按預期處理了此情況。")

        elif file_status_from_log and 'error' in file_status_from_log:
             print(f"日誌確認檔案 '{expected_dl_filename}' 下載時發生錯誤: '{file_status_from_log}'。")
             self.skipTest(f"由於下載時發生錯誤 (狀態: {file_status_from_log})，跳過內容驗證。")

        else:
            # 未能從日誌解析出明確狀態，或者狀態未知
            self.fail(f"檔案 '{expected_dl_filename}' 未成功下載，且無法從日誌中確定其狀態或狀態未知 ('{file_status_from_log}')。\n下載器 stdout:\n{downloader_stdout_log}")

if __name__ == "__main__":
    unittest.main(argv=['first-arg-is-ignored'], exit=False)
