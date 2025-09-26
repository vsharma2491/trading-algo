import os
import sys
import json
import time
import threading
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse
import requests
import pyotp
import base64
import subprocess
import logging
import hashlib
from fyers_apiv3 import fyersModel
from fyers_apiv3.FyersWebsocket import data_ws
from ratelimit import limits, sleep_and_retry
import functools
from typing import Dict, List, Optional, Any, Tuple

# Import base broker classes
from .base import BrokerBase

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)

from dotenv import load_dotenv
load_dotenv()


# Rate limiting configuration for Fyers API
# Per Second: 10, Per Minute: 200, Per Day: 100000
def fyers_rate_limit(func):
    """A decorator to enforce Fyers API rate limits.

    This decorator applies multiple rate-limiting rules to a function:
    - 10 calls per second
    - 200 calls per minute
    - 100,000 calls per day

    If a limit is exceeded, the decorator will pause the execution and retry.

    Args:
        func (callable): The function to be rate-limited.

    Returns:
        callable: The wrapped function with rate limiting applied.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        logger.debug(f"Rate limiting applied to {func.__name__}")
        return func(*args, **kwargs)
    
    # Apply the rate limiting decorators
    wrapper = sleep_and_retry(limits(calls=10, period=1))(wrapper)
    wrapper = sleep_and_retry(limits(calls=200, period=60))(wrapper)
    wrapper = sleep_and_retry(limits(calls=100000, period=86400))(wrapper)
    
    return wrapper

def getEncodedString(string: str) -> str:
    """Encodes a string to a Base64 ASCII string.

    Args:
        string (str): The string to encode.

    Returns:
        str: The Base64 encoded string.
    """
    return base64.b64encode(str(string).encode("ascii")).decode("ascii")


class FyersBroker(BrokerBase):
    """A broker class for Fyers with REST and WebSocket capabilities.

    This class handles both RESTful API calls for historical data, quotes, and
    margin details, as well as WebSocket connections for live data streaming.

    Attributes:
        access_token (str): The authenticated access token.
        auth_response_data (dict): The full response from the authentication endpoint.
        fyers_model (fyersModel.FyersModel): The Fyers API model instance.
        symbols (list): A list of symbols for WebSocket subscription.
        data_type (str): The type of data to subscribe to via WebSocket.
        ws (data_ws.FyersDataSocket): The WebSocket instance.
    """

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        data_type: str = "SymbolUpdate",
        log_path: str = "",
        litemode: bool = False,
        write_to_file: bool = False,
        reconnect: bool = True,
        data_handler: Optional[Any] = None,
    ):
        """Initializes the FyersBroker.

        Args:
            symbols (Optional[List[str]]): A list of symbols for WebSocket
                subscription. Defaults to a sample list.
            data_type (str): The type of data for WebSocket subscription
                (e.g., "SymbolUpdate"). Defaults to "SymbolUpdate".
            log_path (str): The file path for logging. Defaults to "".
            litemode (bool): Whether to use litemode for WebSocket. Defaults to False.
            write_to_file (bool): Whether to write WebSocket data to a file.
                Defaults to False.
            reconnect (bool): Whether to automatically reconnect the WebSocket.
                Defaults to True.
            data_handler (Optional[Any]): A handler to process incoming
                WebSocket messages. Defaults to None.
        """
        logger.info("Initializing FyersBroker...")
        self.access_token, self.auth_response_data = self.authenticate()
        self.fyers_model = fyersModel.FyersModel(
            client_id=os.environ["BROKER_API_KEY"],
            token=self.access_token,
            is_async=False,
            log_path=os.getcwd(),
        )
        self._init_context()

        # WebSocket parameters
        self.symbols = symbols or ["NSE:SBIN-EQ", "NSE:ADANIENT-EQ"]
        self.data_type = data_type
        self.log_path = log_path
        self.litemode = litemode
        self.write_to_file = write_to_file
        self.reconnect = reconnect
        self.data_handler = data_handler
        self.ws = None  # Placeholder for the WebSocket instance

        # === Begin Benchmark Tracking Changes ===
        self._benchmark = False
        # Dictionary to count messages per ticker in the current second.
        self.ticker_second_counts = {}
        # Cumulative accumulators over a 1-minute window.
        self.minute_seconds_count = 0
        self.cumulative_distinct_tickers = 0
        self.cumulative_ticker_counts = {}
        # Lock to avoid race conditions.
        self.benchmark_lock = threading.Lock()
        if self._benchmark:
            # Start background threads to aggregate per-second counts and print per-minute averages.
            threading.Thread(target=self._aggregate_second, daemon=True).start()
            threading.Thread(target=self._benchmark_minute, daemon=True).start()
        # === End Benchmark Tracking Changes ===
    
    def authenticate(self) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        """Authenticates with the Fyers API using a TOTP-based flow.

        This method performs a multi-step authentication process to obtain an
        access token. It requires environment variables for credentials.

        Returns:
            A tuple containing:
            - Optional[str]: The access token if authentication is successful,
              otherwise None.
            - Optional[Dict[str, Any]]: A dictionary with authentication
              response data, including status and messages.
        """
        response_data = {
            'status': 'error',
            'message': 'Authentication failed',
            'data': None
        }
        try:
            # Required env vars
            fy_id = os.environ['BROKER_ID']
            totp_key = os.environ['BROKER_TOTP_KEY']
            pin = os.environ['BROKER_TOTP_PIN']
            client_id = os.environ['BROKER_API_KEY']
            secret_key = os.environ['BROKER_API_SECRET']
            redirect_uri = os.environ['BROKER_TOTP_REDIDRECT_URI']
            response_type = "code" # Should be always `code`
            grant_type = "authorization_code" # Should be always `authorization_code`
            # Step 1: Send login OTP
            URL_SEND_LOGIN_OTP = "https://api-t2.fyers.in/vagator/v2/send_login_otp_v2"
            res = requests.post(url=URL_SEND_LOGIN_OTP, json={
                "fy_id": getEncodedString(fy_id),
                "app_id": "2"
            }).json()
            if datetime.now().second % 30 > 27:
                time.sleep(5)
            # Step 2: Verify OTP
            URL_VERIFY_OTP = "https://api-t2.fyers.in/vagator/v2/verify_otp"
            res2 = requests.post(url=URL_VERIFY_OTP, json={
                "request_key": res["request_key"],
                "otp": pyotp.TOTP(totp_key).now()
            }).json()
            # Step 3: Verify PIN
            ses = requests.Session()
            URL_VERIFY_OTP2 = "https://api-t2.fyers.in/vagator/v2/verify_pin_v2"
            payload2 = {
                "request_key": res2["request_key"],
                "identity_type": "pin",
                "identifier": getEncodedString(pin)
            }
            res3 = ses.post(url=URL_VERIFY_OTP2, json=payload2).json()
            ses.headers.update({
                'authorization': f"Bearer {res3['data']['access_token']}"
            })
            # Step 4: Get auth code
            TOKENURL = "https://api-t1.fyers.in/api/v3/token"
            payload3 = {
                "fyers_id": fy_id,
                "app_id": client_id[:-4],
                "redirect_uri": redirect_uri,
                "appType": "100",
                "code_challenge": "",
                "state": "None",
                "scope": "",
                "nonce": "",
                "response_type": "code",
                "create_cookie": True
            }
            res4 = ses.post(url=TOKENURL, json=payload3).json()
            parsed = urlparse(res4['Url'])
            auth_code = parse_qs(parsed.query)['auth_code'][0]
            # Step 5: Exchange auth code for access token
            import hashlib
            url = 'https://api-t1.fyers.in/api/v3/validate-authcode'
            checksum_input = f"{client_id}:{secret_key}"
            app_id_hash = hashlib.sha256(checksum_input.encode('utf-8')).hexdigest()
            payload = {
                'grant_type': grant_type,
                'appIdHash': app_id_hash,
                'code': auth_code
            }
            headers = {
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            }
            response = ses.post(url, headers=headers, json=payload, timeout=30.0)
            response.raise_for_status()
            auth_data = response.json()
            if auth_data.get('s') == 'ok':
                access_token = auth_data.get('access_token')
                if not access_token:
                    response_data['message'] = "Authentication succeeded but no access token was returned"
                    return None, response_data
                response_data.update({
                    'status': 'success',
                    'message': 'Authentication successful',
                    'data': {
                        'access_token': access_token,
                        'refresh_token': auth_data.get('refresh_token'),
                        'expires_in': auth_data.get('expires_in')
                    }
                })
                return access_token, response_data
            else:
                error_msg = auth_data.get('message', 'Authentication failed')
                response_data['message'] = f"API error: {error_msg}"
                return None, response_data
        except Exception as e:
            response_data['message'] = f"Authentication failed: {str(e)}"
            return None, response_data


    # === Begin Benchmark Aggregation Method ===
    def _aggregate_second(self):
        """Accumulate per-second data and update cumulative counters."""
        while True:
            time.sleep(1)  # Wait for one second interval
            with self.benchmark_lock:
                # Snapshot and reset the per-second ticker counts.
                current_counts = self.ticker_second_counts
                self.ticker_second_counts = {}
            # Compute distinct tickers in this second.
            distinct_this_second = len(current_counts)
            with self.benchmark_lock:
                self.minute_seconds_count += 1
                self.cumulative_distinct_tickers += distinct_this_second
                # For each ticker, update cumulative count.
                for ticker, count in current_counts.items():
                    self.cumulative_ticker_counts[ticker] = (
                        self.cumulative_ticker_counts.get(ticker, 0) + count
                    )

    # === End Benchmark Aggregation Method ===

    # === Begin Benchmark Reporting Method ===
    def _benchmark_minute(self):
        """Every minute, compute and print the average distinct tickers per second and average messages per ticker per second."""
        while True:
            time.sleep(60)  # One-minute interval
            with self.benchmark_lock:
                if self.minute_seconds_count == 0:
                    continue  # Avoid division by zero
                avg_distinct = (
                    self.cumulative_distinct_tickers / self.minute_seconds_count
                )
                report_lines = []
                report_lines.append("Benchmark (over last minute):")
                report_lines.append(
                    f"Average distinct tickers per second: {avg_distinct:.2f}"
                )
                tickers_counts = 0
                total_counts = 0
                for ticker, total_count in self.cumulative_ticker_counts.items():
                    if total_count > 0:
                        tickers_counts += 1
                        total_counts += total_count

                avg_msgs = total_counts / self.minute_seconds_count
                report_lines.append(
                    f"Summary Records per Second\t {avg_msgs:.2f} from {tickers_counts} tickers - {total_counts} records in {self.minute_seconds_count} seconds"
                )
                print("\n" + "\n".join(report_lines))
                # Reset cumulative counters for the next minute.
                self.minute_seconds_count = 0
                self.cumulative_distinct_tickers = 0
                self.cumulative_ticker_counts = {}

    # === End Benchmark Reporting Method ===

    def _init_context(self):
        """Initialize context for tracking API calls."""
        if os.path.exists("FyersModel.json"):
            with open("FyersModel.json", "r") as f:
                self.context = json.load(f)
            if self.context.get("DATE") != str(datetime.now().date()):
                self._create_context()
        else:
            self._create_context()

    def _create_context(self):
        self.context = {"TOTAL_API_CALLS": 0, "DATE": str(datetime.now().date())}
        with open("FyersModel.json", "w") as f:
            json.dump(self.context, f)

    def update_context(self):
        self.context["TOTAL_API_CALLS"] += 1
        self.context["DATE"] = str(datetime.now().date())
        with open("FyersModel.json", "w") as f:
            json.dump(self.context, f)

    def get_access_token(self) -> Optional[str]:
        """Returns the authenticated access token.

        Returns:
            Optional[str]: The access token, or None if not authenticated.
        """
        return self.access_token

    # REST-based data retrieval methods
    @fyers_rate_limit
    def get_history(self, symbol: str, resolution: str, start_date: str, end_date: str, oi_flag: bool = False) -> Dict[str, Any]:
        """Retrieves historical data by breaking requests into smaller chunks.

        This method handles Fyers API limitations by automatically splitting
        the date range into smaller intervals based on the data resolution.

        Args:
            symbol (str): The trading symbol (e.g., "NSE:SBIN-EQ").
            resolution (str): The timeframe resolution (e.g., "1", "5", "D").
            start_date (str): The start date in "YYYY-MM-DD" format.
            end_date (str): The end date in "YYYY-MM-DD" format.
            oi_flag (bool): Whether to fetch open interest data. Defaults to False.

        Returns:
            Dict[str, Any]: A dictionary containing the combined historical
                            data or an error message.
        """
        # Format symbol if needed
        formatted_symbol = (
            f"NSE:{symbol}-EQ" if not symbol.startswith("NSE") else symbol
        )

        # Convert string dates to datetime objects
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")

        # Determine chunk size based on resolution
        if resolution in ["D", "1D"]:
            # For daily resolution: up to 366 days per request
            max_days = 366
        elif resolution in ["5S", "10S", "15S", "30S", "45S"]:
            # For seconds resolution: up to 30 trading days
            max_days = 30
        else:
            # For minute resolutions: up to 100 days per request
            max_days = 100

        # Initialize result container
        all_candles = []

        # Break the date range into chunks
        current_start = start_dt
        while current_start <= end_dt:
            # Calculate end date for this chunk
            current_end = min(current_start + timedelta(days=max_days - 1), end_dt)

            # Format dates for API request
            chunk_start = current_start.strftime("%Y-%m-%d")
            chunk_end = current_end.strftime("%Y-%m-%d")

            logger.info(
                f"Fetching {formatted_symbol} data from {chunk_start} to {chunk_end} with resolution {resolution}"
            )

            # Prepare request parameters
            data_headers = {
                "symbol": formatted_symbol,
                "resolution": resolution,
                "date_format": "1",
                "range_from": chunk_start,
                "range_to": chunk_end,
                "cont_flag": "1"
            }
            if oi_flag:
                data_headers["oi_flag"] = "1"
            # Make the API call
            chunk_data = self.fyers_model.history(data_headers)
            self.update_context()

            # Check if we got valid data
            if "candles" in chunk_data and len(chunk_data["candles"]) > 0:
                all_candles.extend(chunk_data["candles"])
            else:
                # logger.warning(f"No data returned for {formatted_symbol} from {chunk_start} to {chunk_end}")
                pass
            # Add a small delay to avoid rate limiting
            time.sleep(0.5)

            # Move to next chunk
            current_start = current_end + timedelta(days=1)

        # Return combined result
        if not all_candles:
            # logger.warning(f"No historical data returned for {symbol} from {start_date} to {end_date}.")
            return {"s": "no_data", "candles": []}

        return {"s": "ok", "candles": all_candles}
    
    @fyers_rate_limit
    def get_option_chain(self, data: dict, strikecount: int = 5) -> Dict[str, Any]:
        """Retrieves the option chain for a given underlying symbol.

        Args:
            data (dict): A dictionary containing the symbol for the option chain.
            strikecount (int): The number of strikes to retrieve above and
                below the current price. Defaults to 5.

        Returns:
            Dict[str, Any]: The option chain data from the Fyers API.
        """
        data["strikecount"] = strikecount
        result = self.fyers_model.optionchain(data)
        self.update_context()
        return result
    

    @fyers_rate_limit
    def get_quotes(self, data: dict) -> Dict[str, Any]:
        """Retrieves real-time quotes for one or more symbols.

        Args:
            data (dict): A dictionary containing the list of symbols.

        Returns:
            Dict[str, Any]: The quote data from the Fyers API.
        """
        result = self.fyers_model.quotes(data)
        self.update_context()
        return result

    @fyers_rate_limit
    def get_margin(self, symbols: list, use_curl: bool = True) -> Dict[str, Any]:
        """Calculates and retrieves margin details for a list of symbols.

        Args:
            symbols (list): A list of trading symbols.
            use_curl (bool): If True, uses cURL for the request; otherwise,
                uses the requests library. Defaults to True.

        Returns:
            Dict[str, Any]: A dictionary containing margin information for each
                            symbol or an error message.
        """
        url = "https://api-t1.fyers.in/api/v3/multiorder/margin"
        headers = {
            "Authorization": f"{os.environ['BROKER_API_KEY']}:{self.access_token}",
            "Content-Type": "application/json",
        }
        data = {"symbols": ",".join(symbols)}
        fyers = fyersModel.FyersModel(
            client_id=os.environ["BROKER_API_KEY"], token=self.access_token, is_async=False, log_path=""
        )
        MARGIN_DICT = {}
        # while True:
        response_q = fyers.quotes(data=data)
        for i, symbol in enumerate(symbols):
            order_template = [
                {
                    "symbol": symbol,
                    "qty": 1,
                    "side": 1,
                    "type": 2,
                    "productType": "INTRADAY",
                    "limitPrice": 0.0,
                    "stopLoss": 0.0,
                    "stopPrice": 0.0,
                    "takeProfit": 0.0,
                }
            ]

            payload = json.dumps({"data": order_template})
            if use_curl:
                curl_command = [
                    "curl",
                    "--location",
                    "--request",
                    "POST",
                    url,
                    "--header",
                    f"Authorization: {headers['Authorization']}",
                    "--header",
                    "Content-Type: application/json",
                    "--data-raw",
                    payload,
                ]

                try:
                    result = subprocess.run(
                        curl_command, capture_output=True, text=True, check=True
                    )
                    try:
                        MARGIN_DICT[symbol] = round(
                            response_q["d"][i]["v"]["lp"]
                            / json.loads(result.stdout)["data"]["margin_total"]
                        )
                    except:
                        MARGIN_DICT[symbol] = 1
                except subprocess.CalledProcessError as e:
                    return {"error": e.stderr}

            else:
                try:
                    response = requests.post(url, headers=headers, data=payload)
                    response.raise_for_status()
                    MARGIN_DICT[symbol] = round(
                        response_q["d"][i]["v"]["lp"]
                        / response.json()["data"]["margin_total"]
                    )
                except requests.exceptions.RequestException as e:
                    return {"error": str(e)}
            time.sleep(1)
        return MARGIN_DICT


    @fyers_rate_limit
    def get_span_margin(self, order_data: List[Dict[str, Any]], use_curl: bool = False) -> Dict[str, Any]:
        """Calculates span and exposure margin for a list of orders.

        Args:
            order_data (List[Dict[str, Any]]): A list of order details.
            use_curl (bool): If True, uses cURL for the request. Defaults to False.

        Returns:
            Dict[str, Any]: The API response with margin details or an error.
        """
        url = "https://api.fyers.in/api/v2/span_margin"
        headers = {
            "Authorization": f"{self.fyers_model.client_id}:{self.access_token}",
            "Content-Type": "application/json",
        }
        payload = json.dumps({"data": order_data})
        try:
            if use_curl:
                curl_command = [
                    "curl", "--location", "--request", "POST", url,
                    "--header", f"Authorization: {headers['Authorization']}",
                    "--header", "Content-Type: application/json",
                    "--data-raw", payload,
                ]
                result = subprocess.run(curl_command, capture_output=True, text=True, check=True)
                return json.loads(result.stdout)
            else:
                response = requests.post(url, headers=headers, data=payload)
                response.raise_for_status()
                return response.json()
        except Exception as e:
            logger.error(f"Error in FyersBroker.get_span_margin: {e}")
            return {"error": str(e)}

    @fyers_rate_limit
    def get_multiorder_margin(self, order_data: List[Dict[str, Any]], use_curl: bool = False) -> Dict[str, Any]:
        """Calculates the margin required for a list of orders.

        This method uses the Fyers Multiorder Margin API to calculate the
        total margin for a batch of orders.

        Args:
            order_data (List[Dict[str, Any]]): A list of order details.
            use_curl (bool): If True, uses cURL for the request. Defaults to False.

        Returns:
            Dict[str, Any]: The API response with margin details or an error.
        """
        url = "https://api-t1.fyers.in/api/v3/multiorder/margin"
        headers = {
            "Authorization": f"{self.fyers_model.client_id}:{self.access_token}",
            "Content-Type": "application/json",
        }
        payload = json.dumps({"data": order_data})
        try:
            if use_curl:
                curl_command = [
                    "curl", "--location", "--request", "POST", url,
                    "--header", f"Authorization: {headers['Authorization']}",
                    "--header", "Content-Type: application/json",
                    "--data-raw", payload,
                ]
                result = subprocess.run(curl_command, capture_output=True, text=True, check=True)
                return json.loads(result.stdout)
            else:
                response = requests.post(url, headers=headers, data=payload)
                response.raise_for_status()
                return response.json()
        except Exception as e:
            logger.error(f"Error in FyersBroker.get_multiorder_margin: {e}")
            return {"error": str(e)}

    # WebSocket-based live data methods
    def connect_websocket(self) -> data_ws.FyersDataSocket:
        """Establishes a WebSocket connection for live data streaming.

        This method initializes and connects the Fyers WebSocket client using
        the parameters provided during the broker's initialization.

        Returns:
            data_ws.FyersDataSocket: The connected WebSocket instance.
        """
        self.ws = data_ws.FyersDataSocket(
            access_token=self.access_token,
            log_path=self.log_path,
            litemode=self.litemode,
            write_to_file=self.write_to_file,
            reconnect=self.reconnect,
            on_connect=self._on_ws_open,
            on_close=self._on_ws_close,
            on_message=self._on_ws_message,
        )
        self.ws.connect()
        return self.ws

    def _on_ws_message(self, message):
        """
        Internal callback for handling WebSocket messages.
        """
        # Process the message; if a data handler is provided, pass the data.
        print(message)
        if "symbol" in message:
            if self._benchmark:
                with self.benchmark_lock:
                    self.ticker_second_counts[message["symbol"]] = (
                        self.ticker_second_counts.get(message["symbol"], 0) + 1
                    )
            if self.data_handler:
                self.data_handler.data_queue.put(message)
            else:
                # print(message)
                pass

    def _on_ws_close(self, message):
        """
        Internal callback for handling WebSocket closure.
        """
        print("WebSocket connection closed:", message)

    def _on_ws_open(self):
        """
        Internal callback for handling WebSocket connection open event.
        """
        print("WebSocket connection opened. Subscribing to symbols.")
        self.ws.subscribe(symbols=self.symbols, data_type=self.data_type)
        self.ws.keep_running()
