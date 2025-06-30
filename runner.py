import os
import json
import subprocess
import time
import datetime
import logging
import shutil
import multiprocessing
from src.utils.config_loader import load_project_config
from src.utils.logger import setup_logger

# --- Global Variables & Configuration ---
EVENT_BUS_DIR = "event_bus"
QUEUE_DIR = os.path.join(EVENT_BUS_DIR, "queue")
IN_PROGRESS_DIR = os.path.join(EVENT_BUS_DIR, "in_progress")
COMPLETED_DIR = os.path.join(EVENT_BUS_DIR, "completed")
FAILED_DIR = os.path.join(EVENT_BUS_DIR, "failed") # Centralized failed directory

LOG_DIR = "logs"
DEFAULT_PYTHON_EXECUTABLE = "python"
DEFAULT_MAX_CONCURRENT_APPS = os.cpu_count()

# --- Logger Setup ---
# Logger for the runner itself. File handler is set up in main.
# Console handler is set up by setup_logger.
logger = setup_logger('SOP4_Runner', logging.INFO)

# This list will store AsyncResult objects from the pool
active_tasks_results = []

# --- Configuration Loading (will be done in main) ---
project_config = {}
PYTHON_EXECUTABLE = DEFAULT_PYTHON_EXECUTABLE
MAX_CONCURRENT_APPS = DEFAULT_MAX_CONCURRENT_APPS


def get_task_files():
    """Scans the queue directory for new task files (JSON)."""
    if not os.path.exists(QUEUE_DIR):
        logger.warning(f"目錄 {QUEUE_DIR} 不存在。 Runner 將閒置。")
        return []
    return sorted([f for f in os.listdir(QUEUE_DIR) if f.endswith(".json")])

def move_task_file(task_filename, source_dir, dest_dir, new_filename_base=None):
    """
    Moves a task file between directories.
    If new_filename_base is provided, it's used as the base for the new filename,
    otherwise, the original task_filename is used.
    A timestamp is always appended to the filename in the destination.
    """
    source_path = os.path.join(source_dir, task_filename)
    timestamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S_%f')

    if new_filename_base:
        # new_filename_base is expected to be task_filename_original + status_suffix
        # e.g., "my_task.json.completed" or "my_task.json.failed"
        # task_filename is the current name in source_dir (e.g., "my_task.TIMESTAMP_IN_QUEUE.json")

        # temp_stem_from_new_base will be "my_task.json"
        # status_str will be ".completed"
        temp_stem_from_new_base, status_str = os.path.splitext(new_filename_base)

        # original_stem will be "my_task"
        # original_ext will be ".json"
        original_stem, original_ext = os.path.splitext(temp_stem_from_new_base)

        new_task_filename = f"{original_stem}{status_str}.{timestamp}{original_ext}"
    else:
        # No new_filename_base, usually for queue -> in_progress
        # task_filename is the original name like "my_task.json"
        base, ext = os.path.splitext(task_filename)
        new_task_filename = f"{base}.{timestamp}{ext}" # Results in "my_task.TIMESTAMP.json"

    dest_path = os.path.join(dest_dir, new_task_filename)

    try:
        os.makedirs(dest_dir, exist_ok=True)
        shutil.move(source_path, dest_path)
        logger.info(f"已移動任務 '{task_filename}' 從 '{source_dir}' 到 '{dest_path}'")
        return dest_path
    except Exception as e:
        logger.error(f"移動任務 '{task_filename}' 從 '{source_dir}' 到 '{dest_dir}' 時發生錯誤: {e}")
        return None

def execute_task_subprocess(task_config, task_filename_original, current_python_executable):
    """
    Executes a single task using subprocess.Popen.
    This function is designed to be called within a multiprocessing worker.
    Returns a dictionary with execution results.
    """
    app_name = task_config.get("app_name")
    params = task_config.get("params", {})
    result = {
        "success": False,
        "stdout": "",
        "stderr": "",
        "returncode": -1,
        "app_name": app_name,
        "original_filename": task_filename_original
    }

    if not app_name:
        error_msg = f"任務檔案 '{task_filename_original}' 缺少 'app_name'。"
        logger.error(error_msg)
        result["stderr"] = error_msg
        return result # Early exit

    app_script_path = os.path.join("apps", app_name, "run.py")

    if not os.path.exists(app_script_path):
        error_msg = f"應用程式腳本 '{app_script_path}' (app_name: '{app_name}') 未找到。"
        logger.error(error_msg)
        result["stderr"] = error_msg
        return result # Early exit

    command = [current_python_executable, app_script_path]
    for param_name, param_value in params.items():
        command.append(f"--{param_name.replace('_', '-')}")
        command.append(str(param_value))

    logger.info(f"子進程執行任務 '{task_filename_original}' (App: {app_name}): {' '.join(command)}")

    env = os.environ.copy()
    env["PYTHONPATH"] = "."

    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', env=env)
        stdout, stderr = process.communicate(timeout=task_config.get("timeout", 3600)) # Default timeout 1hr

        result["stdout"] = stdout
        result["stderr"] = stderr
        result["returncode"] = process.returncode

        if process.returncode == 0:
            logger.info(f"任務 '{task_filename_original}' (App: {app_name}) 成功完成。")
            result["success"] = True
        else:
            logger.error(f"任務 '{task_filename_original}' (App: {app_name}) 失敗，返回碼 {process.returncode}。")
    except subprocess.TimeoutExpired:
        logger.error(f"任務 '{task_filename_original}' (App: {app_name}) 執行超時。")
        result["stderr"] = f"執行超時 (timeout={task_config.get('timeout', 3600)}s)"
        result["returncode"] = -2 # Custom code for timeout
        # Ensure process is killed if timed out
        if 'process' in locals() and process.poll() is None:
            process.kill()
            process.communicate() # Clean up
    except Exception as e:
        logger.error(f"任務 '{task_filename_original}' (App: {app_name}) 執行期間發生例外: {e}", exc_info=True)
        result["stderr"] = str(e)
        result["returncode"] = -3 # Custom code for other exceptions

    return result

def process_single_task_wrapper(task_filepath_in_progress, task_filename_original, current_python_executable):
    """
    Wrapper function for multiprocessing pool.
    Reads task config, executes it, and returns all necessary info for post-processing.
    """
    logger.info(f"工作進程開始處理: {task_filename_original} (來自 {task_filepath_in_progress})")
    task_config = None
    read_error = False
    try:
        with open(task_filepath_in_progress, 'r', encoding='utf-8') as f:
            task_config = json.load(f)
    except Exception as e:
        logger.error(f"工作進程錯誤：讀取任務檔案 '{task_filepath_in_progress}' 失敗: {e}")
        read_error = True
        # This error needs to be propagated to the main process to handle the file
        # The file is already in IN_PROGRESS_DIR
        return {
            "success": False,
            "original_filename": task_filename_original,
            "in_progress_filepath": task_filepath_in_progress, # So main can move it
            "app_name": "N/A - Read Error",
            "status_suffix": ".read_error", # Suffix for the file when moved by main
            "stdout": "",
            "stderr": f"無法讀取任務檔案: {e}",
            "returncode": -100 # Custom code for read error
        }

    execution_result = execute_task_subprocess(task_config, task_filename_original, current_python_executable)

    # Combine results for the callback
    return {
        "success": execution_result["success"],
        "original_filename": task_filename_original,
        "in_progress_filepath": task_filepath_in_progress,
        "app_name": execution_result["app_name"],
        "status_suffix": ".completed" if execution_result["success"] else ".failed",
        "stdout": execution_result["stdout"],
        "stderr": execution_result["stderr"],
        "returncode": execution_result["returncode"]
    }

def handle_task_result(result):
    """Callback function to process results from worker processes."""
    global active_tasks_results # To remove the AsyncResult later, though direct removal is tricky with callbacks
                                # A better way is to check results in the main loop.
                                # For now, this callback handles file moving.

    task_filename_original = result["original_filename"]
    in_progress_filepath = result["in_progress_filepath"] # This is the file to move
    in_progress_filename = os.path.basename(in_progress_filepath) # Get the actual filename in in_progress

    app_name = result["app_name"]
    status_suffix = result["status_suffix"] # e.g., ".completed" or ".failed" or ".read_error"

    logger.info(f"處理任務 '{task_filename_original}' (App: {app_name}) 的結果: {'成功' if result['success'] else '失敗'}")

    if result["stdout"]:
        logger.info(f"--- STDOUT for {task_filename_original} (App: {app_name}) ---")
        for line in result["stdout"].splitlines():
            logger.info(line)
        logger.info(f"--- END STDOUT for {task_filename_original} (App: {app_name}) ---")

    if result["stderr"]:
        logger.error(f"--- STDERR for {task_filename_original} (App: {app_name}) ---")
        for line in result["stderr"].splitlines():
            logger.error(line)
        logger.error(f"--- END STDERR for {task_filename_original} (App: {app_name}) ---")


    if result["success"]:
        # Move from IN_PROGRESS_DIR to COMPLETED_DIR
        # The new_filename_base should be task_filename_original + status_suffix
        # e.g., original.json + .completed -> original.completed.timestamp.json
        move_task_file(in_progress_filename, IN_PROGRESS_DIR, COMPLETED_DIR, new_filename_base=task_filename_original + status_suffix)
    else:
        # Move from IN_PROGRESS_DIR to FAILED_DIR
        # e.g., original.json + .failed -> original.failed.timestamp.json
        # or original.json + .read_error -> original.read_error.timestamp.json
        move_task_file(in_progress_filename, IN_PROGRESS_DIR, FAILED_DIR, new_filename_base=task_filename_original + status_suffix)

    # The corresponding AsyncResult object should be removed from active_tasks_results
    # This is better done in the main loop by iterating over a copy.
    # For now, we assume the main loop will handle this.

def main_loop():
    """Main loop to monitor queue and process tasks using a multiprocessing pool."""
    global active_tasks_results # Ensure we're using the global list

    logger.info(f"Runner 啟動。使用 {MAX_CONCURRENT_APPS} 個併發工作進程。監控目錄: {QUEUE_DIR}")

    # Create directories if they don't exist
    for dir_path in [QUEUE_DIR, IN_PROGRESS_DIR, COMPLETED_DIR, FAILED_DIR, LOG_DIR]:
        os.makedirs(dir_path, exist_ok=True)

    with multiprocessing.Pool(processes=MAX_CONCURRENT_APPS) as pool:
        try:
            while True:
                # Clean up finished tasks from active_tasks_results
                # Iterate over a copy for safe removal
                for res_obj in active_tasks_results[:]:
                    if res_obj.ready():
                        try:
                            # Calling get() here to raise exceptions if any occurred in the callback
                            # or in the task function itself before the callback was called.
                            # The actual result handling (file moving) is done in the callback.
                            res_obj.get()
                        except Exception as e:
                            logger.error(f"從異步結果中獲取時發生錯誤 (任務可能已在回呼中處理): {e}", exc_info=True)
                        active_tasks_results.remove(res_obj)

                # Submit new tasks if pool has capacity
                num_active_workers = len(active_tasks_results)
                if num_active_workers < MAX_CONCURRENT_APPS:
                    task_files = get_task_files()
                    if task_files:
                        for task_filename in task_files:
                            if len(active_tasks_results) >= MAX_CONCURRENT_APPS:
                                break # Pool is full for now

                            logger.info(f"發現新任務: {task_filename}")

                            # Move to in_progress. The filename in in_progress will have a timestamp.
                            # The original task_filename is preserved for logging and naming in completed/failed.
                            in_progress_filepath = move_task_file(task_filename, QUEUE_DIR, IN_PROGRESS_DIR)

                            if in_progress_filepath:
                                logger.info(f"任務 '{task_filename}' 已移至 {in_progress_filepath}，準備提交給工作進程。")
                                async_result = pool.apply_async(
                                    process_single_task_wrapper,
                                    args=(in_progress_filepath, task_filename, PYTHON_EXECUTABLE), # Pass original filename
                                    callback=handle_task_result
                                )
                                active_tasks_results.append(async_result)
                            else:
                                logger.error(f"無法將任務 '{task_filename}' 移至 {IN_PROGRESS_DIR}。跳過。")

                            if len(active_tasks_results) >= MAX_CONCURRENT_APPS:
                                break # Re-check after adding a task
                    else: # No task files
                        if not active_tasks_results: # No tasks running and queue is empty
                            logger.debug(f"佇列為空且無活動任務，等待 {project_config.get('runner_settings',{}).get('queue_poll_interval_idle', 5)} 秒...")
                            time.sleep(project_config.get('runner_settings',{}).get('queue_poll_interval_idle', 5))
                        else: # Tasks running but queue is empty
                            logger.debug(f"佇列為空但有 {len(active_tasks_results)} 個活動任務，等待 {project_config.get('runner_settings',{}).get('queue_poll_interval_active', 1)} 秒...")
                            time.sleep(project_config.get('runner_settings',{}).get('queue_poll_interval_active', 1))

                else: # Pool is full
                    logger.debug(f"進程池已滿 ({len(active_tasks_results)}/{MAX_CONCURRENT_APPS} 個任務)，等待 {project_config.get('runner_settings',{}).get('queue_poll_interval_full', 1)} 秒...")
                    time.sleep(project_config.get('runner_settings',{}).get('queue_poll_interval_full', 1))

                # General short sleep if no specific longer sleep was done
                # time.sleep(0.1) # More responsive, but might be too busy for some cases.
                                # The sleeps above should cover most cases.

        except KeyboardInterrupt:
            logger.info("Runner 收到使用者中斷指令 (KeyboardInterrupt)。正在關閉...")
            logger.info("等待現有任務完成...")
            pool.close()  # Prevents any more tasks from being submitted to the pool.
            pool.join()   # Waits for the worker processes to exit.
            logger.info("所有工作進程已結束。")
        except Exception as e:
            logger.critical(f"Runner 遇到嚴重錯誤並將退出: {e}", exc_info=True)
            pool.terminate() # For critical errors, terminate immediately
            pool.join()
        finally:
            logger.info("Runner 正在關閉。")


if __name__ == "__main__":
    # --- Load Configuration ---
    try:
        project_config = load_project_config("config/project_config.yaml")
        runner_settings = project_config.get("runner_settings", {})
        PYTHON_EXECUTABLE = runner_settings.get("python_executable", DEFAULT_PYTHON_EXECUTABLE)
        MAX_CONCURRENT_APPS = runner_settings.get("max_concurrent_apps", DEFAULT_MAX_CONCURRENT_APPS)
        # Ensure MAX_CONCURRENT_APPS is at least 1
        if not isinstance(MAX_CONCURRENT_APPS, int) or MAX_CONCURRENT_APPS < 1:
            logger.warning(f"max_concurrent_apps 設定無效 ({MAX_CONCURRENT_APPS})，將使用預設值 {DEFAULT_MAX_CONCURRENT_APPS}。")
            MAX_CONCURRENT_APPS = DEFAULT_MAX_CONCURRENT_APPS
        else:
            # Respect os.cpu_count() if config value is higher and not explicitly forced
            cpu_count = os.cpu_count()
            if MAX_CONCURRENT_APPS > cpu_count :
                 logger.warning(f"設定的 max_concurrent_apps ({MAX_CONCURRENT_APPS}) 大於 CPU 核心數 ({cpu_count})。建議調整以獲得最佳效能。將限制為 {cpu_count}。")
                 MAX_CONCURRENT_APPS = cpu_count


    except FileNotFoundError:
        logger.error("錯誤：找不到設定檔 'config/project_config.yaml'。 Runner 將使用預設設定執行。")
        # Defaults are already set for PYTHON_EXECUTABLE and MAX_CONCURRENT_APPS
    except Exception as e:
        logger.error(f"載入設定檔時發生錯誤: {e}。 Runner 將使用預設設定執行。")
        # Defaults are already set

    # --- Setup File Logging for Runner ---
    os.makedirs(LOG_DIR, exist_ok=True)
    # Use the logger instance obtained from setup_logger
    log_filename = os.path.join(LOG_DIR, f"runner_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    file_handler = logging.FileHandler(log_filename, encoding='utf-8')
    # Use a similar format as defined in src/utils/logger.py for consistency
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - [%(levelname)s] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler) # Add file handler to the logger from setup_logger

    # Ensure event bus directories exist (moved to main_loop for clarity, but can be here too)
    # os.makedirs(QUEUE_DIR, exist_ok=True)
    # os.makedirs(IN_PROGRESS_DIR, exist_ok=True)
    # os.makedirs(COMPLETED_DIR, exist_ok=True)
    # os.makedirs(FAILED_DIR, exist_ok=True) # New failed directory

    main_loop()
