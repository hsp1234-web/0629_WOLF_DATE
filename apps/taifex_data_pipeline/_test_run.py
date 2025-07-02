# -*- coding: utf-8 -*-
# 精煉廠測試檔 (v20.2 串流處理版)
import os
# 確保 DuckDB 和其他數值計算庫在受限環境下不會因線程競爭導致效能下降
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
import sys
import unittest
import asyncio # 新增 for async test
import io # 新增 for BytesIO
# import subprocess # 移除 subprocess
import json
import tempfile
import shutil
import zipfile
import duckdb
from typing import Dict, List, Tuple, Any, Union # Union 新增
from unittest.mock import MagicMock # 新增 for mocking StreamReader

# --- 路徑自我校正樣板碼 ---
try:
    current_pipeline_dir = os.path.dirname(os.path.abspath(__file__))
    apps_dir = os.path.dirname(current_pipeline_dir)
    project_root = os.path.dirname(apps_dir)

    if apps_dir not in sys.path:
        sys.path.insert(0, apps_dir)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    # 導入被測試的模組
    from taifex_data_pipeline import run as pipeline_run # 更改導入方式
    from taifex_data_pipeline.run import SimpleLogger, HardwareManager # 導入其他需要的組件
    # from taifex_data_downloader.run import StreamDownloadError # 如果需要 mock downloader 異常 (暫不需要)
    # from taifex_data_pipeline.stream_unzipper import AiozipMemberStreamAdapter # 如果需要特定檢查此類型 (暫不需要)

except ImportError as e:
    print(f"導入模組時發生錯誤: {e}", file=sys.stderr)
    pipeline_run = None # type: ignore
    SimpleLogger = None # type: ignore
    HardwareManager = None # type: ignore
except Exception as e:
    print(f"路徑校正或導入時發生其他錯誤: {e}", file=sys.stderr)
    pipeline_run = None # type: ignore
    SimpleLogger = None # type: ignore
    HardwareManager = None # type: ignore
# --- 路徑自我校正樣板碼結束 ---


class TestTaifexDataPipelineStreaming(unittest.IsolatedAsyncioTestCase): # 更改基類
    """
    針對重構後的非同步串流版本 `run.py` 的端到端測試。
    主要測試 `run.py` 的核心邏輯 (`async_main`) 是否能正確處理
    模擬的記憶體數據串流 (來自 `aiohttp.StreamReader` 的 mock)，
    並在 DuckDB 資料庫中生成預期的表和數據。
    強調：整個流程不應有本地磁碟檔案讀寫 (除了最終DB和format_map)。
    """

    def _create_mock_aiohttp_stream_reader(self, content_bytes: bytes) -> MagicMock:
        """
        創建一個模擬的 aiohttp.StreamReader 物件。
        這個 mock 物件的 read() 方法會返回提供的 content_bytes。
        """
        mock_stream = MagicMock(name="MockAiohttpStreamReader")

        # 模擬 read() 方法
        async def mock_read(n: int = -1) -> bytes:
            if hasattr(mock_stream, '_buffer') and mock_stream._buffer is not None:
                if n == -1:
                    data = mock_stream._buffer.read()
                    mock_stream._buffer = None # 標記已讀完
                    return data
                else:
                    data = mock_stream._buffer.read(n)
                    if not data: # 如果讀不到更多，則 buffer 設為 None
                         mock_stream._buffer = None
                    return data
            return b"" # 如果 buffer 為 None 或已耗盡

        # 模擬 readline() 方法 (簡化版)
        async def mock_readline() -> bytes:
            if hasattr(mock_stream, '_buffer') and mock_stream._buffer is not None:
                line = mock_stream._buffer.readline()
                if not mock_stream._buffer.peek(1): # 檢查是否還有內容
                    mock_stream._buffer = None
                return line
            return b""

        # 模擬 readchunk() 方法
        async def mock_readchunk(size: int = 8192) -> bytes:
            return await mock_read(size)

        # 模擬 at_eof() 方法
        def mock_at_eof() -> bool:
            return not (hasattr(mock_stream, '_buffer') and mock_stream._buffer is not None and mock_stream._buffer.peek(1))

        mock_stream.read = MagicMock(side_effect=mock_read)
        mock_stream.readline = MagicMock(side_effect=mock_readline)
        mock_stream.readchunk = MagicMock(side_effect=mock_readchunk)
        mock_stream.at_eof = MagicMock(side_effect=mock_at_eof)

        # 初始化內部緩衝區
        mock_stream._buffer = io.BytesIO(content_bytes)
        return mock_stream

    def _create_zip_in_memory(self, files_content: Dict[str, Union[str, bytes]]) -> bytes:
        """
        在記憶體中創建一個 ZIP 檔案，包含指定的檔案及其內容。
        返回 ZIP 檔案的位元組內容。
        """
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            for filename, content in files_content.items():
                if isinstance(content, str):
                    zf.writestr(filename, content.encode('utf-8')) # 預設使用 utf-8 編碼字串內容
                else: # 假設是 bytes
                    zf.writestr(filename, content)
        return zip_buffer.getvalue()

    async def asyncSetUp(self): # 更改為 asyncSetUp
        """
        為每個測試案例設定初始環境。
        - 初始化 logger。
        - 創建臨時目錄用於資料庫輸出和工作檔案。
        - 準備樣本數據內容 (CSV 字串)。
        """
        self.logger = SimpleLogger(log_level="DEBUG")
        pipeline_run.logger = self.logger # 嘗試覆蓋 run.py 中的 logger

        self.base_temp_dir = tempfile.mkdtemp(prefix="test_pipeline_streaming_")
        self.temp_db_output_dir = os.path.join(self.base_temp_dir, "db_output")
        self.temp_work_dir = os.path.join(self.base_temp_dir, "work_temp") # DuckDB 臨時目錄等

        os.makedirs(self.temp_db_output_dir, exist_ok=True)
        os.makedirs(self.temp_work_dir, exist_ok=True)

        # 準備樣本 CSV 內容 (與舊測試中的 _create_sample_tick_data_csv 和 sample_pipeline_data.zip 內容對應)
        self.sample_tick_data_csv_content = """成交日期,商品代號,到期月份(週別),履約價,買賣權,成交時間,成交價格,成交數量
20240726,TXO,202408W1,18000,買權,084501,120.5,2
20240726,TXO,202408W1,18000,買權,084502,121.0,3
20240726,MXF,202408,,,084503,1750.5,10
BADDATE,MXF,202408,,,084504,1750.0,1
20240726,TXF,202408,,,084505,17800,INVALID_VOL
20240727,TXO,202408W2,17500,賣權,090000,50.0,5
20240727,QQQ,202409,100,買權,090100,數據遺失,10
20240727,RRR,202409,200,賣權,090200,50.0,---
"""
        # 來自舊 sample_pipeline_data.zip 的內容 (簡化版)
        self.futures_daily_content = """交易日期,契約,到期月份(週別),開盤價,最高價,最低價,收盤價,漲跌價,漲跌%,成交量,結算價,未沖銷契約數,最後最佳買價,最後最佳賣價,歷史最高價,歷史最低價
2022/01/03,TX,202201,18241,18338,18240,18298,80,0.44,87654,18300,65432,18297,18298,18350,18000
""" # 實際 ZIP 中還有更多...
        self.options_daily_v1_content = """交易日期,商品代號,到期月份(週別),履約價,買賣權,開盤價,最高價,最低價,收盤價,成交量,結算價,未沖銷契約數,最後最佳買價,最後最佳賣價,歷史最高價,歷史最低價,暫停交易,交易時段
2022/01/04,TXO,202201W1,18000,買權,180,195,170,190,1234,190,5678,189,190,200,150,否,一般
"""
        self.institutional_investors_content = """交易日期,商品名稱,身份別,合計/契約,買賣權,多方交易口數,空方交易口數,... (省略其他欄位)
2022/01/03,臺股期貨,外資,契約,,10000,8000,...
2022/01/03,臺指選擇權,自營商,契約,買權,500,300,...
""" # 高度簡化，實際檔案更複雜

    async def asyncTearDown(self): # 更改為 asyncTearDown
        if hasattr(self, 'base_temp_dir') and os.path.exists(self.base_temp_dir):
            shutil.rmtree(self.base_temp_dir)
        # 移除在 setUp 中可能創建的臨時檔案 (如果有的話)
        # self.tick_data_sample_csv_path 不再創建本地檔案

    async def test_pipeline_streaming_run(self): # 更改方法名並設為 async
        """
        執行端到端的串流管線測試。
        """
        test_db_name = "test_streaming_analytics.duckdb"
        db_full_path = os.path.join(self.temp_db_output_dir, test_db_name)
        format_map_path = os.path.join(self.temp_db_output_dir, "format_map.json")

        # 準備模擬的 ZIP 串流
        zip_files_content = {
            "futures_daily_sample.csv": self.futures_daily_content,
            "options_daily_v1_sample.csv": self.options_daily_v1_content,
            "institutional_investors_sample.csv": self.institutional_investors_content,
            "tick_data_in_zip_sample.csv": self.sample_tick_data_csv_content # 也將 tick data 放入 ZIP
        }
        zip_bytes = self._create_zip_in_memory(zip_files_content)
        mock_zip_stream_reader = self._create_mock_aiohttp_stream_reader(zip_bytes)

        # 準備一個直接的 CSV 串流 (非 ZIP)
        mock_direct_csv_stream_reader = self._create_mock_aiohttp_stream_reader(
            self.sample_tick_data_csv_content.encode('utf-8') # 假設 CSV 內容是 UTF-8
        )

        input_streams_for_main: List[Tuple[str, Any]] = [
            ("sample_archive.zip", mock_zip_stream_reader),
            ("direct_tick_data.csv", mock_direct_csv_stream_reader)
        ]

        self.logger.info(f"開始執行 (v20.2 Streaming) 精煉廠核心邏輯...")

        # 直接調用 async_main
        # 注意：run.py 中的 main() 和 argparse 部分不再使用
        results = await pipeline_run.async_main(
            input_streams=input_streams_for_main,
            db_output_dir_arg=self.temp_db_output_dir,
            db_name_arg=test_db_name,
            temp_dir_arg=self.temp_work_dir,
            max_workers_arg=1, # 測試時用單線程可能更易調試
            memory_limit_gb_arg=1, # 測試時限制記憶體
            log_level_arg="DEBUG"
        )

        self.logger.info(f"精煉廠核心邏輯執行結果: {results}")
        self.assertIsNotNone(results, "async_main應返回結果字典")
        self.assertEqual(results.get("status"), "success", f"管線執行應成功，但收到: {results.get('message')}")

        # 驗證 format_map.json 和 DuckDB 檔案已創建
        self.assertTrue(os.path.exists(format_map_path), f"格式地圖 {format_map_path} 未建立。")
        with open(format_map_path, 'r', encoding='utf-8') as f_map:
            format_map_content = json.load(f_map)
        # 鍵現在是 descriptor，例如 "sample_archive.zip -> futures_daily_sample.csv"
        # 預期鍵的數量 = ZIP中檔案數 + 直接CSV數
        expected_num_format_map_entries = len(zip_files_content) + 1
        self.assertEqual(len(format_map_content), expected_num_format_map_entries, f"格式地圖條目數應為 {expected_num_format_map_entries}，實際為 {len(format_map_content)}。鍵: {list(format_map_content.keys())}")

        self.assertTrue(os.path.exists(db_full_path), f"DuckDB 資料庫 {db_full_path} 未建立。")

        # --- 資料庫驗證 ---
        con = duckdb.connect(database=db_full_path, read_only=True)

        # daily_ohlc: 來自 ZIP 中的 futures_daily (1行) + options_daily_v1 (1行)
        daily_ohlc_rows = con.execute("SELECT COUNT(*) FROM daily_ohlc;").fetchone()
        self.assertEqual(daily_ohlc_rows[0] if daily_ohlc_rows else 0, 2, "daily_ohlc 表記錄數不符。")

        # institutional_investors: 來自 ZIP 中的 institutional_investors (2行有效)
        inst_inv_rows = con.execute("SELECT COUNT(*) FROM institutional_investors;").fetchone()
        self.assertEqual(inst_inv_rows[0] if inst_inv_rows else 0, 2, "institutional_investors 表記錄數不符。")

        # tick_data: 來自 ZIP 中的 tick_data_in_zip (4行有效) + direct_tick_data (4行有效)
        # 由於 ON CONFLICT DO NOTHING 和相同的數據，最終應該只有4行
        tick_data_rows = con.execute("SELECT COUNT(*) FROM tick_data;").fetchone()
        self.assertEqual(tick_data_rows[0] if tick_data_rows else 0, 4,
                         f"tick_data 表記錄數應為4 (經過去重)，實際 {tick_data_rows[0] if tick_data_rows else 0}")

        # 抽樣檢查 tick_data (來自 direct_tick_data.csv 或 zip 內成員，內容相同)
        query_datetime_condition_tick = "strftime(trade_datetime, '%Y-%m-%d %H:%M:%S.%f') = '2024-07-26 08:45:01.000000'"
        sql_query_tick = f"SELECT price, volume FROM tick_data WHERE product_id='TXO' AND strike_price=18000 AND option_type='C' AND {query_datetime_condition_tick};"
        res_tick_sample = con.execute(sql_query_tick).fetchone()
        self.assertIsNotNone(res_tick_sample, f"未能查詢到指定的 tick_data 樣本記錄。查詢: {sql_query_tick}")
        if res_tick_sample:
            self.assertEqual(res_tick_sample[0], 120.5, "tick_data 樣本價格不符。")
            self.assertEqual(res_tick_sample[1], 2, "tick_data 樣本成交量不符。")

        con.close()

        # 驗證無不期望的本地檔案寫入 (檢查 temp_input_dir 是否為空)
        # self.temp_input_dir 在此測試中未使用，因為我們是模擬串流
        # self.temp_work_dir 可能包含 duckdb 臨時檔案，這是正常的
        # 關鍵是沒有原始數據或解壓後的數據被寫到磁碟
        # 一個簡單的檢查是確保除了 db_output_dir 和 work_temp/duckdb_temp 之外沒有其他檔案
        # 更嚴格的檢查需要 mock open() 等，但目前範圍外。

        # 檢查主臨時目錄下是否只有預期的子目錄
        # base_temp_dir 下應該只有 db_output 和 work_temp
        unexpected_items_in_base_temp = [
            item for item in os.listdir(self.base_temp_dir)
            if item not in ['db_output', 'work_temp']
        ]
        self.assertEqual(len(unexpected_items_in_base_temp), 0,
                        f"基礎臨時目錄 {self.base_temp_dir} 中發現非預期項目: {unexpected_items_in_base_temp}。只應包含 'db_output' 和 'work_temp'。")

        # 檢查 work_temp (除了 duckdb_temp) 是否為空
        items_in_work_temp = [
            item for item in os.listdir(self.temp_work_dir)
            if item != 'duckdb_temp' # duckdb_temp 是 duckdb 自己的臨時目錄
        ]
        self.assertEqual(len(items_in_work_temp), 0,
                         f"工作目錄 {self.temp_work_dir} (排除 duckdb_temp) 應為空，但包含: {items_in_work_temp}。這可能表示有臨時檔案寫入。")


if __name__ == "__main__":
    # 為了能在 Colab 或腳本中單獨運行和調試，可以這樣配置：
    # unittest.main(argv=['first-arg-is-ignored'], exit=False) # 舊的運行方式
    # 對於 IsolatedAsyncioTestCase，需要 asyncio.run
    if pipeline_run: # 確保模組已載入
        asyncio.run(unittest.main(argv=['first-arg-is-ignored'], exit=False, verbosity=0))
    else:
        print("Pipeline_run module not loaded, tests cannot run.", file=sys.stderr)

# 之前的 log_message 和 _get_test_logger 輔助函式定義已移除
# --- End of _test_run.py ---
