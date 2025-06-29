import urllib.request
import json

DEFAULT_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'

def fetch_json_data(url: str, headers: dict = None) -> dict:
    """
    Fetches JSON data from a given URL.

    Args:
        url: The URL to fetch data from.
        headers: Optional dictionary of request headers.
                 If None, a default User-Agent will be used.

    Returns:
        A dictionary parsed from the JSON response.

    Raises:
        urllib.error.URLError: If there's an issue with the network or URL.
        urllib.error.HTTPError: If the server returns an HTTP error code.
        json.JSONDecodeError: If the response body is not valid JSON.
        Exception: For other unexpected errors during the process.
    """
    print(f"Attempting to fetch data from: {url}")

    effective_headers = {'User-Agent': DEFAULT_USER_AGENT}
    if headers:
        effective_headers.update(headers)

    req = urllib.request.Request(url, headers=effective_headers)

    try:
        with urllib.request.urlopen(req, timeout=10) as response: # Added timeout
            print(f"Received response with status: {response.status}")
            if response.status == 200:
                try:
                    data = json.loads(response.read().decode('utf-8'))
                    print("Successfully parsed JSON response.")
                    return data
                except json.JSONDecodeError as e:
                    print(f"JSONDecodeError: Failed to parse JSON response - {e}")
                    raise  # Re-raise the exception to be handled by the caller
            else:
                print(f"HTTPError: Received non-200 status code: {response.status}")
                # Raise an HTTPError for non-200 responses to allow specific handling
                raise urllib.error.HTTPError(url, response.status, response.reason, response.headers, response.fp)

    except urllib.error.HTTPError as e:
        # This block will catch HTTPError raised above (for non-200)
        # and also HTTPError from urlopen itself (e.g. 403, 404)
        print(f"HTTPError: {e.code} - {e.reason} while fetching {url}")
        raise
    except urllib.error.URLError as e:
        # Handles network errors (e.g., host not found, connection refused)
        print(f"URLError: {e.reason} while fetching {url}")
        raise
    except Exception as e:
        # Catch any other unexpected errors
        print(f"An unexpected error occurred: {e}")
        raise

if __name__ == '__main__':
    # Example usage for testing the module directly
    print("Testing api_client.py...")

    # Test 1: CoinGecko Ping (expected to succeed)
    test_url_coingecko = "https://api.coingecko.com/api/v3/ping"
    print(f"\n[Test 1] Fetching from CoinGecko: {test_url_coingecko}")
    try:
        data_cg = fetch_json_data(test_url_coingecko)
        print("CoinGecko Ping Response:", data_cg)
    except Exception as e:
        print(f"Error fetching CoinGecko Ping: {e}")

    # Test 2: TWSE MI_INDEX (expected to succeed)
    # Note: TWSE uses a different User-Agent in the original script,
    # but the default one here should also work for many public APIs.
    # If specific headers are needed, they can be passed.
    test_url_twse = "https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX"
    print(f"\n[Test 2] Fetching from TWSE: {test_url_twse}")
    try:
        # TWSE might sometimes be sensitive, let's use the specific headers if needed
        # For now, relying on the default User-Agent
        data_twse = fetch_json_data(test_url_twse)
        print("TWSE MI_INDEX Response (first 200 chars):", str(data_twse)[:200] + "...")
    except Exception as e:
        print(f"Error fetching TWSE MI_INDEX: {e}")

    # Test 3: Invalid URL (expected to fail with URLError)
    test_url_invalid = "https://api.nonexistentdomain12345.com/ping"
    print(f"\n[Test 3] Fetching from invalid URL: {test_url_invalid}")
    try:
        fetch_json_data(test_url_invalid)
    except urllib.error.URLError as e:
        print(f"Successfully caught expected URLError for invalid domain: {e}")
    except Exception as e:
        print(f"Unexpected error type for invalid domain: {e}")

    # Test 4: URL leading to HTTP 404 (expected to fail with HTTPError)
    test_url_404 = "https://api.coingecko.com/api/v3/nonexistent_endpoint"
    print(f"\n[Test 4] Fetching from non-existent endpoint (404): {test_url_404}")
    try:
        fetch_json_data(test_url_404)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"Successfully caught expected HTTPError 404: {e}")
        else:
            print(f"Caught HTTPError but with unexpected code {e.code}: {e}")
    except Exception as e:
        print(f"Unexpected error type for 404 test: {e}")

    # Test 5: URL that does not return JSON (expected to fail with JSONDecodeError)
    test_url_not_json = "https://www.google.com" # Google's homepage is HTML, not JSON
    print(f"\n[Test 5] Fetching non-JSON content from: {test_url_not_json}")
    try:
        fetch_json_data(test_url_not_json)
    except json.JSONDecodeError as e:
        print(f"Successfully caught expected JSONDecodeError: {e}")
    except urllib.error.HTTPError as e:
        # It's possible Google might return a non-200 for bot-like access,
        # or the content type isn't application/json which might lead to other issues.
        print(f"Caught HTTPError instead of JSONDecodeError for non-JSON test: {e.code} - {e.reason}. This can happen if the server restricts access or content type is not as expected.")
    except Exception as e:
        print(f"Unexpected error type for non-JSON test: {e}")

    print("\napi_client.py testing complete.")
