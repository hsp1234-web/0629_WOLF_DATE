# -*- coding: utf-8 -*-
"""
解析器模組 (apps.file_processor.parsers)

此套件包含所有用於解析不同交易所檔案格式的解析器類別。
每個解析器應繼承自 .base_parser.BaseParser。

主要功能：
- 提供一個統一的介面來處理各種檔案格式。
- 將原始檔案數據轉換為結構化的 Pandas DataFrame。
- 允許動態載入和選擇適合特定檔案的解析器。

使用方式：
  file_processor.run 主程式會根據檔案名稱或其他特徵，
  從此套件中動態選擇並實例化合適的解析器。
"""

# 這裡可以選擇性地匯出特定的解析器類別，方便外部直接引用
# 例如:
# from .txo_daily_parser import TXODailyParser
# from .another_parser import AnotherParser

# 或者讓 run.py 使用 importlib 動態載入，這樣就不需要在 __init__.py 中明確列出所有解析器。
# (目前的 run.py 設計是使用 importlib)

# 這個 __init__.py 檔案的存在，使得 'parsers' 目錄可以被 Python 視為一個套件。
