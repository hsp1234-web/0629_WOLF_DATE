# -*- coding: utf-8 -*-
# 偵察兵測試檔
import os
import sys
import unittest
import subprocess
import json

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
        self.sample_csv_file = os.path.join(current_dir, "sample_test_data.csv")
        self.non_existent_file = os.path.join(current_dir, "non_existent_sample.csv")

        # 確保 sample_test_data.csv 存在
        if not os.path.exists(self.sample_csv_file):
            with open(self.sample_csv_file, 'w', encoding='utf-8') as f:
                f.write("Hello,World,123\n")
                f.write("這裡是繁體中文,測試,456\n")
                f.write("Another,Line,with,English,and,數字,789\n")
                f.write("期貨,選擇權,股票,基金\n")
                f.write("TX,MXF,EXF,FXF\n")
                f.write("這是第六行,不應該出現在預覽中\n")

        # 建立一個假的 ZIP 檔案用於測試
        self.sample_zip_file = os.path.join(current_dir, "sample_test_data.zip")
        import zipfile
        with zipfile.ZipFile(self.sample_zip_file, 'w') as zf:
            zf.writestr("dummy_file_1.txt", "This is content of dummy file 1.")
            zf.writestr("folder/dummy_file_2.csv", "col1,col2\nval1,val2")

    def tearDown(self):
        """測試清理"""
        if os.path.exists(self.sample_zip_file):
            os.remove(self.sample_zip_file)
        # empty_test_file.csv 已在測試中清理，sample_csv_file 保留

    def test_prospect_valid_text_file(self):
        """測試探勘有效的文字 CSV 檔案"""
        command = [sys.executable, self.run_py_script, "--file-path", self.sample_csv_file]
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8')

        # 檢查是否有任何執行錯誤輸出到 stderr
        if result.stderr:
            print(f"執行 run.py 時發生錯誤:\n{result.stderr}")

        self.assertEqual(result.returncode, 0, f"run.py 應成功執行並返回 0。標準誤: {result.stderr}")

        try:
            report = json.loads(result.stdout)
        except json.JSONDecodeError:
            self.fail(f"run.py 的輸出不是有效的 JSON 格式。輸出內容:\n{result.stdout}")

        self.assertEqual(report["status"], "success", "報告狀態應為 'success'")
        self.assertEqual(os.path.abspath(report["file_path"]), os.path.abspath(self.sample_csv_file), "報告中的檔案路徑不正確")
        self.assertTrue(report["size_bytes"] > 0, "檔案大小應大於 0")
        self.assertIsNotNone(report["encoding"], "應偵測到編碼")
        # 檔案是以 UTF-8 儲存的，所以預期偵測結果是 utf-8
        self.assertIn(report["encoding"].lower(), ["utf-8", "utf-8-sig"], f"預期編碼為 utf-8 或 utf-8-sig，但得到 {report['encoding']}")

        self.assertEqual(len(report["preview"]), 5, "預覽內容應有 5 行")

        expected_preview_lines = [
            "Hello,World,123",
            "這裡是繁體中文,測試,456",
            "Another,Line,with,English,and,數字,789",
            "期貨,選擇權,股票,基金",
            "TX,MXF,EXF,FXF"
        ]
        for i in range(5):
            self.assertEqual(report["preview"][i], expected_preview_lines[i], f"預覽第 {i+1} 行內容不符預期")

    def test_prospect_non_existent_file(self):
        """測試探勘不存在的檔案"""
        command = [sys.executable, self.run_py_script, "--file-path", self.non_existent_file]
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8')

        # 對於不存在的檔案，run.py 應返回非 0 的結束碼
        self.assertNotEqual(result.returncode, 0, "run.py 應因檔案不存在而執行失敗 (返回非0)。")

        try:
            report = json.loads(result.stdout)
        except json.JSONDecodeError:
            self.fail(f"run.py 的輸出不是有效的 JSON 格式。輸出內容:\n{result.stdout}")

        self.assertEqual(report["status"], "failure", "報告狀態應為 'failure'")
        self.assertIn("檔案不存在", report["error"], "錯誤訊息應包含 '檔案不存在'")
        self.assertEqual(os.path.abspath(report["file_path"]), os.path.abspath(self.non_existent_file), "報告中的檔案路徑不正確")

    def test_prospect_empty_file(self):
        """測試探勘空檔案"""
        empty_file_path = os.path.join(current_dir, "empty_test_file.csv")
        with open(empty_file_path, 'w', encoding='utf-8') as f:
            pass # 建立空檔案

        command = [sys.executable, self.run_py_script, "--file-path", empty_file_path]
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8')

        if result.stderr:
            print(f"執行 run.py (空檔案測試) 時發生錯誤:\n{result.stderr}")

        # 即使檔案是空的，腳本本身應該成功執行並報告檔案為空
        self.assertEqual(result.returncode, 0, f"run.py 應成功執行並返回 0。標準誤: {result.stderr}")

        try:
            report = json.loads(result.stdout)
        except json.JSONDecodeError:
            self.fail(f"run.py 的輸出不是有效的 JSON 格式。輸出內容:\n{result.stdout}")

        self.assertEqual(report["status"], "success", "空檔案的報告狀態應為 'success'") # 雖然檔案空，但探勘操作本身是成功的
        self.assertEqual(report["size_bytes"], 0, "空檔案的大小應為 0 bytes")
        self.assertIsNone(report["encoding"], "空檔案應無偵測編碼") # 或 report['error'] 包含 "檔案為空"
        self.assertEqual(len(report["preview"]), 0, "空檔案的預覽應為空列表")
        if report.get("error"): # 檢查是否有 "檔案為空" 的錯誤訊息
            self.assertIn("檔案為空", report["error"], "錯誤訊息應提示檔案為空")


        os.remove(empty_file_path) # 清理

    def test_prospect_zip_file(self):
        """測試探勘有效的 ZIP 檔案"""
        command = [sys.executable, self.run_py_script, "--file-path", self.sample_zip_file]
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8')

        if result.stderr:
            print(f"執行 run.py (ZIP 檔案測試) 時發生錯誤:\n{result.stderr}")

        self.assertEqual(result.returncode, 0, f"run.py 應成功執行並返回 0。標準誤: {result.stderr}")

        try:
            report = json.loads(result.stdout)
        except json.JSONDecodeError:
            self.fail(f"run.py 的輸出不是有效的 JSON 格式。輸出內容:\n{result.stdout}")

        self.assertEqual(report["status"], "success", "ZIP 檔案報告狀態應為 'success'")
        self.assertEqual(os.path.abspath(report["file_path"]), os.path.abspath(self.sample_zip_file), "報告中的檔案路徑不正確")
        self.assertTrue(report["size_bytes"] > 0, "ZIP 檔案大小應大於 0")
        self.assertEqual(report["encoding"], "binary/zip", "ZIP 檔案的編碼應為 'binary/zip'")
        self.assertEqual(report["file_type"], "zip", "ZIP 檔案的 file_type 應為 'zip'")

        self.assertTrue(len(report["preview"]) > 0, "ZIP 檔案預覽不應為空")
        # 預期 preview 包含 "압축 내용: dummy_file_1.txt" 和 "압축 내용: folder/dummy_file_2.csv"
        # 順序可能不定，取決於 zipfile.infolist() 的順序
        expected_members = ["압축 내용: dummy_file_1.txt", "압축 내용: folder/dummy_file_2.csv"]

        # 檢查 preview 是否包含所有預期的成員，由於順序不定，轉換為 set 比較
        # 注意：如果 num_lines 限制了 preview 的數量，這裡的斷言需要調整
        # 目前 detect_encoding_and_preview 中的 num_lines 是 5，而我們只有2個成員，所以都會顯示
        self.assertIn(expected_members[0], report["preview"], "ZIP 預覽應包含 dummy_file_1.txt")
        self.assertIn(expected_members[1], report["preview"], "ZIP 預覽應包含 folder/dummy_file_2.csv")


if __name__ == "__main__":
    unittest.main(argv=['first-arg-is-ignored'], exit=False)
