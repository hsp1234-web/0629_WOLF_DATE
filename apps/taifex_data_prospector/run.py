# -*- coding: utf-8 -*-
# 偵察兵主執行檔
import os
import sys
import json
import argparse
import pytz
from datetime import datetime
import io

# --- 路徑自我校正樣板碼 ---
# 確保腳本在任何位置執行時，都能正確找到專案根目錄並將 'apps' 加入 sys.path
try:
    # 嘗試定位 'apps' 資料夾，假設此腳本位於 apps/some_app/run.py
    current_dir = os.path.dirname(os.path.abspath(__file__))
    apps_dir = os.path.dirname(current_dir)  # 退回到 'apps' 層級
    project_root = os.path.dirname(apps_dir) # 退回到專案根目錄層級

    if apps_dir not in sys.path:
        sys.path.insert(0, apps_dir)
    if project_root not in sys.path: # 確保根目錄也在 sys.path 中，方便存取其他頂層模組 (如果有的話)
        sys.path.insert(0, project_root)
except Exception as e:
    print(f"路徑校正時發生錯誤: {e}", file=sys.stderr)
    # 在某些情況下，例如直接在頂層執行，可能不需要校正，或者校正邏輯需要調整
    # 這裡可以根據實際的專案結構和執行方式進行調整
    pass
# --- 路徑自我校正樣板碼結束 ---

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
    i = int(size_bytes.bit_length() / 10) # int(math.log(size_bytes, 1024)) if size_bytes > 0 else 0
    if i >= len(size_name): i = len(size_name) -1 # 避免超出範圍
    p = 1024 ** i
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"

def detect_encoding_and_preview(file_path: str, num_lines: int = 5) -> dict:
    """
    偵測檔案編碼並讀取前幾行作為預覽。
    僅讀取少量初始字節以提高效率。
    """
    preview_lines = []
    detected_encoding = None
    error_message = None

    read_limit_bytes = 4096

    try:
        with open(file_path, 'rb') as f_bytes:
            initial_bytes = f_bytes.read(read_limit_bytes)
            if not initial_bytes:
                return {'encoding': None, 'preview': [], 'error': '檔案為空', 'file_type': 'empty'}

        # 首先檢查是否為 ZIP 檔案
        import zipfile
        is_zip = False
        if file_path.lower().endswith('.zip'): # 簡易檢查副檔名
            try:
                # 為了確認是否真的是 ZIP，可以嘗試讀取其內容
                # 但由於我們只讀了 initial_bytes，這可能不完整。
                # 更好的方式是直接嘗試用 zipfile 打開原始檔案路徑。
                # 不過，為了遵循只讀取前幾行的原則，這裡處理方式會比較棘手。
                # 暫時先基於副檔名，如果需要更強的 ZIP 檢測，需要完整讀取或更複雜邏輯。
                # 此處的簡化：如果副檔名是 .zip，我們就嘗試列出其內容。
                 with open(file_path, 'rb') as full_f_bytes_for_zip: # 為 ZIP 重新打開以完整讀取
                    if zipfile.is_zipfile(full_f_bytes_for_zip):
                        is_zip = True
                        full_f_bytes_for_zip.seek(0) # 重置指針
                        with zipfile.ZipFile(full_f_bytes_for_zip, 'r') as zf:
                            # 預覽 ZIP 檔案內的檔名列表 (前 N 個)
                            preview_lines = [f"압축 내용: {member.filename}" for member in zf.infolist()[:num_lines]]
                            if not preview_lines:
                                preview_lines = ["압축 파일 내용이 비었거나 읽을 수 없습니다."]
                        return {'encoding': 'binary/zip', 'preview': preview_lines, 'error': None, 'file_type': 'zip'}
            except zipfile.BadZipFile:
                # 副檔名是 .zip 但不是有效的 zip 檔案
                error_message = "檔案副檔名為 .zip 但似乎不是一個有效的 ZIP 檔案。"
                # 繼續嘗試作為一般文字檔案處理
                pass # 繼續下面的文字檔案處理邏輯
            except Exception as e_zip:
                error_message = f"嘗試作為 ZIP 檔案處理時發生錯誤: {e_zip}"
                pass # 繼續下面的文字檔案處理邏輯

        # 如果不是 ZIP 或 ZIP 處理失敗，則嘗試作為文字檔案處理
        common_encodings = ['utf-8', 'utf-8-sig', 'ms950', 'big5']
        for enc in common_encodings:
            try:
                decoded_content = initial_bytes.decode(enc)
                buffer = io.StringIO(decoded_content)
                for _ in range(num_lines):
                    line = buffer.readline()
                    if not line: break
                    preview_lines.append(line.rstrip('\r\n'))
                detected_encoding = enc
                error_message = None # 清除之前可能的 zip 錯誤
                break
            except UnicodeDecodeError:
                preview_lines = []
                continue

        if not detected_encoding:
            try:
                decoded_content = initial_bytes.decode('latin-1')
                buffer = io.StringIO(decoded_content)
                for _ in range(num_lines):
                    line = buffer.readline()
                    if not line: break
                    preview_lines.append(line.rstrip('\r\n') + " (以 latin-1 解碼可能不正確)")
                detected_encoding = 'latin-1 (回退)'
                error_message = None
            except Exception:
                 if not error_message: # 避免覆蓋來自 ZIP 判斷的錯誤
                    error_message = "無法使用常見編碼 (utf-8, ms950, big5) 解碼，也無法以 latin-1 回退。"
                 try:
                     buffer = io.BytesIO(initial_bytes)
                     for _ in range(num_lines):
                         line_bytes = buffer.readline()
                         if not line_bytes: break
                         preview_lines.append(repr(line_bytes.rstrip(b'\r\n')))
                     if not preview_lines and initial_bytes : # 如果檔案小於一行但有內容
                         preview_lines.append(repr(initial_bytes))
                 except Exception as e_repr:
                     preview_lines.append(f"無法獲取原始字節預覽: {e_repr}")

    except FileNotFoundError:
        error_message = "檔案不存在。"
        return {'encoding': None, 'preview': [], 'error': error_message, 'file_type': 'error'}
    except IOError as e:
        error_message = f"讀取檔案時發生 IO 錯誤: {e}"
        return {'encoding': None, 'preview': [], 'error': error_message, 'file_type': 'error'}
    except Exception as e:
        error_message = f"偵測編碼與預覽時發生未預期錯誤: {e}"
        return {'encoding': None, 'preview': [], 'error': error_message, 'file_type': 'error'}

    return {'encoding': detected_encoding, 'preview': preview_lines, 'error': error_message, 'file_type': 'text' if detected_encoding else 'binary_or_unknown'}


def prospect_file(file_path: str) -> dict:
    """
    對單一檔案進行探勘，返回包含元數據和內容預覽的字典。
    """
    report = {
        'file_path': None,
        'absolute_path': None,
        'size_bytes': None,
        'size_human_readable': None,
        'modification_time_utc': None,
        'modification_time_taipei': None,
        'encoding': None,
        'preview': [],
        'error': None,
        'status': 'failure' # 預設為失敗
    }

    try:
        if not os.path.exists(file_path):
            report['error'] = "檔案不存在。"
            report['file_path'] = file_path
            return report

        if not os.path.isfile(file_path):
            report['error'] = "提供的路徑不是一個有效的檔案。"
            report['file_path'] = file_path
            return report

        report['file_path'] = file_path
        report['absolute_path'] = os.path.abspath(file_path)

        stat_info = os.stat(file_path)
        report['size_bytes'] = stat_info.st_size
        report['size_human_readable'] = human_readable_size(stat_info.st_size)

        mod_timestamp = stat_info.st_mtime
        # 修正 DeprecationWarning: datetime.datetime.utcfromtimestamp()
        report['modification_time_utc'] = datetime.fromtimestamp(mod_timestamp, tz=pytz.utc).isoformat()
        report['modification_time_taipei'] = get_taipei_time_str(mod_timestamp)

        encoding_info = detect_encoding_and_preview(file_path) # 這現在返回一個包含 'file_type' 的字典
        report['encoding'] = encoding_info['encoding']
        report['preview'] = encoding_info['preview']
        report['file_type'] = encoding_info.get('file_type', 'unknown') # 獲取 file_type

        current_error = encoding_info.get('error')

        if report['file_type'] == 'empty':
            report['error'] = current_error # "檔案為空"
            report['status'] = 'success' # 探勘空檔案視為成功
        elif report['file_type'] == 'zip':
            # 對於 ZIP 檔案，即使 preview 成功，encoding 可能是 'binary/zip'
            # 通常視為成功，除非 zipfile 內部有嚴重錯誤 (已在 detect_encoding_and_preview 中處理)
            report['status'] = 'success'
            if current_error: # 如果 zip 處理中有非致命錯誤記錄
                 report['error'] = current_error
        elif report['file_type'] == 'text':
            report['status'] = 'success' # 文字檔案成功解碼
            if current_error: # 可能有非致命的解碼回退訊息
                 report['error'] = current_error
        elif current_error: # 對於 binary_or_unknown 或 error 類型，如果 detect_... 有錯誤
            error_msg_to_add = f"預覽/編碼錯誤: {current_error}"
            if report['error']:
                 report['error'] += f"; {error_msg_to_add}"
            else:
                 report['error'] = error_msg_to_add
            report['status'] = 'failure' # 如果有錯誤且不是特殊情況，則失敗
        elif not report['error'] and report['file_type'] != 'error': # 如果沒有任何錯誤累積
             report['status'] = 'success'
        else: # 其他未明確成功的情況，或 file_type 是 error
            report['status'] = 'failure'


    except Exception as e:
        report['error'] = f"探勘檔案時發生未預期錯誤: {str(e)}"
        if not report['file_path']: # 確保 file_path 存在
            report['file_path'] = file_path
        report['status'] = 'failure' # 確保未預期錯誤時 status 為 failure

    return report

def main():
    """
    主執行函數，解析參數並輸出探勘報告。
    """
    parser = argparse.ArgumentParser(description="TAIFEX 數據偵察兵：對單一檔案進行快速格式探勘與健康檢查。")
    parser.add_argument("--file-path", required=True, help="要探勘的目標檔案路徑。")

    args = parser.parse_args()

    if not args.file_path:
        print(json.dumps({"error": "未提供 --file-path 參數。", "status": "failure"}, ensure_ascii=False, indent=4))
        sys.exit(1)

    report = prospect_file(args.file_path)

    # 確保以 UTF-8 輸出 JSON 到 stdout
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=4))

    if report['status'] == 'failure':
        sys.exit(1)
    else:
        sys.exit(0)

if __name__ == "__main__":
    main()
