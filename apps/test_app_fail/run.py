import time
import sys
import argparse

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test App Fail")
    parser.add_argument("--task-name", type=str, default="Unknown Task", help="Name of the task")
    args = parser.parse_args()

    print(f"FAIL APP: Starting task {args.task_name}.", file=sys.stdout)
    sys.stdout.flush()
    time.sleep(1)
    print(f"FAIL APP: Task {args.task_name} is about to fail.", file=sys.stderr)
    sys.stderr.flush()
    sys.exit(1)
