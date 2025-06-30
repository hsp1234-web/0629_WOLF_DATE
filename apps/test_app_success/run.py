import time
import sys
import argparse

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test App Success")
    parser.add_argument("--task-name", type=str, default="Unknown Task", help="Name of the task")
    parser.add_argument("--duration", type=int, default=5, help="Duration to sleep in seconds")
    args = parser.parse_args()

    print(f"SUCCESS APP: Starting task {args.task_name}.")
    sys.stdout.flush()
    time.sleep(args.duration)
    print(f"SUCCESS APP: Task {args.task_name} completed after {args.duration} seconds.")
    sys.stdout.flush()
    sys.exit(0)
