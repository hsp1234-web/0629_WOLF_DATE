# -*- coding: utf-8 -*-
import sys
# --- 路徑自我校正樣板碼 (與 _test_run.py 中類似，確保能找到 apps 目錄) ---
import os
try:
    current_script_dir = os.path.dirname(os.path.abspath(__file__)) # 通常是 repo 根目錄
    apps_dir = os.path.join(current_script_dir, "apps")

    # 將 apps 目錄和專案根目錄（如果不同）添加到 sys.path
    if apps_dir not in sys.path:
        sys.path.insert(0, apps_dir)

    project_root_from_script = os.path.dirname(current_script_dir) # 假設腳本在根目錄
    if current_script_dir not in sys.path: # 把腳本所在目錄(根)也加入
        sys.path.insert(0, current_script_dir)

    # print(f"temp_import_test.py: sys.path modified to: {sys.path}")

except Exception as e:
    print(f"路徑校正時發生錯誤: {e}", file=sys.stderr)
# --- 路徑自我校正樣板碼結束 ---

try:
    from apps.taifex_data_pipeline.stream_unzipper import InMemoryStreamUnzipper
    print("✅ SUCCESS: InMemoryStreamUnzipper 模組導入成功！")
    print(f"模組位置: {InMemoryStreamUnzipper.__module__}")
except ImportError as e:
    print(f"❌ FAILED: 導入失敗！錯誤訊息: {e}")
    print(f"sys.path at import failure: {sys.path}")
except Exception as e:
    print(f"❌ FAILED: 發生非預期錯誤！錯誤訊息: {e}")
    print(f"sys.path at unexpected error: {sys.path}")
