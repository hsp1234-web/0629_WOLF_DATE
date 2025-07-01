# -*- coding: utf-8 -*-
"""
高頻數據擷取器 (hf_data_ingestor) 套件。

此應用程式負責從 yfinance 等來源獲取、快取並儲存高頻市場數據。
"""

# 可以在此處匯出套件的主要類別或函數，方便外部調用
# 例如:
# from .run import main
# from .yfinance_client import YFinanceClient
# from .db_manager import DBManager

# 目前的設計是 run.py 作為主要執行入口，
# 並由 run.py 內部匯入 yfinance_client 和 db_manager。
# Colab (或其他調度器) 直接執行 run.py 腳本 (python -m apps.hf_data_ingestor.run)。
# 因此，此 __init__.py 主要目的是將此目錄標記為一個 Python 套件。
