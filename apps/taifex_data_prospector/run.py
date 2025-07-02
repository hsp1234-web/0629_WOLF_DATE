# -*- coding: utf-8 -*-
# 偵察兵主執行檔 (v16.0 穩定版本)
import os
import sys
import json
import argparse
import pytz
from datetime import datetime
import io
import zipfile # 確保 zipfile 被導入

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

TAIPEI_TZ = pytz.timezone('Asia/Taipei')

def get_taipei_time_str(ts=None) -> str:
    dt = datetime.fromtimestamp(ts, tz=pytz.utc) if ts else datetime.now(pytz.utc)
    return dt.astimezone(TAIPEI_TZ).strftime('%Y-%m-%d %H:%M:%S')

def human_readable_size(size_bytes: int) -> str:
    if size_bytes == 0: return "0 B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    i = 0
    if size_bytes > 0:
        # 使用 bit_length 來估計量級，避免 log(0)
        i = min(len(size_name) - 1, int(size_bytes.bit_length() / 10) -1 if size_bytes.bit_length() > 10 else 0)
        # 防止 i 為負數，如果 size_bytes 非常小但大於0
        i = max(0,i)


    p = 1024 ** i
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"

def detect_encoding_and_preview(file_path: str, num_lines: int = 5) -> dict:
    preview_lines = []
    detected_encoding = None
    error_message = None
    file_type = 'unknown'
    read_limit_bytes = 4096

    try:
        if not os.path.exists(file_path): # 先檢查檔案是否存在
            return {'encoding': None, 'preview': [], 'error': '檔案不存在。', 'file_type': 'error'}

        with open(file_path, 'rb') as f_bytes:
            initial_bytes = f_bytes.read(read_limit_bytes)
            if not initial_bytes:
                return {'encoding': None, 'preview': [], 'error': '檔案為空', 'file_type': 'empty'}

        if file_path.lower().endswith('.zip'):
            try:
                with open(file_path, 'rb') as full_f_bytes_for_zip:
                    if zipfile.is_zipfile(full_f_bytes_for_zip):
                        full_f_bytes_for_zip.seek(0)
                        with zipfile.ZipFile(full_f_bytes_for_zip, 'r') as zf:
                            member_list = [member.filename for member in zf.infolist()[:num_lines] if not member.is_dir()]
                            preview_lines = [f"壓縮檔成員: {m}" for m in member_list]
                            if not preview_lines and zf.infolist(): # 如果有成員但都是目錄或超過num_lines限制
                                preview_lines = [f"壓縮檔成員 (首個): {zf.infolist()[0].filename} ..."] if zf.infolist() else ["空的或僅含目錄的壓縮檔"]
                            elif not zf.infolist():
                                preview_lines = ["空的壓縮檔"]
                        return {'encoding': 'binary/zip', 'preview': preview_lines, 'error': None, 'file_type': 'zip'}
                    else: # 副檔名是 .zip 但內容不是有效 zip
                        error_message = "檔案副檔名為 .zip 但似乎不是一個有效的 ZIP 檔案。"
                        file_type = 'invalid_zip_header' # 特殊標記
                        # 繼續嘗試作為文字檔案處理，下面會用 initial_bytes
            except zipfile.BadZipFile:
                error_message = "檔案副檔名為 .zip 但無法作為 ZIP 檔案開啟 (BadZipFile)。"
                file_type = 'bad_zip_file'
            except Exception as e_zip:
                error_message = f"嘗試作為 ZIP 檔案處理時發生錯誤: {e_zip}"
                file_type = 'error_processing_zip'

        # 如果不是 ZIP 或 ZIP 處理失敗/標記為可繼續，則嘗試作為文字檔案處理
        if file_type not in ['zip', 'bad_zip_file', 'error_processing_zip'] or file_type == 'invalid_zip_header':
            common_encodings = ['utf-8', 'utf-8-sig', 'ms950', 'big5']
            for enc in common_encodings:
                try:
                    decoded_content = initial_bytes.decode(enc)
                    buffer = io.StringIO(decoded_content)
                    current_preview = []
                    for _ in range(num_lines):
                        line = buffer.readline()
                        if not line: break
                        current_preview.append(line.rstrip('\r\n'))

                    # 如果成功解碼，則這是我們的選擇
                    preview_lines = current_preview
                    detected_encoding = enc
                    file_type = 'text'
                    error_message = None # 清除之前可能的 zip 相關非致命錯誤
                    break
                except UnicodeDecodeError:
                    continue # 嘗試下一個編碼

            if not detected_encoding: # 所有常見編碼都失敗
                if file_type == 'unknown': file_type = 'binary_or_unknown' # 如果之前沒有被 zip 檢測過
                if not error_message : # 避免覆蓋來自 ZIP 判斷的更具體錯誤
                    error_message = "無法使用常見編碼 (utf-8, ms950, big5) 解碼。"
                # 嘗試 latin-1 作為最後手段獲取預覽
                try:
                    decoded_content_latin1 = initial_bytes.decode('latin-1')
                    buffer_latin1 = io.StringIO(decoded_content_latin1)
                    preview_lines_latin1 = []
                    for _ in range(num_lines):
                        line = buffer_latin1.readline()
                        if not line: break
                        preview_lines_latin1.append(line.rstrip('\r\n') + " (latin-1 回退預覽)")
                    preview_lines = preview_lines_latin1 # 使用 latin-1 預覽
                    # detected_encoding 保持 None 或之前的 'binary_or_unknown'
                except Exception: # latin-1 也失敗
                     preview_lines = [repr(initial_bytes[:200]) + "... (原始字節預覽)"] # 預覽部分原始字節

    except FileNotFoundError: # 這個檢查應該在開頭就做了
        error_message = "檔案不存在。"
        file_type = 'error'
    except IOError as e:
        error_message = f"讀取檔案時發生 IO 錯誤: {e}"
        file_type = 'error'
    except Exception as e:
        error_message = f"偵測編碼與預覽時發生未預期錯誤: {e}"
        file_type = 'error'

    return {'encoding': detected_encoding, 'preview': preview_lines, 'error': error_message, 'file_type': file_type}


def prospect_file(file_path: str) -> dict:
    report = {
        'file_path': file_path, 'absolute_path': None, 'size_bytes': None,
        'size_human_readable': None, 'modification_time_utc': None,
        'modification_time_taipei': None, 'encoding': None, 'preview': [],
        'error': None, 'status': 'failure', 'file_type': 'unknown'
    }
    try:
        if not os.path.exists(file_path):
            report['error'] = "檔案不存在。"
            report['file_type'] = 'error' # 確保 file_type 在此情況下為 error
            return report
        if not os.path.isfile(file_path): # v16: 確保是檔案
            report['error'] = "提供的路徑不是一個有效的檔案。"
            report['file_type'] = 'error' # 確保 file_type 在此情況下為 error
            return report

        report['absolute_path'] = os.path.abspath(file_path)
        stat_info = os.stat(file_path)
        report['size_bytes'] = stat_info.st_size
        report['size_human_readable'] = human_readable_size(stat_info.st_size)

        mod_timestamp = stat_info.st_mtime
        report['modification_time_utc'] = datetime.fromtimestamp(mod_timestamp, tz=pytz.utc).isoformat()
        report['modification_time_taipei'] = get_taipei_time_str(mod_timestamp)

        encoding_info = detect_encoding_and_preview(file_path)
        report['encoding'] = encoding_info['encoding']
        report['preview'] = encoding_info['preview']
        report['file_type'] = encoding_info.get('file_type', 'unknown')

        current_encoding_error = encoding_info.get('error')

        if report['file_type'] == 'empty':
            report['error'] = current_encoding_error # "檔案為空"
            report['status'] = 'success'
        elif report['file_type'] == 'zip':
            report['status'] = 'success'
            if current_encoding_error: report['error'] = current_encoding_error
        elif report['file_type'] == 'text':
            report['status'] = 'success'
            if current_encoding_error: report['error'] = current_encoding_error
        elif current_encoding_error:
            error_msg_to_add = f"預覽/編碼錯誤: {current_encoding_error}"
            if report['error']: report['error'] += f"; {error_msg_to_add}"
            else: report['error'] = error_msg_to_add
            report['status'] = 'failure'
        elif not report['error'] and report['file_type'] != 'error':
             report['status'] = 'success'
        else:
            report['status'] = 'failure'
            if not report['error'] and report['file_type'] == 'error': # 從 detect_... 返回的 error
                 report['error'] = "檔案類型偵測返回錯誤狀態。"


    except Exception as e:
        report['error'] = f"探勘檔案時發生未預期錯誤: {str(e)}"
        report['status'] = 'failure'

    return report

def main():
    parser = argparse.ArgumentParser(description="TAIFEX 數據偵察兵：對單一檔案進行快速格式探勘與健康檢查。")
    parser.add_argument("--file-path", required=True, help="要探勘的目標檔案路徑。")
    args = parser.parse_args()

    final_report = prospect_file(args.file_path)

    sys.stdout.reconfigure(encoding='utf-8') # 確保 JSON 能正確輸出中文字符
    print(json.dumps(final_report, ensure_ascii=False, indent=4))

    if final_report['status'] == 'failure':
        sys.exit(1)
    else:
        sys.exit(0)

if __name__ == "__main__":
    main()
