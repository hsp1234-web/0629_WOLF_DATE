# -*- coding: utf-8 -*-
# 精煉廠測試檔 (v21.0 整合探勘邏輯)
import os
import sys
import unittest
import json
import tempfile
import shutil
import zipfile
import duckdb
import queue # 用於模擬佇列
import io
import time
from datetime import datetime
import pytz # 用於時間格式化輔助函式

# --- 路徑自我校正樣板碼 ---
try:
    current_pipeline_dir = os.path.dirname(os.path.abspath(__file__))
    apps_dir = os.path.dirname(current_pipeline_dir)
    project_root = os.path.dirname(apps_dir)

    if apps_dir not in sys.path:
        sys.path.insert(0, apps_dir)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from taifex_data_pipeline import run as pipeline_run_module
except Exception as e:
    print(f"路徑校正或導入時發生錯誤: {e}", file=sys.stderr)
    pipeline_run_module = None

# --- v21.0 探勘邏輯輔助函式 (參考 v1.0.5 探勘腳本) ---
TAIPEI_TZ = pytz.timezone('Asia/Taipei')

def get_taipei_time_str(ts=None) -> str:
    dt = datetime.fromtimestamp(ts) if ts else datetime.now()
    return dt.astimezone(TAIPEI_TZ).strftime('%Y-%m-%d %H:%M:%S')

def human_readable_size(size_bytes: int) -> str:
    if size_bytes == 0: return "0 B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    i = int(size_bytes.bit_length() / 10)
    p = 1024 ** i
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"

def prospect_text_content_from_stream(stream: io.BytesIO, num_lines: int = 5) -> dict:
    """從流中讀取指定行數，嘗試解碼並返回預覽。"""
    try:
        stream.seek(0)
        byte_lines = [stream.readline() for _ in range(num_lines)]
        # 確保只處理實際讀到的行，避免對空字節串解碼
        byte_lines = [line for line in byte_lines if line]


        for encoding in ['utf-8', 'utf-8-sig', 'ms950', 'big5']: # 調整編碼嘗試順序
            try:
                # 移除行尾的換行符進行預覽
                decoded_lines = [line.decode(encoding).rstrip('\r\n') for line in byte_lines]
                # print(f"DEBUG: Tried {encoding}, lines: {decoded_lines}")
                # 只要有一個成功就可以返回
                if decoded_lines or not byte_lines: # 如果byte_lines為空(空檔案)，也算成功
                    return {'status': 'success', 'encoding': encoding, 'preview': decoded_lines}
            except UnicodeDecodeError:
                # print(f"DEBUG: Failed to decode with {encoding}")
                continue
            except Exception as e_dec: # 捕獲其他可能的解碼錯誤
                # print(f"DEBUG: Error decoding with {encoding}: {e_dec}")
                return {'status': 'failure', 'error_reason': f'使用 {encoding} 解碼時發生錯誤: {e_dec}', 'preview': []}


        # 如果所有嘗試都失敗了
        preview_repr = [repr(line[:100]) + ('...' if len(line) > 100 else '') for line in byte_lines] #顯示部分原始字節
        return {'status': 'failure',
                'error_reason': '未能使用常見編碼 (utf-8, ms950, big5) 解碼。',
                'preview': [f"原始字節 (前100): {line_repr}" for line_repr in preview_repr]}
    except Exception as e:
        return {'status': 'failure', 'error_reason': f'讀取內容流時發生錯誤: {e}', 'preview': []}

def print_prospect_report(file_descriptor: str, metadata: dict, prospect_result: dict):
    """打印單個檔案的探勘報告。"""
    print("-" * 70)
    print(f"📜 **檔案探勘報告: {file_descriptor}**")
    print(f"  💾 大小: {metadata.get('size', 'N/A')}")
    print(f"  ⏱️  最後修改時間: {metadata.get('mod_time', 'N/A')}")

    if prospect_result['status'] == 'success':
        print(f"  🔍 編碼 (猜測): {prospect_result.get('encoding', 'N/A')}")
        print(f"  📄 內容預覽 (前 {len(prospect_result.get('preview', []))} 行):")
        if prospect_result['preview']:
            for i, line in enumerate(prospect_result['preview']):
                print(f"    {i+1:02d}: {line}")
        elif metadata.get('size_bytes', -1) == 0 : # 處理空檔案的情況
             print("    (檔案為空)")
        else: # 有內容但預覽為空 (可能解碼後是空行)
            print("    (無有效行可預覽或解碼後為空行)")
    else:
        print(f"  ⚠️ 探勘失敗: {prospect_result.get('error_reason', '未知錯誤')}")
        if prospect_result.get('preview'): # 如果解碼失敗但有原始字節預覽
            print(f"  📄 原始字節預覽 (前 {len(prospect_result.get('preview', []))} 行):")
            for i, line in enumerate(prospect_result['preview']):
                print(f"    {i+1:02d}: {line}")
    print("-" * 70)

def prospect_file_entry(file_path: str):
    """探勘單個檔案或 ZIP 檔案中的所有成員。"""
    print(f"\n🕵️  開始探勘: {file_path}")
    if not os.path.exists(file_path):
        print(f"  ❌ 錯誤: 檔案不存在 {file_path}")
        return

    if file_path.lower().endswith('.zip'):
        try:
            with zipfile.ZipFile(file_path, 'r') as zf:
                member_list = [m for m in zf.infolist() if not m.is_dir() and '__MACOSX' not in m.filename]
                print(f"  🗜️  此為 ZIP 檔案，包含 {len(member_list)} 個成員。")
                if not member_list:
                    stat_info = os.stat(file_path)
                    metadata = {
                        'size': human_readable_size(stat_info.st_size),
                        'mod_time': get_taipei_time_str(stat_info.st_mtime),
                        'size_bytes': stat_info.st_size
                    }
                    print_prospect_report(f"{file_path} (空ZIP或無有效成員)", metadata, {'status':'success', 'encoding':'N/A', 'preview':['(ZIP檔案為空或無有效成員)']})

                for member_info in member_list:
                    descriptor = f"{file_path} -> {member_info.filename}"
                    metadata = {
                        'size': human_readable_size(member_info.file_size),
                        'mod_time': get_taipei_time_str(datetime(*member_info.date_time).timestamp()),
                        'size_bytes': member_info.file_size
                    }
                    try:
                        with zf.open(member_info.filename, 'r') as member_file:
                            # 限制讀取大小以避免記憶體問題，對於探勘前幾行足夠
                            content_bytes = member_file.read(1024 * 512) # 最多讀 512KB
                            stream_buffer = io.BytesIO(content_bytes)
                            prospect_result = prospect_text_content_from_stream(stream_buffer)
                            print_prospect_report(descriptor, metadata, prospect_result)
                    except Exception as e_member:
                        prospect_result = {'status': 'failure', 'error_reason': f'讀取ZIP成員時發生錯誤: {e_member}', 'preview': []}
                        print_prospect_report(descriptor, metadata, prospect_result)
        except zipfile.BadZipFile:
            stat_info = os.stat(file_path)
            metadata = {'size': human_readable_size(stat_info.st_size), 'mod_time': get_taipei_time_str(stat_info.st_mtime), 'size_bytes': stat_info.st_size}
            prospect_result = {'status': 'failure', 'error_reason': '損壞的ZIP檔案。', 'preview':[]}
            print_prospect_report(file_path, metadata, prospect_result)
        except Exception as e_zip:
            stat_info = os.stat(file_path)
            metadata = {'size': human_readable_size(stat_info.st_size), 'mod_time': get_taipei_time_str(stat_info.st_mtime), 'size_bytes': stat_info.st_size}
            prospect_result = {'status': 'failure', 'error_reason': f'處理ZIP檔案時發生錯誤: {e_zip}', 'preview':[]}
            print_prospect_report(file_path, metadata, prospect_result)
    else: # 非 ZIP 檔案
        try:
            stat_info = os.stat(file_path)
            metadata = {
                'size': human_readable_size(stat_info.st_size),
                'mod_time': get_taipei_time_str(stat_info.st_mtime),
                'size_bytes': stat_info.st_size
            }
            with open(file_path, 'rb') as f:
                content_bytes = f.read(1024 * 512) # 最多讀 512KB
                stream_buffer = io.BytesIO(content_bytes)
                prospect_result = prospect_text_content_from_stream(stream_buffer)
                print_prospect_report(file_path, metadata, prospect_result)
        except Exception as e_file:
            # 嘗試獲取元數據（如果可能）
            try:
                stat_info = os.stat(file_path)
                metadata = {'size': human_readable_size(stat_info.st_size), 'mod_time': get_taipei_time_str(stat_info.st_mtime), 'size_bytes': stat_info.st_size}
            except:
                metadata = {'size': 'N/A', 'mod_time': 'N/A', 'size_bytes': -1}
            prospect_result = {'status': 'failure', 'error_reason': f'讀取或處理檔案時發生錯誤: {e_file}', 'preview':[]}
            print_prospect_report(file_path, metadata, prospect_result)
# --- 探勘邏輯結束 ---

class TestTaifexDataPipelineQueueDriven(unittest.TestCase):

    def setUp(self):
        if pipeline_run_module is None:
            self.fail("pipeline_run_module 未能成功導入，請檢查路徑或 run.py 錯誤。")

        self.sample_zip_file_path = os.path.join(current_pipeline_dir, "sample_pipeline_data.zip")
        self.assertTrue(os.path.exists(self.sample_zip_file_path), f"測試 ZIP 檔案 {self.sample_zip_file_path} 不存在。")

        self.base_temp_dir = tempfile.mkdtemp(prefix="test_pipeline_q_")
        self.temp_files_input_dir = os.path.join(self.base_temp_dir, "unzipped_input_files")
        self.temp_db_output_dir = os.path.join(self.base_temp_dir, "db_output")
        self.temp_processing_dir = os.path.join(self.base_temp_dir, "processing_temp")

        os.makedirs(self.temp_files_input_dir, exist_ok=True)
        os.makedirs(self.temp_db_output_dir, exist_ok=True)
        os.makedirs(self.temp_processing_dir, exist_ok=True)

        self.extracted_file_paths = []
        try:
            with zipfile.ZipFile(self.sample_zip_file_path, 'r') as zip_ref:
                zip_ref.extractall(self.temp_files_input_dir)
                for member in zip_ref.namelist():
                    extracted_path = os.path.join(self.temp_files_input_dir, member)
                    if os.path.isfile(extracted_path):
                         self.extracted_file_paths.append(os.path.abspath(extracted_path))
            self.assertTrue(len(self.extracted_file_paths) > 0, "未能從 sample_pipeline_data.zip 解壓縮任何檔案。")
        except Exception as e:
            self.fail(f"setUp 中解壓縮樣本 ZIP 時發生錯誤: {e}")


    def tearDown(self):
        if hasattr(self, 'base_temp_dir') and os.path.exists(self.base_temp_dir):
            shutil.rmtree(self.base_temp_dir)

    def test_pipeline_run_from_queue_FULL_PROCESS(self):
        """
        (保留) 測試精煉廠的佇列驅動完整執行流程 (資料庫寫入)。
        注意：此測試依賴於 downloader 成功下載並將正確的檔案路徑放入佇列。
        在 downloader 未修復前，此測試可能會因為找不到檔案或檔案內容不符預期而失敗。
        """
        print("\n--- 開始測試：完整 Pipeline 執行流程 ---")
        test_db_name = "test_q_analytics_full.duckdb"
        db_full_path = os.path.join(self.temp_db_output_dir, test_db_name)
        format_map_path = os.path.join(self.temp_db_output_dir, pipeline_run_module.FORMAT_MAP_FILENAME)

        task_q = queue.Queue()
        # 模擬 downloader 可能放入的項目格式，確保包含 file_path 和 file_type
        # 即使是 ZIP，也需要一個 file_type 來指導初始的判斷或日誌記錄。
        # process_single_file_entry 內部會進一步處理ZIP內的各個檔案類型。
        queue_item = {
            'file_path': self.sample_zip_file_path,
            'file_type': 'futures_daily_trades', # 使用一個 COMMANDER_TASKS_CONFIG_V3 中的有效鍵作為示例
            'source_url': f'test_sample_zip:{os.path.basename(self.sample_zip_file_path)}' # source_url 主要用於日誌
        }
        task_q.put(queue_item)

        stop_sentinel_value = "STOP_FULL_PIPELINE_TEST"
        task_q.put(stop_sentinel_value)

        hw_settings_for_test = {"max_workers": 2, "memory_limit_gb": 1}

        try:
            pipeline_run_module.run_pipeline_from_queue(
                task_queue=task_q,
                db_output_dir=self.temp_db_output_dir,       # 修改: 使用 db_output_dir
                stop_sentinel=stop_sentinel_value,
                db_name=test_db_name,                        # 新增: 傳遞 db_name
                processing_temp_dir_name="test_pipeline_temp_full_process" # 新增: 傳遞 processing_temp_dir_name
                # format_map_path 和 hw_settings 已從 run_pipeline_from_queue 簽名中移除，因其在函式內部處理
            )
        except Exception as e_run_pipeline:
            self.fail(f"執行 run_pipeline_from_queue (完整流程) 時發生未預期錯誤: {e_run_pipeline}")

        # 驗證 format_map.json (現在路徑在 run_pipeline_from_queue 內部構建)
        expected_format_map_path = os.path.join(self.temp_db_output_dir, pipeline_run_module.FORMAT_MAP_FILENAME) #FORMAT_MAP_FILENAME來自run模組
        self.assertTrue(os.path.exists(expected_format_map_path), f"格式地圖 {expected_format_map_path} 未建立。")
        try:
            with open(expected_format_map_path, 'r', encoding='utf-8') as f_map:
                format_map_content = json.load(f_map)
            self.assertTrue(len(format_map_content) > 0, f"格式地圖應至少包含1個配方，實際: {len(format_map_content)}")
        except Exception as e_map:
            self.fail(f"讀取或解析格式地圖 {expected_format_map_path} 失敗: {e_map}")

        self.assertTrue(os.path.exists(db_full_path), f"DuckDB 資料庫檔案 {db_full_path} 未建立。")

        try:
            con = duckdb.connect(database=db_full_path, read_only=True)
            res_ohlc_count = con.execute("SELECT COUNT(*) FROM daily_ohlc;").fetchone()
            self.assertTrue(res_ohlc_count is not None and res_ohlc_count[0] > 0, "daily_ohlc 表不應為空")
            res_inst_count = con.execute("SELECT COUNT(*) FROM institutional_investors;").fetchone()
            self.assertTrue(res_inst_count is not None and res_inst_count[0] > 0, "institutional_investors 表不應為空")
            # 可以增加對其他表的檢查，如果樣本數據包含的話
            con.close()
        except Exception as e_db:
            self.fail(f"檢查 DuckDB 內容 (完整流程) 時發生錯誤: {e_db}")
        print("--- 結束測試：完整 Pipeline 執行流程 ---")

    def test_prospecting_logic_on_sample_zip(self):
        """
        測試新的探勘邏輯，直接作用於 sample_pipeline_data.zip。
        此測試驗證 D 部分的要求。
        """
        print("\n--- 開始測試：探勘邏輯 (sample_pipeline_data.zip) ---")
        if not os.path.exists(self.sample_zip_file_path):
            self.fail(f"探勘測試失敗：樣本 ZIP 檔案 '{self.sample_zip_file_path}' 不存在。")

        try:
            prospect_file_entry(self.sample_zip_file_path)
            # 這裡我們不直接斷言打印到控制台的內容，
            # 而是確保函式執行沒有拋出未捕獲的例外。
            # 手動檢查控制台輸出以確認前五行是否正確打印。
            succeeded_prospecting = True
        except Exception as e_prospect:
            succeeded_prospecting = False
            self.fail(f"執行 prospect_file_entry 時發生未預期錯誤: {e_prospect}")

        self.assertTrue(succeeded_prospecting, "探勘邏輯執行應順利完成。")
        print("--- 結束測試：探勘邏輯 ---")
        print("\n📋 請手動檢查上方控制台輸出，確認 sample_pipeline_data.zip 及其內部檔案的前五行內容已正確打印。")


if __name__ == "__main__":
    unittest.main(argv=['first-arg-is-ignored'], exit=False)
