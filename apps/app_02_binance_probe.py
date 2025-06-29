import urllib.request
import json

def probe_binance_klines():
    """
    Connects to the Binance public API to fetch K-line (candlestick) data.
    This function uses only standard libraries and requires no API key.
    """
    # Endpoint for K-line data for BTC/USDT, 1-day interval, last 10 candles
    url = "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=10"
    print(f"Executing Binance Probe...\nTarget URL: {url}\n")

    try:
        # Set a user-agent to mimic a standard browser request
        headers = {'User-Agent': 'Mozilla/5.0'}
        req = urllib.request.Request(url, headers=headers)

        with urllib.request.urlopen(req) as response:
            if response.status == 200:
                print("Status: HTTP/1.1 200 OK\n")
                data = json.loads(response.read().decode('utf-8'))
                print("--- RAW JSON RESPONSE (first 500 chars) ---")
                print(str(data)[:500] + "...")
                print("\n--- PROBE SUCCESS ---")
            else:
                print(f"--- PROBE FAILED: Received HTTP Status Code {response.status} ---")

    except Exception as e:
        print(f"--- PROBE FAILED: An exception occurred ---")
        print(str(e))

if __name__ == "__main__":
    probe_binance_klines()
