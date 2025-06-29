import sys
import os

# Add parent directory of 'utils' to Python path if 'utils' is in a sibling directory
# This allows importing from utils.api_client
# Adjust if your project structure is different
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
# Assuming 'utils' is in the same directory as 'apps' parent, i.e., project root
# If 'utils' is directly inside 'apps' parent (e.g. project_root/utils), this is correct.
# If 'utils' is at the same level as 'apps' (e.g. project_root/module/apps and project_root/module/utils)
# then parent_dir needs to be adjusted, or a more robust path handling is needed (e.g. setting PYTHONPATH)
# For this specific case, assuming utils is at project_root/utils
# and apps is at project_root/apps
# So, we need to go one level up from 'apps' to reach the project root where 'utils' can be found.
project_root = parent_dir
sys.path.append(project_root)

try:
    from utils.api_client import fetch_json_data, DEFAULT_USER_AGENT
except ImportError:
    print("Error: Unable to import 'fetch_json_data' from 'utils.api_client'.")
    print("Ensure 'utils' directory is in the Python path and api_client.py exists.")
    print(f"Current sys.path: {sys.path}")
    sys.exit(1)

def probe_twse_market_summary_refactored():
    """
    Connects to the TWSE OpenAPI to fetch the daily market summary,
    using the centralized api_client.
    """
    url = "https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX"
    print(f"Executing TWSE Probe (Refactored)...\nTarget URL: {url}\n")

    # TWSE historically might be sensitive to User-Agent.
    # The original script used 'Mozilla/5.0'.
    # DEFAULT_USER_AGENT in api_client is 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)...'
    # which should generally be fine. If issues arise, a specific header can be passed.
    # For now, we'll rely on the default User-Agent set in fetch_json_data.
    # headers = {'User-Agent': 'Mozilla/5.0'} # Example if specific header needed

    try:
        data = fetch_json_data(url) # No specific headers, will use default from api_client
        if data:
            print("Status: HTTP/1.1 200 OK (assumed by successful fetch_json_data)\n")
            print("--- RAW JSON RESPONSE (first 500 chars) ---")
            print(str(data)[:500] + "...")
            print("\n--- PROBE SUCCESS (Refactored) ---")
        # fetch_json_data will raise exceptions on failure, so no 'else' needed here
        # for HTTP status codes other than 200, as those are handled in api_client.

    except Exception as e:
        # The fetch_json_data function already prints detailed error info.
        # We can add a more general message here or re-raise if needed.
        print(f"--- PROBE FAILED (Refactored): An exception occurred ---")
        # The specific exception (URLError, HTTPError, JSONDecodeError) was already printed by api_client
        # print(str(e)) # This would print it again, perhaps not needed.

if __name__ == "__main__":
    probe_twse_market_summary_refactored()
