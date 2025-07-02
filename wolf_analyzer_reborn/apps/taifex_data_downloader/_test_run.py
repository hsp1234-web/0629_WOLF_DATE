# -*- coding: utf-8 -*-
import unittest
import subprocess
import os
import shutil
import sys
import time

class TestDownloaderRun(unittest.TestCase):
    test_output_dir_name = "temp_test_downloader_output_A1B2C3D4"
    run_script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run.py")

    @classmethod
    def setUpClass(cls):
        """在所有測試開始前，準備環境"""
        cls.base_test_dir = os.path.dirname(os.path.abspath(__file__))
        cls.test_output_path = os.path.join(cls.base_test_dir, cls.test_output_dir_name)
        # 清理可能存在的舊測試目錄
        if os.path.exists(cls.test_output_path):
            shutil.rmtree(cls.test_output_path)
        os.makedirs(cls.test_output_path, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        """在所有測試結束後，清理測試目錄和檔案"""
        if os.path.exists(cls.test_output_path):
            shutil.rmtree(cls.test_output_path)

    def _run_downloader_script(self, start_date: str, end_date: str, download_types: str, output_path:str) -> tuple[str, str, int]:
        """輔助函數：執行 run.py 並返回其標準輸出、標準錯誤和返回碼"""
        python_executable = sys.executable
        cmd = [
            python_executable, self.run_script_path,
            "--start-date", start_date,
            "--end-date", end_date,
            "--download-types", download_types,
            "--output-path", output_path,
            "--delay", "0.1" # 測試時使用較小延遲
        ]
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8'
        )
        stdout, stderr = process.communicate(timeout=180) # 增加超時以應對網路請求

        return stdout, stderr, process.returncode

    def test_download_single_day_pcr(self):
        """測試下載單一日期的 Put/Call Ratio 數據"""
        start_date = "2023-01-03" # 選擇一個交易日
        end_date = "2023-01-03"
        download_types = "put_call_ratio"

        stdout, stderr, returncode = self._run_downloader_script(start_date, end_date, download_types, self.test_output_path)

        # print("\n--- Downloader STDOUT ---")
        # print(stdout)
        # print("--- Downloader STDERR ---")
        # print(stderr)
        # print(f"Return code: {returncode}")
        # print("--- End Downloader Output ---\n")

        self.assertEqual(returncode, 0, f"run.py 執行失敗，錯誤信息:\n{stderr}")

        # 檢查 STDOUT 中是否有成功的日誌 (根據 run.py 的格式)
        self.assertIn("「採集官」下載任務啟動...", stdout)
        self.assertIn(f"類型: {download_types}, 日期: {start_date}, 狀態: success", stdout, "應包含成功下載的日誌")
        self.assertIn("「採集官」下載任務執行完畢。", stdout)

        # 檢查檔案是否已下載
        # 預期檔案路徑： self.test_output_path / B_Market_Sentiment/Put_Call_Ratio / PCRatio_2023_01_03.zip
        expected_folder = os.path.join(self.test_output_path, "B_Market_Sentiment", "Put_Call_Ratio")
        expected_filename = "PCRatio_2023_01_03.zip"
        expected_file_path = os.path.join(expected_folder, expected_filename)

        self.assertTrue(os.path.exists(expected_file_path), f"預期檔案 {expected_file_path} 未被下載。")
        self.assertTrue(os.path.getsize(expected_file_path) > 0, f"預期檔案 {expected_file_path} 大小為0。")

    def test_download_date_range_multiple_types(self):
        """測試下載日期範圍內的多種類型數據，並檢查是否有 'exists' 狀態"""
        # 第一次下載
        start_date = "2023-03-01"
        end_date = "2023-03-02" # 兩天
        # 選擇兩種檔案較小的類型
        download_types = "put_call_ratio,institutional_investors"

        stdout1, stderr1, returncode1 = self._run_downloader_script(start_date, end_date, download_types, self.test_output_path)
        self.assertEqual(returncode1, 0, f"第一次下載執行失敗，錯誤信息:\n{stderr1}")
        self.assertIn(f"類型: put_call_ratio, 日期: {start_date}, 狀態: success", stdout1)
        self.assertIn(f"類型: institutional_investors, 日期: {start_date}, 狀態: success", stdout1)
        self.assertIn(f"類型: put_call_ratio, 日期: {end_date}, 狀態: success", stdout1)
        self.assertIn(f"類型: institutional_investors, 日期: {end_date}, 狀態: success", stdout1)

        # 檢查檔案
        pcr_folder = os.path.join(self.test_output_path, "B_Market_Sentiment", "Put_Call_Ratio")
        inst_folder = os.path.join(self.test_output_path, "B_Market_Sentiment", "Institutional_Investors")

        self.assertTrue(os.path.exists(os.path.join(pcr_folder, "PCRatio_2023_03_01.zip")))
        self.assertTrue(os.path.exists(os.path.join(inst_folder, "3_1_1_2023_03_01.zip")))
        self.assertTrue(os.path.exists(os.path.join(pcr_folder, "PCRatio_2023_03_02.zip")))
        self.assertTrue(os.path.exists(os.path.join(inst_folder, "3_1_1_2023_03_02.zip")))

        # 第二次下載相同內容，預期狀態為 'exists'
        time.sleep(1) # 確保檔案系統時間戳更新（如果需要）
        stdout2, stderr2, returncode2 = self._run_downloader_script(start_date, end_date, download_types, self.test_output_path)
        self.assertEqual(returncode2, 0, f"第二次下載執行失敗，錯誤信息:\n{stderr2}")
        self.assertIn(f"類型: put_call_ratio, 日期: {start_date}, 狀態: exists", stdout2)
        self.assertIn(f"類型: institutional_investors, 日期: {start_date}, 狀態: exists", stdout2)
        self.assertIn(f"類型: put_call_ratio, 日期: {end_date}, 狀態: exists", stdout2)
        self.assertIn(f"類型: institutional_investors, 日期: {end_date}, 狀態: exists", stdout2)


    def test_download_non_trading_day(self):
        """測試下載非交易日（例如週末），預期狀態為 'not_found'"""
        start_date = "2023-01-01"  # 週日
        end_date = "2023-01-01"
        download_types = "futures_daily_trades" # 這種每日生成的通常假日沒有

        stdout, stderr, returncode = self._run_downloader_script(start_date, end_date, download_types, self.test_output_path)
        self.assertEqual(returncode, 0, f"run.py 執行失敗，錯誤信息:\n{stderr}")
        # TAIFEX 在非交易日對某些數據類型會返回一個 HTTP 200 和一個空/小型的 ZIP。
        # 因此，downloader.py 會報告 success。
        self.assertIn(f"類型: {download_types}, 日期: {start_date}, 狀態: success", stdout, "非交易日TAIFEX也可能返回成功和小檔案")

        # 可選：檢查檔案大小是否非常小
        expected_folder = os.path.join(self.test_output_path, "A_Core_Trading", "Futures_Trades")
        expected_filename = f"Daily_{start_date.replace('-', '_')}.zip" # Daily_YYYY_MM_DD.zip
        expected_file_path = os.path.join(expected_folder, expected_filename)
        self.assertTrue(os.path.exists(expected_file_path), "即使是非交易日，也應下載了檔案（可能為空）")
        # 例如，TAIFEX 的空 Daily_YYYY_MM_DD.zip 大約 278 字節
        self.assertTrue(os.path.getsize(expected_file_path) < 1024, "非交易日下載的檔案大小應非常小 (<1KB)")


    def test_invalid_date_format(self):
        """測試無效的日期格式"""
        start_date = "2023/01/01" # 錯誤格式
        end_date = "2023-01-02"
        download_types = "put_call_ratio"

        stdout, stderr, returncode = self._run_downloader_script(start_date, end_date, download_types, self.test_output_path)
        self.assertNotEqual(returncode, 0, "對於無效日期格式，run.py 應以非0狀態碼退出")
        # run.py 中直接用 logger.error 並 sys.exit(1)
        # 所以錯誤訊息主要在 stderr (如果 subprocess Popen text=True, encoding='utf-8'，則 logger 的輸出可能混合在 stdout)
        # 這裡我們檢查 stdout 是否包含 run.py 中 logger.error 的特定訊息
        self.assertIn("錯誤：開始日期或結束日期格式不正確", stdout)


    def test_invalid_download_type(self):
        """測試無效的下載類型"""
        start_date = "2023-01-03"
        end_date = "2023-01-03"
        download_types = "non_existent_type,put_call_ratio" # 包含一個無效類型

        # 確保在運行此測試前，目標檔案（如果存在）被刪除，以避免之前測試的影響
        # 這裡用一個不同的日期以確保隔離性，或者確保清理
        # 為了簡單，我們先確保這個特定檔案的預期路徑是乾淨的
        # 注意：`test_download_single_day_pcr` 會創建 PCRatio_2023_01_03.zip
        # 我們可以選擇一個不同的日期，或者在 setUpClass/tearDownClass 中更細緻地管理，
        # 或者在此測試開始時就刪除這個特定的檔案。

        # 選擇在此處明確刪除此測試將檢查的檔案，以確保測試的獨立性
        check_folder = os.path.join(self.test_output_path, "B_Market_Sentiment", "Put_Call_Ratio")
        check_filename = "PCRatio_2023_01_03.zip" # 與 download_types 中的 put_call_ratio 對應
        check_file_path = os.path.join(check_folder, check_filename)
        if os.path.exists(check_file_path):
            os.remove(check_file_path)
        if os.path.exists(check_folder) and not os.listdir(check_folder): # 如果目錄為空則刪除
             os.rmdir(check_folder)

        stdout, stderr, returncode = self._run_downloader_script(start_date, end_date, download_types, self.test_output_path)
        self.assertNotEqual(returncode, 0, "對於無效下載類型，run.py 應以非0狀態碼退出")
        self.assertIn("錯誤：以下為無效的下載類型: non_existent_type", stdout)

        # 再次確認有效的類型 (put_call_ratio) 沒有被下載
        self.assertFalse(os.path.exists(check_file_path), "無效類型錯誤時，有效類型也不應被下載")


if __name__ == '__main__':
    unittest.main(verbosity=2)
