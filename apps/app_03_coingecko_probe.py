import sys
import os

# Add parent directory of 'utils' to Python path
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
project_root = parent_dir
sys.path.append(project_root)

try:
    from utils.api_client import fetch_json_data
except ImportError:
    print("Error: Unable to import 'fetch_json_data' from 'utils.api_client'.")
    print("Ensure 'utils' directory is in the Python path and api_client.py exists.")
    sys.exit(1)

def probe_coingecko_ping_refactored():
    """
    Connects to the CoinGecko public API to check server status,
    using the centralized api_client.
    """
    url = "https://api.coingecko.com/api/v3/ping"
    print(f"Executing CoinGecko Probe (Refactored)...\nTarget URL: {url}\n")

    try:
        data = fetch_json_data(url)
        if data:
            print("Status: HTTP/1.1 200 OK (assumed by successful fetch_json_data)\n")
            print("--- RAW JSON RESPONSE ---")
            print(data)
            print("\n--- PROBE SUCCESS (Refactored) ---")
        # fetch_json_data handles and prints errors internally

    except Exception as e:
        print(f"--- PROBE FAILED (Refactored): An exception occurred ---")
        # Error details are already printed by fetch_json_data

if __name__ == "__main__":
    probe_coingecko_ping_refactored()
