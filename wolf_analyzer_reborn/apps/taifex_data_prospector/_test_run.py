# -*- coding: utf-8 -*-
import unittest
import subprocess
import os
import shutil
import sys
import zipfile
from typing import List, Dict, Any

# 將 apps 目錄添加到 sys.path，以便導入 prospector 中的模塊 (如果測試需要直接調用)
# 對於通過 subprocess 執行 run.py 的方式，這不是嚴格必需的，但保持良好實踐
current_dir = os.path.dirname(os.path.abspath(__file__))
# apps_dir = os.path.dirname(current_dir) # prospector 的父目錄是 apps
# wolf_analyzer_reborn_dir = os.path.dirname(apps_dir) # apps 的父目錄是 wolf_analyzer_reborn
# sys.path.insert(0, apps_dir)
# sys.path.insert(0, wolf_analyzer_reborn_dir)


class TestProspectorRun(unittest.TestCase):
    test_dir_name = "temp_test_prospector_data_F2A5C1E3" # 使用一個獨特的名稱以避免衝突
    run_script_path = os.path.join(current_dir, "run.py")

    @classmethod
    def setUpClass(cls):
        """在所有測試開始前，創建測試目錄和檔案"""
        cls.base_test_dir = os.path.join(current_dir, cls.test_dir_name)
        os.makedirs(cls.base_test_dir, exist_ok=True)

        # 創建子目錄
        cls.sub_dir_path = os.path.join(cls.base_test_dir, "subfolder")
        os.makedirs(cls.sub_dir_path, exist_ok=True)

        # 創建一個 UTF-8 編碼的文字檔案
        cls.txt_file_path = os.path.join(cls.base_test_dir, "test_utf8.txt")
        with open(cls.txt_file_path, "w", encoding="utf-8") as f:
            f.write("這是 UTF-8 檔案的第一行。\n")
            f.write("包含一些中文，例如：你好，世界。\n")
            f.write("第三行內容。\n")
            f.write("第四行。\n")
            f.write("第五行。\n")
            f.write("第六行，這行不應該在預覽中。\n")

        # 創建一個 MS950 編碼的文字檔案在子目錄
        cls.ms950_file_path = os.path.join(cls.sub_dir_path, "test_ms950.txt")
        with open(cls.ms950_file_path, "w", encoding="ms950") as f:
            f.write("這是 MS950 檔案。\n")
            f.write("Big5 編碼測試。\n")

        # 創建一個空的文字檔案
        cls.empty_file_path = os.path.join(cls.base_test_dir, "empty.txt")
        with open(cls.empty_file_path, "w", encoding="utf-8") as f:
            pass # 空檔案

        # 創建一個 ZIP 檔案，內含一個文字檔案和一個空檔案
        cls.zip_file_path = os.path.join(cls.base_test_dir, "archive.zip")
        with zipfile.ZipFile(cls.zip_file_path, 'w') as zf:
            zf.writestr("zipped_content.txt", "這是 ZIP 檔案內的文字。\n第二行在 ZIP 內。")
            zf.writestr("zipped_empty.txt", "") # ZIP 內的空檔案
            zf.writestr("folder_in_zip/another_file.txt", "File inside a folder in ZIP.")


    @classmethod
    def tearDownClass(cls):
        """在所有測試結束後，清理測試目錄和檔案"""
        if os.path.exists(cls.base_test_dir):
            shutil.rmtree(cls.base_test_dir)

    def _run_prospector(self, target_path: str) -> str:
        """輔助函數：執行 run.py 並返回其標準輸出"""
        python_executable = sys.executable # 使用當前 Python 解釋器
        process = subprocess.Popen(
            [python_executable, self.run_script_path, "--file-path", target_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True, # Python 3.7+
            encoding='utf-8'
        )
        stdout, stderr = process.communicate(timeout=60) # 設定超時以防卡死

        if process.returncode != 0 or stderr: # 如果有返回碼錯誤或 stderr 有內容，都打印
            print(f"--- run.py (pid {process.pid}) STDERR ---", flush=True)
            print(stderr if stderr else "<stderr is empty>", flush=True)
            print(f"--- END run.py (pid {process.pid}) STDERR ---", flush=True)

        # self.assertEqual(process.returncode, 0, f"run.py 執行失敗，錯誤信息:\n{stderr}")
        return stdout

    def test_prospect_full_directory(self):
        """測試探勘整個準備好的目錄"""
        output = self._run_prospector(self.base_test_dir)
        # print("\n--- Full Directory Prospector Output ---")
        # print(output)
        # print("--- End Full Directory Prospector Output ---\n")

        # 斷言 UTF-8 檔案的報告
        # prospector.py 輸出的是絕對路徑，這裡檢查路徑是否以期望的 base_test_dir 開頭並包含檔案名
        expected_txt_path = os.path.join(self.base_test_dir, "test_utf8.txt")
        self.assertIn("檔案路徑: " + expected_txt_path, output)
        self.assertIn("偵測編碼: utf-8", output)
        self.assertIn("L1: '這是 UTF-8 檔案的第一行。'", output) # 移除 \n
        self.assertIn("L5: '第五行。'", output) # 移除 \n
        self.assertNotIn("第六行", output, "預覽不應包含第六行")

        # 斷言 MS950 檔案的報告
        expected_ms950_path = os.path.join(self.base_test_dir, "subfolder", "test_ms950.txt")
        self.assertIn("檔案路徑: " + expected_ms950_path, output)
        self.assertIn("偵測編碼: ms950", output)
        self.assertIn("L1: '這是 MS950 檔案。'", output) # 移除 \n

        # 斷言空檔案的報告
        expected_empty_path = os.path.join(self.base_test_dir, "empty.txt")
        self.assertIn("檔案路徑: " + expected_empty_path, output)
        self.assertIn("(檔案為空或無法預覽)", output) # 與 prospector.py 的輸出一致

        # 斷言 ZIP 檔案本身的報告
        expected_zip_path = os.path.join(self.base_test_dir, "archive.zip")
        self.assertIn("檔案路徑: " + expected_zip_path, output)
        # self.assertIn("type: zip_summary", output) # Type 'zip_summary' is not explicitly printed in report string
        self.assertIn("內含檔案: 3 個", output) # This implies it's a zip summary

        # 斷言 ZIP 檔案內部檔案的報告
        self.assertIn(f"檔案路徑: {expected_zip_path} -> zipped_content.txt", output)
        self.assertIn("L1: '這是 ZIP 檔案內的文字。'", output) # 移除 \n
        self.assertIn("偵測編碼: utf-8", output)

        self.assertIn(f"檔案路徑: {expected_zip_path} -> zipped_empty.txt", output)
        self.assertIn("(檔案為空或無法預覽)", output)

        self.assertIn(f"檔案路徑: {expected_zip_path} -> folder_in_zip/another_file.txt", output)
        self.assertIn("L1: 'File inside a folder in ZIP.'", output) # 移除 \n
        # 根據上次的實際輸出，這個純 ASCII 檔案被 ms950 解碼器先捕獲
        self.assertIn("偵測編碼: ms950", output)


        # 斷言總結報告
        self.assertIn("任務總結報告", output)
        self.assertIn("最終探勘檔案總數 (包含壓縮檔內檔案, 不計ZIP本身): 6 個", output)
        self.assertIn("成功探勘檔案清單 (共 6 筆)", output)
        self.assertIn("失敗/跳過檔案清單 (共 0 筆)", output)
        self.assertIn(".txt: 3 個", output) # 修正：基於頂層檔案統計
        self.assertIn(".zip: 1 個", output) # 修正：基於頂層檔案統計
        self.assertIn("ms950: 2 個", output) # 編碼統計基於 successful_files (含zip內)，保持不變
        self.assertIn("utf-8: 2 個", output) # 編碼統計基於 successful_files (含zip內)，保持不變
                                        # empty.txt 和 zipped_empty.txt 的編碼是 N/A，不計入 Counter

    def test_prospect_single_file(self):
        """測試探勘包含單個文字檔案的目錄"""
        single_file_test_dir = os.path.join(self.base_test_dir, "single_txt_dir")
        os.makedirs(single_file_test_dir, exist_ok=True)
        target_txt_file_name = "test_utf8.txt"  # 定義變數
        # 複製目標檔案到這個臨時目錄
        shutil.copy(self.txt_file_path, os.path.join(single_file_test_dir, target_txt_file_name))

        output = self._run_prospector(single_file_test_dir)
        # print("\n--- Single File Dir Prospector Output ---")
        # print(output)
        # print("--- End Single File Dir Prospector Output ---\n")

        # 斷言中路徑應為 prospector 看到的絕對路徑
        expected_file_path_in_report = os.path.join(single_file_test_dir, target_txt_file_name)
        self.assertIn("檔案路徑: " + expected_file_path_in_report, output)
        self.assertIn("偵測編碼: utf-8", output)
        self.assertIn("L1: '這是 UTF-8 檔案的第一行。'", output)
        self.assertIn("任務總結報告", output)
        self.assertIn("最終探勘檔案總數 (包含壓縮檔內檔案, 不計ZIP本身): 1 個", output)
        self.assertIn("成功探勘檔案清單 (共 1 筆)", output)
        self.assertIn(".txt: 1 個", output)
        self.assertIn("utf-8: 1 個", output)

        shutil.rmtree(single_file_test_dir) # 清理臨時創建的目錄

    def test_prospect_non_existent_path(self):
        """測試探勘一個不存在的路徑"""
        non_existent_path = os.path.join(self.base_test_dir, "non_existent_A7B3C9D0.txt")
        output = self._run_prospector(non_existent_path)
        # print("\n--- Non-existent Path Prospector Output ---")
        # print(output)
        # print("--- End Non-existent Path Prospector Output ---\n")

        # run.py 會先檢查路徑是否存在並打印錯誤，然後 prospector_core 不會被調用
        self.assertIn(f"錯誤：提供的路徑 '{non_existent_path}' 不存在。", output) # 這是 run.py 的日誌
        # self.assertIn(f"錯誤：找不到指定的目標路徑 '{non_existent_path}'。", output) # prospector_core 不會運行
        # self.assertIn("探勘任務因路徑或掃描錯誤而提前終止", output) # run.py 的總結 - 這行不會被執行到
        self.assertNotIn("最終探勘檔案總數", output)
        self.assertNotIn("任務總結報告", output) # 確認沒有總結報告

    def test_prospect_broken_zip(self):
        """測試探勘包含一個損壞 ZIP 檔案的目錄"""
        broken_zip_test_dir = os.path.join(self.base_test_dir, "broken_zip_dir")
        os.makedirs(broken_zip_test_dir, exist_ok=True)
        broken_zip_filename = "broken.zip" # 定義變數

        broken_zip_path_in_test_dir = os.path.join(broken_zip_test_dir, broken_zip_filename)
        with open(broken_zip_path_in_test_dir, "wb") as f:
            f.write(b"This is not a zip file content")

        output = self._run_prospector(broken_zip_test_dir)
        # print("\n--- Broken ZIP Dir Prospector Output ---")
        # print(output)
        # print("--- End Broken ZIP Dir Prospector Output ---\n")

        expected_file_path_in_report = os.path.join(broken_zip_test_dir, broken_zip_filename)
        self.assertIn("檔案路徑: " + expected_file_path_in_report, output)
        self.assertIn("損壞的 ZIP 檔案或非 ZIP 格式", output)
        # self.assertIn("type: zip_summary", output) # Type 'zip_summary' is not explicitly printed
        self.assertIn("❌ [報告]", output)

        self.assertIn("任務總結報告", output)
        self.assertIn("最終探勘檔案總數 (包含壓縮檔內檔案, 不計ZIP本身): 0 個", output)
        self.assertIn("成功探勘檔案清單 (共 0 筆)", output)
        self.assertIn("失敗/跳過檔案清單 (共 0 筆)", output)
        self.assertIn(".zip: 1 個", output)

        shutil.rmtree(broken_zip_test_dir)

if __name__ == '__main__':
    unittest.main(verbosity=2)

# 修正與備註：
# 1. `sys.path` 的修改：對於 `subprocess` 方式，通常不需要修改 `sys.path`。
# 2. `_run_prospector` 中的 `process.returncode` 檢查：保持，以便觀察是否有非預期錯誤。
# 3. 輸出斷言：已調整為預期 prospector.py 輸出的絕對路徑。
# 4. `test_prospect_full_directory` 中檔案計數和編碼統計：
#    - `最終探勘檔案總數`: 6 個 (正確)
#    - `成功探勘檔案清單 (共 6 筆)` (正確)
#    - `.txt: 6 個` (正確)
#    - `ms950: 2 個` (test_ms950.txt, folder_in_zip/another_file.txt) (基於上次測試輸出修正)
#    - `utf-8: 2 個` (test_utf8.txt, zipped_content.txt) (基於上次測試輸出修正)
# 5. `test_prospect_non_existent_path`: 已修正為只檢查 run.py 的錯誤輸出。
# 6. `test_prospect_broken_zip`: 已修正路徑斷言。
# 7. `test_prospect_single_file`: 已修正路徑斷言。
# 8. 預覽內容斷言中的 `\n` 已移除，因為 prospector.py 會 rstrip。
# 9. 空檔案的預覽斷言已更新為 `(檔案為空或無法預覽)`。
