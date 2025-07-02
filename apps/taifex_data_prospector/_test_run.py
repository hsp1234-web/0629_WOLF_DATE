# -*- coding: utf-8 -*-
# 偵察兵測試檔 (v16.0 穩定版本)
import os
import sys
import unittest
import subprocess
import json
import zipfile # 用於創建測試 ZIP

# --- 路徑自我校正樣板碼 ---
try:
    current_dir = os.path.dirname(os.path.abspath(__file__))
    apps_dir = os.path.dirname(current_dir)
    project_root = os.path.dirname(apps_dir)

    if apps_dir not in sys.path:
        sys.path.insert(0, apps_dir)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
except Exception as e:
    print(f"路徑校正時發生錯誤: {e}", file=sys.stderr)
    pass
# --- 路徑自我校正樣板碼結束 ---

class TestTaifexDataProspector(unittest.TestCase):

    def setUp(self):
        """測試設定"""
        self.run_py_script = os.path.join(current_dir, "run.py")
        self.sample_csv_file = os.path.join(current_dir, "sample_test_data.csv") # 已由前一步驟創建
        self.sample_zip_file = os.path.join(current_dir, "sample_test_data.zip") # 已由前一步驟創建
        self.non_existent_file = os.path.join(current_dir, "non_existent_sample.csv")
        self.empty_test_file = os.path.join(current_dir, "empty_test_file.csv")

        # 創建一個空的測試檔案
        with open(self.empty_test_file, 'w') as f:
            pass

        # 再次確認 sample_test_data.csv 和 sample_test_data.zip 存在，以防萬一
        if not os.path.exists(self.sample_csv_file):
             with open(self.sample_csv_file, 'w', encoding='utf-8') as f:
                f.write("Hello,World,123\n這裡是繁體中文,測試,456\nAnother,Line,with,English,and,數字,789\n期貨,選擇權,股票,基金\nTX,MXF,EXF,FXF\n這是第六行\n")
        if not os.path.exists(self.sample_zip_file):
            with zipfile.ZipFile(self.sample_zip_file, 'w') as zf:
                zf.write(self.sample_csv_file, os.path.basename(self.sample_csv_file))


    def tearDown(self):
        """測試清理"""
        if os.path.exists(self.empty_test_file):
            os.remove(self.empty_test_file)
        # 保留 sample_csv_file 和 sample_zip_file，它們是 repo 的一部分

    def _run_prospector(self, file_path_to_prospect):
        command = [sys.executable, self.run_py_script, "--file-path", file_path_to_prospect]
        process = subprocess.run(command, capture_output=True, text=True, encoding='utf-8')
        if process.stderr:
            print(f"執行 prospector 針對 {file_path_to_prospect} 的 stderr:\n{process.stderr}")
        try:
            report = json.loads(process.stdout)
        except json.JSONDecodeError:
            self.fail(f"Prospector 輸出不是有效的 JSON (檔案: {file_path_to_prospect}).\nstdout:\n{process.stdout}\nstderr:\n{process.stderr}")
        return process.returncode, report


    def test_prospect_valid_text_file(self):
        """測試探勘有效的文字 CSV 檔案"""
        return_code, report = self._run_prospector(self.sample_csv_file)
        self.assertEqual(return_code, 0, "探勘有效 CSV 檔案應成功返回 0。")
        self.assertEqual(report["status"], "success", "報告狀態應為 'success'")
        self.assertEqual(os.path.abspath(report["file_path"]), os.path.abspath(self.sample_csv_file))
        self.assertTrue(report["size_bytes"] > 0)
        self.assertIn(report["encoding"], ["utf-8", "utf-8-sig"], f"預期編碼為 utf-8，得到 {report['encoding']}")
        self.assertEqual(len(report["preview"]), 5)
        self.assertEqual(report["preview"][0], "Hello,World,123")
        self.assertEqual(report["file_type"], "text")

    def test_prospect_zip_file(self):
        """測試探勘有效的 ZIP 檔案"""
        return_code, report = self._run_prospector(self.sample_zip_file)
        self.assertEqual(return_code, 0, "探勘 ZIP 檔案應成功返回 0。")
        self.assertEqual(report["status"], "success")
        self.assertEqual(report["file_type"], "zip")
        self.assertEqual(report["encoding"], "binary/zip")
        self.assertTrue(len(report["preview"]) > 0)
        self.assertIn(os.path.basename(self.sample_csv_file), report["preview"][0]) # 預期包含原始檔名

    def test_prospect_empty_file(self):
        """測試探勘空檔案"""
        return_code, report = self._run_prospector(self.empty_test_file)
        # 探勘空檔案本身是成功的操作，run.py 應返回 0
        self.assertEqual(return_code, 0, "探勘空檔案應成功返回 0。")
        self.assertEqual(report["status"], "success", "空檔案的報告狀態應為 'success'")
        self.assertEqual(report["file_type"], "empty")
        self.assertEqual(report["size_bytes"], 0)
        self.assertIn("檔案為空", report["error"]) # error 欄位會註明檔案為空
        self.assertEqual(len(report["preview"]), 0)

    def test_prospect_non_existent_file(self):
        """測試探勘不存在的檔案"""
        return_code, report = self._run_prospector(self.non_existent_file)
        self.assertNotEqual(return_code, 0, "探勘不存在檔案應失敗返回非 0。")
        self.assertEqual(report["status"], "failure")
        self.assertEqual(report["file_type"], "error") # file_type 在 detect_... 中設為 error
        self.assertIn("檔案不存在", report["error"])

if __name__ == "__main__":
    unittest.main(argv=['first-arg-is-ignored'], exit=False)
