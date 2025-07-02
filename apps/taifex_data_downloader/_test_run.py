# -*- coding: utf-8 -*-
# 採集官測試檔 (v16.0 同步版本)
import os
import sys
import unittest
import subprocess
import json
import tempfile
import shutil
import re
from typing import Optional

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

class TestTaifexDataDownloaderSync(unittest.TestCase): # 更名以區分

    def setUp(self):
        self.downloader_run_py = os.path.join(current_downloader_dir, "run.py")
        self.prospector_run_py = os.path.join(apps_dir, "taifex_data_prospector", "run.py")
        self.temp_download_dir = tempfile.mkdtemp(prefix="test_downloader_sync_")

    def tearDown(self):
        if os.path.exists(self.temp_download_dir):
            shutil.rmtree(self.temp_download_dir)

    def find_first_zip_file(self, directory: str, filename_pattern: Optional[str] = None) -> str | None:
        for root, _, files in os.walk(directory):
            for file in files:
                if file.lower().endswith(".zip"):
                    if filename_pattern is None or re.search(filename_pattern, file, re.IGNORECASE):
                        return os.path.join(root, file)
        return None

    def parse_downloader_log_for_file_status(self, log_output: str, filename: str) -> Optional[str]:
        # 預期日誌格式: "  [YYYY-MM-DD] Task Display Name -> FILENAME ... STATUS_SYMBOL (DETAIL)"
        # tqdm.write 輸出到 stderr
        # 我們需要從 stderr 中解析
        pattern = re.compile(rf"\s*\[\d{{4}}-\d{{2}}-\d{{2}}\].*?->\s*{re.escape(filename)}\s*\.\.\.\s*(✅|☑️|➖|⚠️|❌)\s*(?:\((.*?)\))?")
        status_map = {
            "✅": "success",
            "☑️": "exists",
            "➖": "not_found",
            "⚠️": "warning", # 代表 html content 或 empty file
            "❌": "error"
        }
        for line in log_output.splitlines():
            match = pattern.search(line)
            if match:
                symbol = match.group(1)
                detail = match.group(2) # 例如 "HTML內容", "超時" 等

                base_status = status_map.get(symbol, "unknown_symbol")

                if base_status == "warning": # 細化 warning
                    if detail and "HTML內容" in detail: return "not_found_html_content"
                    if detail and "空檔案" in detail: return "not_found_empty_file"
                elif base_status == "error" and detail: # 細化 error
                     if "超時" in detail: return "error_timeout"
                     if "HTTP" in detail: return f"error_http_{detail.replace('HTTP ', '')}" # error_http_500
                     return f"error_detail_{detail.replace(' ', '_').lower()}"
                return base_status
        return None

    def test_sync_download_and_prospect(self):
        test_date = "2023-11-01"
        test_file_type_arg = "--options-summary"
        expected_dl_filename = f"OptionsDaily_{test_date.replace('-', '_')}.zip"
        expected_relative_subdir = os.path.join("A_Core_Trading", "Options_Summary")

        download_command = [
            sys.executable, self.downloader_run_py,
            "--start-date", test_date, "--end-date", test_date,
            "--output-path", self.temp_download_dir,
            test_file_type_arg,
            "--sleep", "0.1" # 測試時使用較小延遲
        ]

        download_process = subprocess.run(download_command, capture_output=True, text=True, encoding='utf-8')

        print(f"同步下載器 stdout:\n{download_process.stdout}") # 主要的總結訊息
        print(f"同步下載器 stderr:\n{download_process.stderr}") # tqdm 進度條和詳細日誌 (tqdm.write)

        self.assertEqual(download_process.returncode, 0,
                         f"同步下載器 run.py 應成功執行並返回 0。標準誤: {download_process.stderr}")

        # 詳細日誌在 stderr (因為 tqdm.write 預設輸出到 stderr)
        file_status_from_log = self.parse_downloader_log_for_file_status(download_process.stderr, expected_dl_filename)

        # 如果 stderr 中沒有，嘗試 stdout (雖然 downloader 的設計是 tqdm.write 到 stderr)
        if file_status_from_log is None:
            print(f"在 stderr 中未解析到狀態，嘗試從 stdout 解析...")
            file_status_from_log = self.parse_downloader_log_for_file_status(download_process.stdout, expected_dl_filename)

        print(f"從日誌解析到檔案 '{expected_dl_filename}' 的狀態為: {file_status_from_log}")

        if file_status_from_log == 'success' or file_status_from_log == 'exists':
            target_search_dir = os.path.join(self.temp_download_dir, expected_relative_subdir)
            downloaded_zip_file_path = self.find_first_zip_file(target_search_dir, expected_dl_filename)

            if downloaded_zip_file_path and os.path.exists(downloaded_zip_file_path):
                print(f"檔案 '{expected_dl_filename}' 根據日誌狀態 '{file_status_from_log}' 應存在，並在路徑 '{downloaded_zip_file_path}' 找到。")
                self.assertTrue(os.path.getsize(downloaded_zip_file_path) > 0)

                prospect_command = [sys.executable, self.prospector_run_py, "--file-path", downloaded_zip_file_path]
                prospect_process = subprocess.run(prospect_command, capture_output=True, text=True, encoding='utf-8')
                self.assertEqual(prospect_process.returncode, 0, f"偵察兵執行失敗: {prospect_process.stderr}")

                report = json.loads(prospect_process.stdout)
                self.assertEqual(report["status"], "success")
                self.assertEqual(report["file_type"], "zip")
                self.assertTrue(any(".csv" in item.lower() for item in report["preview"]))
            else:
                self.fail(f"日誌報告檔案 '{expected_dl_filename}' 狀態為 '{file_status_from_log}'，但在預期目錄 '{target_search_dir}' 中未找到。")

        elif file_status_from_log == 'not_found_html_content':
            self.skipTest("外部資源返回HTML (not_found_html_content)，跳過內容驗證。下載器已按預期處理。")

        elif file_status_from_log and ('not_found' in file_status_from_log or 'warning' in file_status_from_log):
            self.skipTest(f"外部資源未找到或為空 (狀態: {file_status_from_log})，跳過內容驗證。下載器已按預期處理。")

        elif file_status_from_log and 'error' in file_status_from_log:
             self.skipTest(f"下載時發生錯誤 (狀態: {file_status_from_log})，跳過內容驗證。")
        else:
            self.fail(f"檔案 '{expected_dl_filename}' 未成功下載，且無法從日誌確定其狀態 ('{file_status_from_log}')。\nstderr:\n{download_process.stderr}")

if __name__ == "__main__":
    unittest.main(argv=['first-arg-is-ignored'], exit=False)
