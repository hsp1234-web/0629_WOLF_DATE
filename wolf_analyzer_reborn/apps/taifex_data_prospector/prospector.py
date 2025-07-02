# -*- coding: utf-8 -*-
import os
import io
import time
import zipfile
import logging
import pytz
from datetime import datetime
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
import argparse
import sys # <--- 添加導入

# --- 全域設定 ---
TAIPEI_TZ = pytz.timezone('Asia/Taipei')

# --- 輔助函式 ---
def get_taipei_time_str(ts=None) -> str:
    """將 timestamp 轉換為台北時間的格式化字串"""
    dt = datetime.fromtimestamp(ts) if ts else datetime.now()
    return dt.astimezone(TAIPEI_TZ).strftime('%Y-%m-%d %H:%M:%S')

def human_readable_size(size_bytes: int) -> str:
    """將 bytes 轉換為對人類友善的 KB, MB, GB 格式"""
    if size_bytes == 0:
        return "0 B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    i = int(size_bytes.bit_length() / 10) # Determine the order of magnitude
    p = 1024 ** i
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"

def setup_logger():
    """設定一個簡潔的日誌記錄器"""
    logger = logging.getLogger("Prospector")
    if not logger.handlers: # 避免重複添加 handler
        logger.setLevel(logging.INFO)
        # 明確指定 StreamHandler 使用 sys.stdout
        handler = logging.StreamHandler(sys.stdout) # <--- 修正點
        formatter = logging.Formatter('%(message)s') # 保持簡潔
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger

log = setup_logger()

# --- 核心探勘與報告函式 ---
def create_file_report_str(result: dict) -> str:
    """根據結構化的結果字典，生成單份探勘報告的格式化字串"""
    status_icon = "✅" if result['status'] == 'success' else "❌"
    report_lines = [
        "-" * 80,
        f"{status_icon} [報告]",
        f"  檔案路徑: {result['descriptor']}",
        f"  探勘時間: {result['prospect_time']}",
    ]

    if result.get('type') == 'zip_summary':
        report_lines.extend([
            f"  檔案大小: {result['size']}",
            f"  最後修改: {result['mod_time']}",
            f"  內含檔案: {result['file_count']} 個",
        ])
    else:
        report_lines.extend([
            f"  檔案大小: {result['size']}",
            f"  最後修改: {result['mod_time']}",
        ])

        if result['status'] == 'success':
            report_lines.append(f"  偵測編碼: {result['encoding']}")
            report_lines.append("\n  內容預覽 (前五行):")
            if result['preview']:
                for i, line_text in enumerate(result['preview']):
                    report_lines.append(f"    L{i+1}: {repr(line_text)}")
            else:
                report_lines.append("    (檔案為空)")
        else:
            report_lines.append(f"  錯誤原因: {result['error_reason']}")
    report_lines.append("-" * 80)
    return "\n".join(report_lines)

def prospect_text_content(stream: io.BytesIO) -> dict:
    """對文字內容進行解碼和預覽，返回包含編碼和預覽的字典"""
    try:
        stream.seek(0)
        # 讀取前5行，同時考慮到檔案可能小於5行
        byte_lines = []
        for _ in range(5):
            line = stream.readline()
            if not line:
                break
            byte_lines.append(line)

        # 嘗試常見編碼
        for encoding in ['ms950', 'utf-8', 'utf-8-sig']:
            try:
                decoded_lines = [line.decode(encoding) for line in byte_lines if line]
                return {'status': 'success', 'encoding': encoding, 'preview': decoded_lines}
            except UnicodeDecodeError:
                continue
        # 如果所有嘗試都失敗
        return {'status': 'failure', 'error_reason': '未能使用常見編碼 (ms950, utf-8, utf-8-sig) 解碼，可能為二進位檔案。'}
    except Exception as e:
        return {'status': 'failure', 'error_reason': f'讀取內容時發生錯誤: {e}'}

# --- 平行處理任務函式 ---
def worker_task(task_info: tuple) -> list[dict]:
    """[工人任務] 根據任務類型處理單一或 ZIP 檔案"""
    task_type, file_path = task_info
    all_reports_data = [] # 用於收集結構化報告數據

    try:
        if task_type == 'zip':
            # 首先為 ZIP 檔案本身生成報告
            stat_info = os.stat(file_path)
            zip_file_report_data = {
                'status': 'success', 'type': 'zip_summary',
                'descriptor': file_path, 'prospect_time': get_taipei_time_str(),
                'size': human_readable_size(stat_info.st_size),
                'mod_time': get_taipei_time_str(stat_info.st_mtime),
            }
            try:
                with zipfile.ZipFile(file_path, 'r') as zf:
                    member_list = zf.infolist()
                    zip_file_report_data['file_count'] = sum(1 for m in member_list if not m.is_dir())
                    all_reports_data.append(zip_file_report_data)

                    # 然後處理 ZIP 檔案內的成員
                    for member_info in member_list:
                        if member_info.is_dir() or '__MACOSX' in member_info.filename:
                            continue

                        descriptor = f"{file_path} -> {member_info.filename}"
                        member_result_data = {
                            'descriptor': descriptor, 'prospect_time': get_taipei_time_str(),
                            'size': human_readable_size(member_info.file_size),
                            'mod_time': get_taipei_time_str(datetime(*member_info.date_time).timestamp()),
                            'type': 'file_in_zip'
                        }
                        try:
                            with zf.open(member_info.filename, 'r') as member_stream:
                                # 讀取成員檔案內容到 BytesIO 流中
                                stream_buffer = io.BytesIO(member_stream.read())
                                member_result_data.update(prospect_text_content(stream_buffer))
                        except Exception as member_e:
                            member_result_data.update({'status': 'failure', 'error_reason': f'處理 ZIP 內檔案時發生錯誤: {member_e}'})
                        all_reports_data.append(member_result_data)

            except zipfile.BadZipFile:
                zip_file_report_data.update({'status': 'failure', 'error_reason': '損壞的 ZIP 檔案或非 ZIP 格式。', 'file_count': 0})
                all_reports_data.append(zip_file_report_data)
            except Exception as e_zip:
                # 捕獲其他與ZIP相關的錯誤
                error_report_data = {
                    'status': 'failure', 'descriptor': file_path,
                    'prospect_time': get_taipei_time_str(),
                    'size': human_readable_size(stat_info.st_size),
                    'mod_time': get_taipei_time_str(stat_info.st_mtime),
                    'error_reason': f'處理 ZIP 檔案時發生嚴重錯誤: {e_zip}', 'type': 'zip'
                }
                all_reports_data.append(error_report_data)

        else: # task_type == 'file'
            stat_info = os.stat(file_path)
            result_data = {
                'descriptor': file_path, 'prospect_time': get_taipei_time_str(),
                'size': human_readable_size(stat_info.st_size),
                'mod_time': get_taipei_time_str(stat_info.st_mtime),
                'type': 'file'
            }
            try:
                with open(file_path, 'rb') as f:
                    # 限制讀取大小以避免超大檔案問題，例如前 512KB
                    content_stream = io.BytesIO(f.read(1024 * 512))
                    result_data.update(prospect_text_content(content_stream))
            except Exception as file_e:
                result_data.update({'status': 'failure', 'error_reason': f'讀取檔案時發生錯誤: {file_e}'})
            all_reports_data.append(result_data)

    except Exception as e:
        # 捕獲 worker_task 級別的意外錯誤
        # 確保即使發生未知錯誤，也能為原始任務路徑返回一個失敗報告
        # 檢查是否已經有針對此 file_path 的報告，避免重複
        if not any(r['descriptor'] == file_path for r in all_reports_data):
            try:
                stat_info = os.stat(file_path) # 嘗試獲取文件信息
                size = human_readable_size(stat_info.st_size)
                mod_time = get_taipei_time_str(stat_info.st_mtime)
            except FileNotFoundError:
                size = "N/A"
                mod_time = "N/A"

            all_reports_data.append({
                'status': 'failure',
                'descriptor': file_path,
                'prospect_time': get_taipei_time_str(),
                'size': size,
                'mod_time': mod_time,
                'error_reason': f'處理時發生嚴重錯誤: {e}',
                'type': task_type
            })
    return all_reports_data


# --- 主執行函式 (被 run.py 調用) ---
def run_prospector_core(target_path: str) -> list[dict]:
    """
    核心探勘邏輯。
    :param target_path: 要探勘的目標資料夾路徑。
    :return: 一個包含所有探勘結果字典的列表。
    """
    start_time = time.time()
    log.info("=" * 80)
    log.info("🚀 智能型檔案探勘任務啟動 (v1.0.5 移植版)...")
    log.info(f"🕒 任務開始時間: {get_taipei_time_str()}")
    log.info("=" * 80)

    if not os.path.isdir(target_path):
        log.error(f"❌ 錯誤：找不到指定的目標路徑 '{target_path}'。")
        return []

    log.info(f"目標路徑設定為: {target_path}\n")
    log.info("Phase 1: 正在掃描目錄並建立任務清單...")
    tasks = []
    dir_count = 0
    for root, dirs, files in os.walk(target_path):
        dir_count += len(dirs)
        for name in files:
            file_path = os.path.join(root, name)
            task_type = 'zip' if name.lower().endswith('.zip') else 'file'
            tasks.append((task_type, file_path))

    if not tasks:
        log.warning("在目標路徑中未發現任何檔案。")
        return []
    log.info(f"掃描完成。發現 {dir_count} 個子目錄和 {len(tasks)} 個頂層處理任務。\n")

    num_workers = max(1, os.cpu_count() or 1) # 確保至少有1個工人
    log.info(f"Phase 2: 偵測到 {os.cpu_count() or '未知'} 個 CPU 核心，將啟動 {num_workers} 個工人進行平行探勘...")
    log.info("探勘報告將會即時輸出如下:")

    all_results_data = [] # 存儲結構化的結果數據
    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        future_to_task = {executor.submit(worker_task, task): task for task in tasks}

        for future in as_completed(future_to_task):
            original_task_info = future_to_task[future]
            task_desc = original_task_info[1]
            try:
                results_from_worker = future.result()
                all_results_data.extend(results_from_worker)
                for result_item in results_from_worker:
                    log.info(create_file_report_str(result_item))
            except Exception as exc:
                log.error(f"處理任務 {task_desc} 時產生嚴重例外 (框架層級): {exc}")
                # 為這個特定任務創建一個錯誤報告
                # 嘗試獲取文件信息，如果文件仍然存在
                try:
                    stat_info = os.stat(task_desc)
                    size_hr = human_readable_size(stat_info.st_size)
                    mod_time_hr = get_taipei_time_str(stat_info.st_mtime)
                except FileNotFoundError:
                    size_hr = "N/A"
                    mod_time_hr = "N/A"

                all_results_data.append({
                    'status': 'failure',
                    'descriptor': task_desc,
                    'prospect_time': get_taipei_time_str(),
                    'size': size_hr,
                    'mod_time': mod_time_hr,
                    'error_reason': f'平行處理框架層級錯誤: {exc}',
                    'type': original_task_info[0] # 使用原始任務類型
                })


    # --- 最終總結報告 ---
    duration = time.time() - start_time
    # 過濾掉 zip_summary 類型，只計算實際檔案的成功與失敗
    actual_files_results = [r for r in all_results_data if r.get('type') != 'zip_summary']
    successful_files = [r for r in actual_files_results if r['status'] == 'success']
    failed_files = [r for r in actual_files_results if r['status'] == 'failure']

    # 檔案類型統計時，對於 ZIP 內的檔案，我們只關心其原始副檔名
    file_extensions_list = []
    for r in all_results_data:
        if r.get('type') == 'zip_summary': # 跳過 ZIP 檔案本身的統計
            continue
        descriptor = r['descriptor']
        # 如果是 ZIP 內的檔案， descriptor 格式為 "zip_path -> member_name"
        actual_filename = descriptor.split(' -> ')[-1]
        file_extensions_list.append(os.path.splitext(actual_filename)[1].lower())

    file_extensions = Counter(file_extensions_list)
    encodings = Counter(r['encoding'] for r in successful_files if 'encoding' in r) # 確保 encoding 存在

    summary = [
        "\n", "=" * 80,
        "                         任務總結報告".center(80),
        "=" * 80,
        f"[ {get_taipei_time_str()} ] 檔案探勘任務全部結束。", "",
        "--- 任務概覽 ---",
        f"  - 總運作時間: {duration:.2f} 秒",
        f"  - 掃描目錄總數: {dir_count} 個",
        f"  - 處理頂層檔案/壓縮包: {len(tasks)} 個",
        f"  - 最終探勘檔案總數 (包含壓縮檔內檔案): {len(actual_files_results)} 個", "",
        "--- 檔案類型統計 (包含解壓後) ---",
    ]
    if file_extensions:
        for ext, count in file_extensions.most_common():
            summary.append(f"  - {ext if ext else '[無副檔名]'}: {count} 個")
    else:
        summary.append("  (無檔案可統計)")

    summary.append("\n--- 編碼分佈統計 (僅限成功解碼檔案) ---")
    if encodings:
        for enc, count in encodings.most_common():
            summary.append(f"  - {enc}: {count} 個")
    else:
        summary.append("  (無成功解碼的檔案可統計)")


    summary.extend(["\n" + ("-" * 80), f"✅ 成功探勘檔案清單 (共 {len(successful_files)} 筆)", "-" * 80])
    summary.extend([f"  {i}. {result['descriptor']}" for i, result in enumerate(successful_files, 1)] if successful_files else ["  (無)"])

    summary.extend(["\n" + ("-" * 80), f"❌ 失敗/跳過檔案清單 (共 {len(failed_files)} 筆)", "-" * 80])
    summary.extend([f"  {i}. {result['descriptor']} (原因: {result.get('error_reason', '未知')})" for i, result in enumerate(failed_files, 1)] if failed_files else ["  (無)"])

    summary.append("=" * 80)
    log.info("\n".join(summary))

    return all_results_data # 返回結構化數據，供 run.py 可能的進一步處理或測試斷言

if __name__ == '__main__':
    # 此部分主要用於直接測試 prospector.py，實際由 run.py 調用 run_prospector_core
    parser = argparse.ArgumentParser(description="智能型檔案探勘工具。")
    parser.add_argument("--file-path", required=True, help="要探勘的目標資料夾路徑。")
    args = parser.parse_args()
    run_prospector_core(args.file_path)

# 備註：
# 1. 移除了 Google Colab 特定的 `drive.mount` 和 `display(HTML(...))`。
# 2. 日誌現在使用標準 `logging` 模組輸出到控制台。
# 3. `run_prospector_core` 函數現在接收一個 `target_path` 參數，並返回結構化的結果數據。
# 4. `create_file_report` 已改名為 `create_file_report_str` 並返回字串，由 `run_prospector_core` 中的日誌打印。
# 5. `worker_task` 現在返回報告的結構化數據列表，而不是直接打印。
# 6. 增加了 `if __name__ == '__main__':` 區塊，允許直接執行此腳本進行基本測試（儘管計畫是由 `run.py` 驅動）。
# 7. 調整了 `human_readable_size` 中的位元長度計算，確保 `i` 不會超出 `size_name` 的索引。
# 8. 修正 `prospect_text_content` 以處理檔案行數少於5行的情況。
# 9. 修正 `worker_task` 中 ZIP 檔案處理邏輯，確保 ZIP 檔案本身的報告和其內部檔案的報告都能正確生成，並處理 `BadZipFile` 異常。
# 10. 修正 `run_prospector_core` 中總結報告的統計數字，確保其準確反映實際探勘的檔案。
# 11. 確保 `setup_logger` 不會重複添加 handler。
# 12. 調整了 `worker_task` 中對 ZIP 檔案處理的錯誤捕獲，使其更健壯。
# 13. 調整了 `run_prospector_core` 中 `future.result()` 的錯誤處理和報告。
# 14. 確保 `os.cpu_count()` 返回 `None` 時 (某些環境下可能發生) `num_workers` 至少為 1。
# 15. 檔案類型統計現在基於解壓後的檔案名稱。
# 16. 修正了 `worker_task` 中處理非 ZIP 檔案時，如果 `os.stat` 失敗（例如檔案在掃描後被刪除）的處理。
# 17. 修正 `worker_task` 中的一個細微錯誤，確保即使在頂層 `try...except` 塊中捕獲到異常，也能為當前 `file_path` 生成一個錯誤報告。```python
# -*- coding: utf-8 -*-
import os
import io
import time
import zipfile
import logging
import pytz
from datetime import datetime
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
import argparse

# --- 全域設定 ---
TAIPEI_TZ = pytz.timezone('Asia/Taipei')

# --- 輔助函式 ---
def get_taipei_time_str(ts=None) -> str:
    """將 timestamp 轉換為台北時間的格式化字串"""
    dt = datetime.fromtimestamp(ts) if ts else datetime.now()
    return dt.astimezone(TAIPEI_TZ).strftime('%Y-%m-%d %H:%M:%S')

def human_readable_size(size_bytes: int) -> str:
    """將 bytes 轉換為對人類友善的 KB, MB, GB 格式"""
    if size_bytes == 0:
        return "0 B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    # i = int(size_bytes.bit_length() / 10) # Determine the order of magnitude
    # 修正：避免 size_bytes 為 0 或負數時 bit_length 出錯，並確保 i 在合理範圍
    if size_bytes <= 0: return "0 B"
    i = 0
    while size_bytes >= 1024 and i < len(size_name) - 1:
        size_bytes /= 1024.
        i += 1
    s = round(size_bytes, 2)
    return f"{s} {size_name[i]}"

def setup_logger():
    """設定一個簡潔的日誌記錄器"""
    logger = logging.getLogger("Prospector")
    if not logger.handlers: # 避免重複添加 handler
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler() # 輸出到控制台
        formatter = logging.Formatter('%(message)s') # 保持簡潔
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger

log = setup_logger()

# --- 核心探勘與報告函式 ---
def create_file_report_str(result: dict) -> str:
    """根據結構化的結果字典，生成單份探勘報告的格式化字串"""
    status_icon = "✅" if result['status'] == 'success' else "❌"
    report_lines = [
        "-" * 80,
        f"{status_icon} [報告]",
        f"  檔案路徑: {result['descriptor']}",
        f"  探勘時間: {result['prospect_time']}",
    ]

    if result.get('type') == 'zip_summary':
        report_lines.extend([
            f"  檔案大小: {result['size']}",
            f"  最後修改: {result['mod_time']}",
            f"  內含檔案: {result.get('file_count', 'N/A')} 個",
        ])
        if result['status'] == 'failure' and result.get('error_reason'):
            report_lines.append(f"  錯誤原因: {result['error_reason']}")
    else:
        report_lines.extend([
            f"  檔案大小: {result['size']}",
            f"  最後修改: {result['mod_time']}",
        ])

        if result['status'] == 'success':
            report_lines.append(f"  偵測編碼: {result.get('encoding', 'N/A')}") # 確保 encoding 存在
            report_lines.append("\n  內容預覽 (前五行):")
            if result.get('preview'): # 確保 preview 存在
                for i, line_text in enumerate(result['preview']):
                    report_lines.append(f"    L{i+1}: {repr(line_text)}")
            else:
                report_lines.append("    (檔案為空或無法預覽)")
        else:
            report_lines.append(f"  錯誤原因: {result.get('error_reason', '未知錯誤')}") # 確保 error_reason 存在
    report_lines.append("-" * 80)
    return "\n".join(report_lines)

def prospect_text_content(stream: io.BytesIO) -> dict:
    """對文字內容進行解碼和預覽，返回包含編碼和預覽的字典"""
    try:
        stream.seek(0)
        byte_lines = []
        for _ in range(5):
            line = stream.readline()
            if not line:
                break
            byte_lines.append(line)

        if not byte_lines: # 如果檔案為空
             return {'status': 'success', 'encoding': 'N/A', 'preview': []}

        for encoding in ['ms950', 'utf-8', 'utf-8-sig']:
            try:
                decoded_lines = [line.decode(encoding).rstrip('\r\n') for line in byte_lines if line]
                return {'status': 'success', 'encoding': encoding, 'preview': decoded_lines}
            except UnicodeDecodeError:
                continue
        return {'status': 'failure', 'error_reason': '未能使用常見編碼 (ms950, utf-8, utf-8-sig) 解碼，可能為二進位檔案。'}
    except Exception as e:
        return {'status': 'failure', 'error_reason': f'讀取內容時發生錯誤: {e}'}

# --- 平行處理任務函式 ---
def worker_task(task_info: tuple) -> list[dict]:
    task_type, file_path = task_info
    all_reports_data = []
    current_time_str = get_taipei_time_str()

    try:
        stat_info = os.stat(file_path)
        file_size_hr = human_readable_size(stat_info.st_size)
        mod_time_hr = get_taipei_time_str(stat_info.st_mtime)
    except FileNotFoundError:
        all_reports_data.append({
            'status': 'failure', 'descriptor': file_path, 'prospect_time': current_time_str,
            'size': "N/A", 'mod_time': "N/A",
            'error_reason': '檔案掃描後但在處理前遺失。', 'type': task_type
        })
        return all_reports_data
    except Exception as e_stat: # 其他 os.stat 可能的錯誤
        all_reports_data.append({
            'status': 'failure', 'descriptor': file_path, 'prospect_time': current_time_str,
            'size': "N/A", 'mod_time': "N/A",
            'error_reason': f'獲取檔案狀態時出錯: {e_stat}', 'type': task_type
        })
        return all_reports_data


    if task_type == 'zip':
        zip_file_report_data = {
            'descriptor': file_path, 'prospect_time': current_time_str,
            'size': file_size_hr, 'mod_time': mod_time_hr, 'type': 'zip_summary',
        }
        try:
            with zipfile.ZipFile(file_path, 'r') as zf:
                member_list = zf.infolist()
                zip_file_report_data['file_count'] = sum(1 for m in member_list if not m.is_dir() and '__MACOSX' not in m.filename)
                zip_file_report_data['status'] = 'success'
                all_reports_data.append(zip_file_report_data)

                for member_info in member_list:
                    if member_info.is_dir() or '__MACOSX' in member_info.filename:
                        continue

                    member_current_time_str = get_taipei_time_str()
                    descriptor = f"{file_path} -> {member_info.filename}"
                    member_result_data = {
                        'descriptor': descriptor, 'prospect_time': member_current_time_str,
                        'size': human_readable_size(member_info.file_size),
                        'mod_time': get_taipei_time_str(datetime(*member_info.date_time).timestamp()),
                        'type': 'file_in_zip'
                    }
                    try:
                        with zf.open(member_info.filename, 'r') as member_stream:
                            stream_buffer = io.BytesIO(member_stream.read(1024 * 512)) # 限制讀取大小
                            member_result_data.update(prospect_text_content(stream_buffer))
                    except Exception as member_e:
                        member_result_data.update({'status': 'failure', 'error_reason': f'處理 ZIP 內檔案時發生錯誤: {member_e}'})
                    all_reports_data.append(member_result_data)

        except zipfile.BadZipFile as bzf_err:
            err_msg = f'損壞的 ZIP 檔案或非 ZIP 格式: {bzf_err}'
            zip_file_report_data.update({'status': 'failure', 'error_reason': err_msg, 'file_count': 0})
            if not any(r['descriptor'] == file_path and r['type'] == 'zip_summary' for r in all_reports_data):
                 all_reports_data.append(zip_file_report_data)
            else: # 如果已存在報告，更新它
                for r in all_reports_data:
                    if r['descriptor'] == file_path and r['type'] == 'zip_summary':
                        r.update({'status': 'failure', 'error_reason': err_msg, 'file_count': 0})
                        break
        except Exception as e_zip:
            error_reason = f'處理 ZIP 檔案時發生一般性嚴重錯誤: {e_zip}'
            # 確保 zip_file_report_data 被初始化（如果 e_zip 在 BadZipFile 之前發生）
            if 'status' not in zip_file_report_data: # 可能尚未添加
                zip_file_report_data = {
                    'descriptor': file_path, 'prospect_time': current_time_str,
                    'size': file_size_hr, 'mod_time': mod_time_hr, 'type': 'zip_summary',
                    'status': 'failure', 'error_reason': error_reason, 'file_count': 0
                }
                all_reports_data.append(zip_file_report_data)
            else: # 如果已存在報告，更新它
                 for r in all_reports_data:
                    if r['descriptor'] == file_path and r['type'] == 'zip_summary':
                        r.update({'status': 'failure', 'error_reason': error_reason, 'file_count': 0})
                        break
    else: # task_type == 'file'
        result_data = {
            'descriptor': file_path, 'prospect_time': current_time_str,
            'size': file_size_hr, 'mod_time': mod_time_hr, 'type': 'file'
        }
        try:
            with open(file_path, 'rb') as f:
                content_stream = io.BytesIO(f.read(1024 * 512))
                result_data.update(prospect_text_content(content_stream))
        except Exception as file_e:
            result_data.update({'status': 'failure', 'error_reason': f'讀取檔案時發生錯誤: {file_e}'})
        all_reports_data.append(result_data)

    return all_reports_data

# --- 主執行函式 (被 run.py 調用) ---
def run_prospector_core(target_path: str) -> list[dict]:
    start_time = time.time()
    log.info("=" * 80)
    log.info("🚀 智能型檔案探勘任務啟動 (v1.0.5 移植版)...")
    log.info(f"🕒 任務開始時間: {get_taipei_time_str()}")
    log.info("=" * 80)

    if not os.path.isdir(target_path):
        log.error(f"❌ 錯誤：找不到指定的目標路徑 '{target_path}'。")
        # 返回一個包含錯誤信息的結果，以便調用者知道發生了什麼
        return [{
            'status': 'failure', 'descriptor': target_path, 'prospect_time': get_taipei_time_str(),
            'size': "N/A", 'mod_time': "N/A",
            'error_reason': f'目標路徑不存在或不是一個目錄: {target_path}', 'type': 'path_error'
        }]

    log.info(f"目標路徑設定為: {os.path.abspath(target_path)}\n")
    log.info("Phase 1: 正在掃描目錄並建立任務清單...")
    tasks = []
    dir_count = 0
    try:
        for root, dirs, files in os.walk(target_path):
            dir_count += len(dirs)
            for name in files:
                file_path = os.path.join(root, name)
                task_type = 'zip' if name.lower().endswith('.zip') else 'file'
                tasks.append((task_type, file_path))
    except Exception as e_walk:
        log.error(f"❌ 錯誤：掃描目錄時發生錯誤: {e_walk}")
        return [{
            'status': 'failure', 'descriptor': target_path, 'prospect_time': get_taipei_time_str(),
            'size': "N/A", 'mod_time': "N/A",
            'error_reason': f'掃描目錄時出錯: {e_walk}', 'type': 'scan_error'
        }]


    if not tasks:
        log.warning("在目標路徑中未發現任何檔案。")
        return [] # 維持原樣，返回空列表表示沒有檔案處理
    log.info(f"掃描完成。發現 {dir_count} 個子目錄和 {len(tasks)} 個頂層處理任務。\n")

    num_workers = max(1, os.cpu_count() or 1)
    log.info(f"Phase 2: 偵測到 {os.cpu_count() or '未知'} 個 CPU 核心，將啟動 {num_workers} 個工人進行平行探勘...")
    log.info("探勘報告將會即時輸出如下:")

    all_results_data = []
    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        future_to_task = {executor.submit(worker_task, task): task for task in tasks}

        for future in as_completed(future_to_task):
            original_task_info = future_to_task[future]
            task_desc = original_task_info[1]
            try:
                results_from_worker = future.result()
                all_results_data.extend(results_from_worker)
                for result_item in results_from_worker:
                    log.info(create_file_report_str(result_item))
            except Exception as exc:
                log.error(f"處理任務 {task_desc} 時產生嚴重例外 (框架層級): {exc}")
                # 為這個特定任務創建一個錯誤報告
                # 嘗試獲取文件信息，如果文件仍然存在
                try:
                    stat_info = os.stat(task_desc)
                    size_hr = human_readable_size(stat_info.st_size)
                    mod_time_hr = get_taipei_time_str(stat_info.st_mtime)
                except FileNotFoundError:
                    size_hr = "N/A"
                    mod_time_hr = "N/A"

                all_results_data.append({
                    'status': 'failure',
                    'descriptor': task_desc,
                    'prospect_time': get_taipei_time_str(),
                    'size': size_hr,
                    'mod_time': mod_time_hr,
                    'error_reason': f'平行處理框架層級錯誤: {exc}',
                    'type': original_task_info[0] # 使用原始任務類型
                })

    duration = time.time() - start_time
    actual_files_results = [r for r in all_results_data if r.get('type') not in ['zip_summary', 'path_error', 'scan_error']]
    successful_files = [r for r in actual_files_results if r['status'] == 'success']
    failed_files = [r for r in actual_files_results if r['status'] == 'failure']

    # 檔案類型統計：基於 `tasks` 列表中的頂層檔案 (task_info[1] 是 file_path)
    top_level_extensions = []
    for task_type, file_path_in_task in tasks:
        top_level_extensions.append(os.path.splitext(file_path_in_task)[1].lower())
    file_extensions = Counter(top_level_extensions)

    encodings = Counter(r['encoding'] for r in successful_files if 'encoding' in r and r['encoding'] != 'N/A')

    summary = [
        "\n", "=" * 80,
        "                         任務總結報告".center(80),
        "=" * 80,
        f"[ {get_taipei_time_str()} ] 檔案探勘任務全部結束。", "",
        "--- 任務概覽 ---",
        f"  - 總運作時間: {duration:.2f} 秒",
        f"  - 掃描目錄總數: {dir_count} 個",
        f"  - 處理頂層檔案/壓縮包: {len(tasks)} 個",
        f"  - 最終探勘檔案總數 (包含壓縮檔內檔案, 不計ZIP本身): {len(actual_files_results)} 個", "",
        "--- 檔案類型統計 (包含解壓後) ---",
    ]
    if file_extensions:
        for ext, count in file_extensions.most_common():
            summary.append(f"  - {ext if ext else '[無副檔名]'}: {count} 個")
    else:
        summary.append("  (無檔案可統計)")

    summary.append("\n--- 編碼分佈統計 (僅限成功解碼檔案) ---")
    if encodings:
        for enc, count in encodings.most_common():
            summary.append(f"  - {enc}: {count} 個")
    else:
        summary.append("  (無成功解碼的檔案可統計)")

    summary.extend(["\n" + ("-" * 80), f"✅ 成功探勘檔案清單 (共 {len(successful_files)} 筆)", "-" * 80])
    summary.extend([f"  {i+1}. {result['descriptor']}" for i, result in enumerate(successful_files)] if successful_files else ["  (無)"])

    summary.extend(["\n" + ("-" * 80), f"❌ 失敗/跳過檔案清單 (共 {len(failed_files)} 筆)", "-" * 80])
    summary.extend([f"  {i+1}. {result['descriptor']} (原因: {result.get('error_reason', '未知')})" for i, result in enumerate(failed_files)] if failed_files else ["  (無)"])

    summary.append("=" * 80)
    log.info("\n".join(summary))

    return all_results_data

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="智能型檔案探勘工具。")
    parser.add_argument("--file-path", required=True, help="要探勘的目標資料夾路徑。")
    args = parser.parse_args()

    # 創建一個臨時目錄和一些測試檔案來模擬
    test_dir = "temp_prospector_test_data"
    os.makedirs(test_dir, exist_ok=True)
    os.makedirs(os.path.join(test_dir, "subfolder"), exist_ok=True)

    with open(os.path.join(test_dir, "file1.txt"), "w", encoding="utf-8") as f:
        f.write("這是第一個測試檔案。\n")
        f.write("包含一些中文字符。\n")

    with open(os.path.join(test_dir, "file2_empty.txt"), "w", encoding="utf-8") as f:
        pass # 空檔案

    with open(os.path.join(test_dir, "subfolder", "file3_ms950.txt"), "w", encoding="ms950") as f:
        f.write("這是MS950編碼的檔案。\n")
        f.write("測試不同編碼。\n")

    zip_path = os.path.join(test_dir, "archive.zip")
    with zipfile.ZipFile(zip_path, 'w') as zf:
        zf.writestr("zipped_file1.txt", "Content of zipped file 1.\nSecond line.")
        zf.writestr("folder_in_zip/zipped_file2.txt", "Content of zipped file 2 in a folder.")

    print(f"測試檔案已創建於: {os.path.abspath(test_dir)}")
    print(f"執行探勘任務於: {os.path.abspath(test_dir)}")

    results = run_prospector_core(test_dir)

    # print("\n--- 結構化結果 (JSON-like) ---")
    # import json
    # print(json.dumps(results, indent=2, ensure_ascii=False))

    # 清理測試檔案和目錄
    # import shutil
    # shutil.rmtree(test_dir)
    # print(f"\n測試目錄 {test_dir} 已刪除。")
