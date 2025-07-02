# -*- coding: utf-8 -*-
import argparse
import os
import shutil
import sys
import json
import io
import hashlib
import pytz
import concurrent.futures
import duckdb
import re
from datetime import datetime
from typing import Dict, Tuple

# 修改 sys.path 以允許導入 pipeline_core
current_script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_script_dir)

try:
    from pipeline_core import (
        DualLogger, HardwareManager, RealtimeHardwareMonitor,
        discover_files_recursively, determine_parsing_recipe, worker_process_file,
        DB_FILE_NAME, FORMAT_MAP_FILENAME,
        TABLE_DEFINITIONS, SEQUENCES, UNIQUE_INDICES, PIPELINE_MAP
    )
except ImportError as e:
    print(f"導入 pipeline_core 時發生錯誤: {e}")
    sys.exit(1)

def run_main_parsing_stage(
    logger: DualLogger,
    hw_manager: HardwareManager,
    local_input_path: str,
    local_staging_path: str,
    format_map: Dict
) -> Tuple[bool, int, int, int, int]:
    logger.header("🚚 階段一: 解析原始檔至本地暫存區 🚚")
    logger.section("掃描本地輸入目錄並建立工作清單")

    jobs = []
    map_updated = False

    all_files_gen = discover_files_recursively(local_input_path, logger)
    discovered_file_count = 0

    for descriptor, content_bytes in all_files_gen:
        discovered_file_count += 1
        file_hash = hashlib.sha256(content_bytes).hexdigest()
        recipe = format_map.get(file_hash)

        if not recipe:
            map_updated = True
            recipe = determine_parsing_recipe(content_bytes, descriptor)
            format_map[file_hash] = recipe if recipe else {"parser": "unknown", "pipeline": "unknown"}
            if recipe and recipe.get("pipeline") != "unknown":
                logger.info(f"動態學習: 為 '{descriptor[:70]}...' 建立配方 -> {recipe.get('pipeline', 'unknown')}")
            elif not recipe:
                 logger.warning(f"學習失敗: 無法為 '{descriptor[:70]}...' 建立配方 (返回 None)。")
            else:
                 logger.warning(f"學習失敗: 為 '{descriptor[:70]}...' 建立的配方未知。")

        if recipe and recipe.get("pipeline") != "unknown":
            jobs.append((descriptor, content_bytes, recipe, local_staging_path))

    if discovered_file_count == 0:
        logger.warning("在本地輸入目錄中沒有掃描到任何檔案。")

    if not jobs:
        logger.warning(f"掃描到 {discovered_file_count} 個項目，但在本地輸入目錄中沒有找到任何有效檔案可供處理。")
        return map_updated, 0, 0, 0, discovered_file_count

    logger.success(f"工作清單建立完畢，共 {len(jobs)} 個有效檔案待處理 (從 {discovered_file_count} 個掃描項目中)。")

    logger.section(f"啟動 {hw_manager.max_workers} 個工人進程，開始並行處理")
    stats = {'success': 0, 'skipped_empty_parse': 0, 'skipped_no_pipeline': 0, 'skipped_empty_clean':0, 'error': 0, 'rows': 0}

    processed_descriptors_in_batch = set()

    with concurrent.futures.ProcessPoolExecutor(max_workers=hw_manager.max_workers) as executor:
        future_to_job_info = {
            executor.submit(worker_process_file, job_args): job_args[0] for job_args in jobs
        }

        for i, future in enumerate(concurrent.futures.as_completed(future_to_job_info)):
            descriptor = future_to_job_info[future]
            if descriptor in processed_descriptors_in_batch:
                pass

            logger.info(f"--- [進度 {i+1}/{len(jobs)}] 正在等待 '{descriptor[:70]}...' 的結果 ---")
            try:
                result = future.result()
                stats[result['status']] = stats.get(result['status'], 0) + 1
                if result['status'] == 'success':
                    stats['rows'] += result['rows_processed']
                    logger.success(f"  ↳ '{result.get('descriptor', descriptor)[:70]}...' 處理成功 ({result['pipeline']})，暫存 {result['rows_processed']:,} 筆記錄至 {os.path.basename(result['staging_file'])}。")
                else:
                    logger.error(f"  ↳ '{result.get('descriptor', descriptor)[:70]}...' 處理失敗/跳過: {result['message']} (Pipeline: {result.get('pipeline', 'N/A')})")

            except Exception as exc:
                logger.error(f"處理任務 '{descriptor[:70]}...' 的 future 時產生嚴重例外: {exc}")
                stats['error'] += 1

            processed_descriptors_in_batch.add(descriptor)

    logger.success(f"階段一所有檔案處理完畢。")
    return map_updated, stats['rows'], stats['success'], stats['error'], sum(stats[k] for k in ['skipped_empty_parse', 'skipped_no_pipeline', 'skipped_empty_clean'])

def run_main_duckdb_loading_stage(
    logger: DualLogger,
    hw_manager: HardwareManager,
    local_db_file_full_path: str,
    local_staging_path: str,
    local_db_temp_path: str
) -> int:
    logger.header("📦 階段二: 從本地暫存區高速載入至 DuckDB 📦")

    os.makedirs(local_db_temp_path, exist_ok=True)

    try:
        conn = duckdb.connect(local_db_file_full_path, read_only=False)
        logger.success(f"成功連接至本地 DuckDB 資料庫: {os.path.basename(local_db_file_full_path)}")
        conn.execute(f"SET memory_limit = '{hw_manager.memory_limit_gb}GB'; SET threads = {hw_manager.max_workers}; SET temp_directory = '{os.path.abspath(local_db_temp_path)}';")
        logger.info(f"DuckDB 環境設定完畢 (Memory: {hw_manager.memory_limit_gb}GB, Threads: {hw_manager.max_workers}, TempDir: {os.path.abspath(local_db_temp_path)})")

        for seq_sql in SEQUENCES.values(): conn.execute(seq_sql)
        for create_sql in TABLE_DEFINITIONS.values(): conn.execute(create_sql)
        logger.info("所有資料庫表格與序列 (SEQUENCE) 已確認或建立。")
    except Exception as e:
        logger.error(f"資料庫初始化失敗: {e}")
        return 0

    all_staged_files = [f for f in os.listdir(local_staging_path) if f.endswith('.parquet')]
    if not all_staged_files:
        logger.warning("本地暫存區中沒有找到任何 .parquet 檔案，無需載入。")
        conn.close()
        return 0

    files_by_table = {}
    possible_pipeline_names = list(TABLE_DEFINITIONS.keys())

    for filename in all_staged_files:
        base_filename = os.path.basename(filename)
        found_pipeline_name = None
        for pname in possible_pipeline_names:
            if base_filename.startswith(pname + "_"):
                found_pipeline_name = pname
                break

        if found_pipeline_name:
            table_name = found_pipeline_name
            if table_name not in files_by_table: files_by_table[table_name] = []
            files_by_table[table_name].append(os.path.join(local_staging_path, filename))
        else:
            logger.warning(f"無法從 Parquet 檔名 '{base_filename}' 中確定 pipeline_name/table_name，跳過此檔案。")

    total_rows_added_to_db = 0
    for table_name, file_list in files_by_table.items():
        if table_name not in TABLE_DEFINITIONS:
            logger.warning(f"跳過 '{table_name}' 的 Parquet 檔案，因為它不在預定義的表格中。 ({len(file_list)} 個檔案)")
            continue

        logger.section(f"正在載入資料至 '{table_name}' 表格 (來源檔案數: {len(file_list)})")
        try:
            # 確保在 INSERT 前移除可能存在的舊索引
            if table_name in UNIQUE_INDICES:
                 conn.execute(f"DROP INDEX IF EXISTS {UNIQUE_INDICES[table_name].split(' ')[4]};")
                 logger.info(f"  ↳ 已嘗試移除唯一性索引 (如果存在) 以便進行裸 INSERT。")

            conn.execute(f"CREATE OR REPLACE TEMP TABLE temp_staging AS SELECT * FROM read_parquet({file_list}, union_by_name=True);")

            initial_count = conn.execute("SELECT COUNT(*) FROM temp_staging").fetchone()[0]
            if initial_count == 0:
                logger.warning(f"  ↳ 臨時表 temp_staging 為空 (從 {len(file_list)} 個檔案中)，跳過此表格。")
                if table_name in UNIQUE_INDICES:
                    try:
                        conn.execute(UNIQUE_INDICES[table_name])
                        logger.info(f"  ↳ (空表) 已為 '{table_name}' 嘗試創建唯一性索引。")
                    except Exception as e_idx_empty:
                        logger.error(f"  ↳ (空表) 為 '{table_name}' 創建唯一性索引失敗: {e_idx_empty}")
                conn.execute("DROP TABLE IF EXISTS temp_staging;")
                continue
            logger.success(f"  ↳ 已將 {len(file_list)} 個 Parquet 檔案 ({initial_count:,} 筆記錄) 高速載入至臨時表 temp_staging。")

            unique_cols_str = re.search(r'\((.*?)\)', UNIQUE_INDICES[table_name]).group(1)
            dedup_sql = f"""
                CREATE OR REPLACE TEMP TABLE temp_clean AS
                SELECT * FROM (
                    SELECT *, ROW_NUMBER() OVER (PARTITION BY {unique_cols_str} ORDER BY source DESC) as rn
                    FROM temp_staging
                ) WHERE rn = 1;
            """
            conn.execute(dedup_sql)
            clean_count = conn.execute("SELECT COUNT(*) FROM temp_clean").fetchone()[0]
            logger.info(f"  ↳ 在臨時表 temp_clean 中去重完成，剩餘 {clean_count:,} 筆唯一記錄 (減少了 {initial_count - clean_count:,} 筆)。")

            if clean_count == 0:
                logger.info(f"  ↳ 去重後無記錄，無需插入。")
            else:
                target_cols_info = conn.execute(f"DESCRIBE {table_name}").fetchall()
                target_cols_names = {row[0] for row in target_cols_info}

                temp_cols_info = conn.execute("DESCRIBE temp_clean").fetchall()
                temp_cols_names = {row[0] for row in temp_cols_info}

                common_cols = [col for col in temp_cols_names if col in target_cols_names and col not in ['id', 'rn']]
                common_cols_str = ", ".join(f'"{c}"' for c in common_cols)

                logger.info(f"  ↳ 將從 temp_clean 向 \"{table_name}\" 插入共同欄位: {common_cols_str}")

                insert_sql = f"""
                INSERT INTO "{table_name}" (id, {common_cols_str})
                SELECT nextval('seq_{table_name}'), {common_cols_str}
                FROM temp_clean;
                """ # *** ON CONFLICT 已確定移除 ***

                logger.info(f"DEBUG: Common cols for \"{table_name}\": {common_cols_str}")
                logger.info(f"--- BEGIN Full INSERT SQL for {table_name} ---")
                logger.info(insert_sql)
                logger.info(f"--- END Full INSERT SQL for {table_name} ---")
                conn.execute(insert_sql)
                inserted_count = conn.execute('SELECT last_successful_query_processed_rows()').fetchone()[0]
                logger.success(f"  ↳ 成功插入 {inserted_count:,} 筆新記錄至 '{table_name}'。")
                total_rows_added_to_db += inserted_count

            # 在數據插入後，再嘗試创建唯一索引
            if table_name in UNIQUE_INDICES:
                try:
                    logger.info(f"  ↳ 準備為 '{table_name}' 創建唯一性索引: {UNIQUE_INDICES[table_name]}")
                    conn.execute(UNIQUE_INDICES[table_name])
                    logger.success(f"  ↳ 已在 '{table_name}' 上嘗試創建/重建唯一性索引。")
                except Exception as e_idx_final: # 如果這裡報錯，通常是 ConstraintError
                    logger.error(f"  ↳ 在 '{table_name}' 上創建/重建唯一性索引時失敗 (可能因數據重複): {e_idx_final}")

            conn.execute("DROP TABLE IF EXISTS temp_staging;")
            conn.execute("DROP TABLE IF EXISTS temp_clean;")

        except Exception as e:
            logger.error(f"  ↳ 載入資料至 '{table_name}' 時發生嚴重錯誤: {e}")
            try:
                if table_name in UNIQUE_INDICES: conn.execute(UNIQUE_INDICES[table_name])
            except: pass

    conn.close()
    logger.success(f"資料庫操作完成並已關閉連線。總共新增 {total_rows_added_to_db:,} 筆記錄到資料庫。")
    return total_rows_added_to_db

def main():
    parser = argparse.ArgumentParser(
        description="「精煉廠」微應用：TAIFEX 數據整合與 DuckDB 載入管道。",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--input-dir", required=True, help="原始數據的輸入目錄 (可為雲端/外部路徑)。")
    parser.add_argument("--output-db-dir", required=True, help="DuckDB 資料庫及 format_map.json 的輸出目錄 (可為雲端/外部路徑)。")
    parser.add_argument("--local-workspace-dir", required=True, help="本地臨時工作區的根目錄。")
    parser.add_argument("--monitoring-interval", type=int, default=8, help="硬體監控刷新頻率(秒)，設為0則不啟用高頻監控。")

    args = parser.parse_args()

    run_log_stream = io.StringIO()
    logger = DualLogger(log_to_console=True, log_stream=run_log_stream)

    hw_manager = HardwareManager(logger)
    hw_manager.display_initial_dashboard()

    monitor = None
    if args.monitoring_interval > 0:
        monitor = RealtimeHardwareMonitor(logger, hw_manager, interval=args.monitoring_interval)

    local_workspace_path = args.local_workspace_dir
    local_input_path = os.path.join(local_workspace_path, "input_files")
    local_staging_path = os.path.join(local_workspace_path, "staging_parquet")
    local_db_path = os.path.join(local_workspace_path, "database")
    local_db_temp_path = os.path.join(local_workspace_path, "duckdb_temp")

    local_db_file_full_path = os.path.join(local_db_path, DB_FILE_NAME)
    local_format_map_path = os.path.join(local_db_path, FORMAT_MAP_FILENAME)

    gdrive_input_path = args.input_dir
    gdrive_db_output_path = args.output_db_dir
    gdrive_db_file_full_path = os.path.join(gdrive_db_output_path, DB_FILE_NAME)
    gdrive_format_map_path = os.path.join(gdrive_db_output_path, FORMAT_MAP_FILENAME)

    logger.header("📊 高適應性期交所數據整合管道 v8.0 (移植版) 啟動 📊")

    try:
        logger.section("步驟 A: 環境準備與『本地優先』同步作業")
        for path in [local_workspace_path, local_input_path, local_staging_path, local_db_path, local_db_temp_path]:
            os.makedirs(path, exist_ok=True)
            logger.info(f"本地目錄 '{path}' 已確認/創建。")

        logger.info("開始從外部同步檔案至本地工作區...")
        if os.path.exists(gdrive_input_path):
            if os.path.exists(local_input_path): shutil.rmtree(local_input_path)
            shutil.copytree(gdrive_input_path, local_input_path)
            logger.success(f"  ↳ 輸入檔案已從 '{gdrive_input_path}' 同步至 '{local_input_path}'。")
        else:
             logger.error(f"指定的輸入路徑 '{gdrive_input_path}' 不存在，無法同步。流程終止。")
             sys.exit(1)

        if os.path.exists(gdrive_db_file_full_path):
            shutil.copy2(gdrive_db_file_full_path, local_db_file_full_path)
            logger.success(f"  ↳ 現有資料庫檔案已從 '{gdrive_db_file_full_path}' 同步至 '{local_db_file_full_path}'。")
        else: logger.warning(f"  ↳ 在外部輸出目錄未找到現有資料庫 '{gdrive_db_file_full_path}'，若流程繼續，將在本地建立新檔。")

        if os.path.exists(gdrive_format_map_path):
            shutil.copy2(gdrive_format_map_path, local_format_map_path)
            logger.success(f"  ↳ 現有格式地圖已從 '{gdrive_format_map_path}' 同步至 '{local_format_map_path}'。")
        else: logger.warning(f"  ↳ 在外部輸出目錄未找到格式地圖 '{gdrive_format_map_path}'。")

        format_map = {}
        try:
            if os.path.exists(local_format_map_path):
                with open(local_format_map_path, 'r', encoding='utf-8') as f: format_map = json.load(f)
                logger.success(f"成功從本地載入格式地圖 '{local_format_map_path}'，包含 {len(format_map)} 筆配方。")
        except FileNotFoundError:
            logger.warning(f"未找到本地格式地圖 '{local_format_map_path}'，將在執行過程中自動建立。")
        except json.JSONDecodeError as jde:
            logger.error(f"本地格式地圖 '{local_format_map_path}' 解析失敗: {jde}。將使用空的地圖。")
            format_map = {}

        if monitor: monitor.start()

        map_updated, rows_staged, success_count, error_count, skipped_count = run_main_parsing_stage(
            logger, hw_manager, local_input_path, local_staging_path, format_map
        )

        logger.header(f"✅ 階段一執行完畢 ✅")
        logger.success(f"總結: 成功 {success_count} 個檔案，失敗 {error_count} 個，跳過 {skipped_count} 個。共 {rows_staged:,} 筆數據記錄被成功解析並寫入本地暫存區。")

        logger.info(f"DEBUG: map_updated 狀態: {map_updated}")
        if map_updated:
            try:
                with open(local_format_map_path, 'w', encoding='utf-8') as f: json.dump(format_map, f, indent=4, ensure_ascii=False)
                logger.info(f"格式地圖已更新並儲存至本地: {local_format_map_path}")
                logger.info(f"DEBUG: 本地 format_map.json ({local_format_map_path}) 是否存在: {os.path.exists(local_format_map_path)}")
            except Exception as e_fm_save:
                logger.error(f"儲存更新後的本地格式地圖失敗: {e_fm_save}")
        else:
            logger.info("DEBUG: map_updated 為 False，未寫入本地 format_map。")

        rows_added_to_db = run_main_duckdb_loading_stage(
            logger, hw_manager, local_db_file_full_path, local_staging_path, local_db_temp_path
        )

        if monitor: monitor.stop()

        logger.header(f"🎉 階段二執行完畢 🎉")
        logger.success(f"總結: 資料庫共新增/更新 {rows_added_to_db:,} 筆唯一數據記錄。")

        logger.section("步驟 D: 回存至外部輸出目錄")
        os.makedirs(gdrive_db_output_path, exist_ok=True)

        logger.info(f"DEBUG: 開始回存。map_updated: {map_updated}")
        logger.info(f"DEBUG: 本地 format_map 路徑 ({local_format_map_path}) 是否存在: {os.path.exists(local_format_map_path)}")
        logger.info(f"DEBUG: 外部 format_map 路徑 ({gdrive_format_map_path}) 是否存在 (回存前): {os.path.exists(gdrive_format_map_path)}")

        if os.path.exists(local_db_file_full_path):
            shutil.copy2(local_db_file_full_path, gdrive_db_file_full_path)
            logger.success(f"資料庫檔案已成功回存至 '{gdrive_db_file_full_path}'。")
        else:
            logger.warning(f"本地資料庫檔案 '{local_db_file_full_path}' 未找到，無法回存。")

        if map_updated or not os.path.exists(gdrive_format_map_path):
            logger.info(f"DEBUG: 進入回存 format_map 的條件判斷。")
            if os.path.exists(local_format_map_path):
                shutil.copy2(local_format_map_path, gdrive_format_map_path)
                logger.success(f"格式地圖已成功回存至 '{gdrive_format_map_path}'。")
                logger.info(f"DEBUG: 外部 format_map 路徑 ({gdrive_format_map_path}) 是否存在 (回存後): {os.path.exists(gdrive_format_map_path)}")
            else:
                 logger.warning(f"本地格式地圖 '{local_format_map_path}' 未找到，無法回存。")
        else:
            logger.info(f"DEBUG: 未滿足回存 format_map 的條件 (map_updated is False and remote map exists)。")

    except Exception as e_main:
        logger.error(f"主流程發生未預期的嚴重錯誤: {type(e_main).__name__}: {e_main}")
        if monitor and monitor._is_running: monitor.stop()
    finally:
        logger.header("🏁 v8.0 (移植版) 全部流程執行完畢 🏁")

        log_file_name = f"pipeline_run_log_{datetime.now(pytz.timezone('Asia/Taipei')).strftime('%Y%m%d_%H%M%S')}.txt"
        final_log_path = os.path.join(args.local_workspace_dir, log_file_name)

        try:
            with open(final_log_path, 'w', encoding='utf-8') as f:
                f.write(run_log_stream.getvalue())
            logger.success(f"完整執行日誌已儲存至: {final_log_path}")
            shutil.copy2(final_log_path, os.path.join(gdrive_db_output_path, log_file_name))
            logger.success(f"完整執行日誌已嘗試複製到外部輸出目錄: {os.path.join(gdrive_db_output_path, log_file_name)}")
        except Exception as e_log_save:
            logger.error(f"儲存完整執行日誌至 '{final_log_path}' 時失敗: {e_log_save}")

        logger.section("步驟 E: 清理本地暫存")
        paths_to_clean = [local_staging_path, local_db_temp_path]
        for p_clean in paths_to_clean:
            if os.path.exists(p_clean):
                try:
                    shutil.rmtree(p_clean)
                    logger.info(f"本地暫存目錄 '{p_clean}' 清理完畢。")
                except Exception as e_clean:
                    logger.error(f"清理本地暫存目錄 '{p_clean}' 時失敗: {e_clean}")
            else:
                logger.info(f"本地暫存目錄 '{p_clean}' 不存在，無需清理。")

        logger.info("建議手動檢查並清理以下目錄（如果不再需要）:")
        logger.info(f" - 本地輸入副本: {local_input_path}")
        logger.info(f" - 本地資料庫工作副本: {local_db_path}")

        debug_log_path = os.path.join(args.local_workspace_dir, "debug_pipeline_run.log")
        try:
            with open(debug_log_path, 'w', encoding='utf-8') as f_debug:
                f_debug.write("=== DEBUG LOG STREAM ===\n")
                f_debug.write(run_log_stream.getvalue())
            logger.info(f"調試日誌已寫入: {debug_log_path}")
        except Exception as e_debug_log:
            logger.error(f"寫入調試日誌 '{debug_log_path}' 時失敗: {e_debug_log}")

if __name__ == "__main__":
    main()
