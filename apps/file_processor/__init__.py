# -*- coding: utf-8 -*-
"""
交易所檔案處理器 (file_processor) 套件。

此應用程式負責掃描、解析使用者上傳的交易所數據檔案，
並將數據存入資料庫。
它包含一個解析器子套件 (parsers) 來處理不同的檔案格式。
"""

# 可以在此處匯出套件的主要類別或函數
# 例如:
# from .run import main
# from .db_manager import DBManager

# 同樣地，目前設計是由 Colab (或其他調度器)
# 直接執行 python -m apps.file_processor.run。
# 此 __init__.py 主要目的是標記此目錄為 Python 套件，
# 並使其下的 parsers 子套件能被正確找到。
