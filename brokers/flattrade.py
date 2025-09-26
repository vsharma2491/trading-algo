import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Dict, Any, Optional, List
from brokers.base import BrokerBase
from logger import logger

from NorenRestApiPy.NorenApi import NorenApi

class FlattradeBroker(BrokerBase):
    """A broker class for the Flattrade API.

    This class handles authentication, order placement, and data retrieval
    for the Flattrade platform.
    """
    def __init__(self):
        """Initializes the FlattradeBroker."""
        super().__init__()
        logger.info("Initializing FlattradeBroker...")
        self.api = NorenApi()
        self.authenticate()

    def authenticate(self) -> Optional[str]:
        """Authenticates with the Flattrade API using a manual token flow.

        This method prompts the user to generate a session token by logging
        in through a browser.

        Returns:
            Optional[str]: The session token if authentication is successful,
                           otherwise None.
        """
        logger.info("Authenticating with Flattrade...")

        api_key = os.getenv("BROKER_API_KEY")
        api_secret = os.getenv("BROKER_API_SECRET")
        broker_id = os.getenv("BROKER_ID")

        if not all([api_key, api_secret, broker_id]):
            logger.error("Flattrade API key, secret, or user ID are not set in .env file.")
            return None

        login_url = f"https://auth.flattrade.in/?app_key={api_key}"
        print(f"Please login to Flattrade using this URL: {login_url}")
        print("After logging in, you will be redirected. Please paste the `request_token` from the redirected URL.")
        request_token = input("Request Token: ")

        # The Flattrade Python client does not expose a direct way to generate a session
        # from a request token. The intended flow is to run their token generator script.
        # For this integration, we will assume the user can provide the final session token.

        print("\nPlease provide the full session token (JWT) after completing the login flow.")
        session_token = input("Session Token: ")

        if not session_token:
            logger.error("Session token is required for Flattrade authentication.")
            return None

        # The set_session method is used to set the authenticated session.
        ret = self.api.set_session(userid=broker_id, password="", usertoken=session_token)

        if ret is not None and ret.get('stat') == 'Ok':
            logger.info("Flattrade authentication successful.")
            self.access_token = session_token
            self.authenticated = True
            return self.access_token
        else:
            logger.error(f"Flattrade authentication failed: {ret.get('emsg')}")
            self.authenticated = False
            return None

    def _get_token(self, exchange: str, symbol: str) -> Optional[str]:
        """Retrieves the instrument token for a given symbol.

        Args:
            exchange (str): The exchange where the symbol is traded (e.g., "NSE").
            symbol (str): The trading symbol.

        Returns:
            Optional[str]: The instrument token if found, otherwise None.
        """
        search_text = symbol
        if exchange == 'NSE' and '-EQ' not in symbol:
            search_text = f"{symbol}-EQ"

        ret = self.api.searchscrip(exchange=exchange, searchtext=search_text)
        if ret and ret.get('stat') == 'Ok' and ret.get('values'):
            for value in ret['values']:
                if value.get('tsym') == search_text:
                    return value.get('token')

        logger.error(f"Could not find token for {symbol} on {exchange}")
        return None

    def get_quote(self, symbol: str, exchange: str = 'NSE') -> Optional[Dict[str, Any]]:
        """Retrieves a real-time quote for a given symbol.

        Args:
            symbol (str): The trading symbol.
            exchange (str): The exchange where the symbol is traded. Defaults to 'NSE'.

        Returns:
            Optional[Dict[str, Any]]: The quote data, or None if not found.
        """
        token = self._get_token(exchange, symbol)
        if not token:
            return None
        return self.api.get_quotes(exchange=exchange, token=token)

    def get_historical_data(self, symbol: str, exchange: str, start_date: str, end_date: str, interval: str = '1') -> Optional[List[Dict[str, Any]]]:
        """Retrieves historical data for a given symbol.

        Args:
            symbol (str): The trading symbol.
            exchange (str): The exchange where the symbol is traded.
            start_date (str): The start date in "YYYY-MM-DD" format.
            end_date (str): The end date in "YYYY-MM-DD" format.
            interval (str): The candle interval in minutes. Defaults to '1'.

        Returns:
            Optional[List[Dict[str, Any]]]: A list of historical data points, or None.
        """
        from datetime import datetime
        token = self._get_token(exchange, symbol)
        if not token:
            return None

        try:
            start_timestamp = datetime.strptime(start_date, "%Y-%m-%d").timestamp()
            end_timestamp = datetime.strptime(end_date, "%Y-%m-%d").timestamp()
        except ValueError:
            logger.error("Invalid date format for historical data. Please use YYYY-MM-DD.")
            return None

        return self.api.get_time_price_series(
            exchange=exchange,
            token=token,
            starttime=start_timestamp,
            endtime=end_timestamp,
            interval=interval
        )

    def place_order(self, symbol: str, quantity: int, price: float, transaction_type: str, order_type: str, product: str, exchange: str = 'NSE', tag: str = "strategy") -> Optional[str]:
        """Places a trading order.

        Args:
            symbol (str): The trading symbol.
            quantity (int): The order quantity.
            price (float): The order price (for LIMIT and SL-LMT orders).
            transaction_type (str): 'BUY' or 'SELL'.
            order_type (str): 'MARKET', 'LIMIT', 'SL', or 'SL-M'.
            product (str): The product type (e.g., 'MIS', 'CNC').
            exchange (str): The exchange. Defaults to 'NSE'.
            tag (str): A tag for the order. Defaults to "strategy".

        Returns:
            Optional[str]: The order ID if successful, otherwise None.
        """
        logger.info(f"Placing order for {symbol} with quantity {quantity}")

        buy_or_sell = 'B' if transaction_type == 'BUY' else 'S'
        prd_type = 'M' if product == 'MIS' else 'C'
        price_type_map = {
            'MARKET': 'MKT',
            'LIMIT': 'LMT',
            'SL': 'SL-MKT',
            'SL-M': 'SL-LMT'
        }
        price_type = price_type_map.get(order_type, 'MKT')
        trigger_price = 0.0
        if order_type in ['SL', 'SL-M']:
            trigger_price = price
            if order_type == 'SL':
                price = 0.0

        ret = self.api.place_order(
            buy_or_sell=buy_or_sell,
            product_type=prd_type,
            exchange=exchange,
            tradingsymbol=symbol,
            quantity=quantity,
            discloseqty=0,
            price_type=price_type,
            price=price,
            trigger_price=trigger_price,
            retention='DAY',
            remarks=tag
        )

        if ret and ret.get('stat') == 'Ok' and ret.get('norenordno'):
            logger.info(f"Order placed successfully. Order ID: {ret['norenordno']}")
            return ret['norenordno']
        else:
            logger.error(f"Order placement failed: {ret.get('emsg')}")
            return None

    def get_positions(self) -> Optional[List[Dict[str, Any]]]:
        """Retrieves the current positions."""
        return self.api.get_positions()

    def get_orders(self) -> Optional[List[Dict[str, Any]]]:
        """Retrieves the order book for the day."""
        return self.api.get_order_book()

    # --- WebSocket Methods ---

    def connect_websocket(self):
        """Initializes and connects the WebSocket client.

        This method assigns all the `on_*` callbacks and starts the
        connection.
        """
        self.api.start_websocket(
            order_update_callback=self.on_order_update,
            subscribe_callback=self.on_ticks,
            socket_open_callback=self.on_connect,
            socket_close_callback=self.on_close,
            socket_error_callback=self.on_error
        )

    def subscribe(self, symbols: List[str], exchange: str = 'NSE'):
        """Subscribes to real-time data for a list of symbols.

        Args:
            symbols (List[str]): A list of trading symbols.
            exchange (str): The exchange of the symbols. Defaults to 'NSE'.
        """
        instrument_list = []
        for symbol in symbols:
            token = self._get_token(exchange, symbol)
            if token:
                instrument_list.append(f"{exchange}|{token}")

        if instrument_list:
            self.api.subscribe(instrument_list)

    def unsubscribe(self, symbols: List[str], exchange: str = 'NSE'):
        """Unsubscribes from real-time data for a list of symbols.

        Args:
            symbols (List[str]): A list of trading symbols to unsubscribe from.
            exchange (str): The exchange of the symbols. Defaults to 'NSE'.
        """
        instrument_list = []
        for symbol in symbols:
            token = self._get_token(exchange, symbol)
            if token:
                instrument_list.append(f"{exchange}|{token}")

        if instrument_list:
            self.api.unsubscribe(instrument_list)

    # --- WebSocket Callbacks ---

    def on_ticks(self, ticks):
        """Placeholder for handling incoming ticks."""
        logger.info(f"Ticks: {ticks}")

    def on_connect(self):
        """Placeholder for actions to be taken on WebSocket connection."""
        logger.info("WebSocket connected.")

    def on_close(self):
        """Placeholder for actions to be taken on WebSocket closure."""
        logger.info("WebSocket closed.")

    def on_error(self, err):
        """Placeholder for handling WebSocket errors."""
        logger.error(f"WebSocket error: {err}")

    def on_order_update(self, order_data):
        """Placeholder for handling order updates."""
        logger.info(f"Order Update: {order_data}")