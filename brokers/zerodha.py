import logging
import os, sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Dict, Any, Optional, List
import requests
import hashlib, pyotp
from dotenv import load_dotenv
from brokers.base import BrokerBase
from kiteconnect import KiteConnect, KiteTicker
import pandas as pd
from threading import Thread

from logger import logger


load_dotenv()


# --- Zerodha Broker ---
class ZerodhaBroker(BrokerBase):
    """A broker class for Zerodha Kite Connect API.

    This class handles authentication, order placement, and WebSocket
    connections for live data streaming with Zerodha.

    Attributes:
        without_totp (bool): If True, authentication requires manual entry of a
                             request token. Otherwise, it uses a TOTP-based flow.
        kite (KiteConnect): An instance of the Kite Connect API client.
        kite_ws (KiteTicker): An instance of the Kite Ticker for WebSocket data.
        symbols (list): A list of instrument tokens to subscribe to via WebSocket.
    """
    def __init__(self, without_totp: bool):
        """Initializes the ZerodhaBroker.

        Args:
            without_totp (bool): Determines the authentication method. If True,
                manual request token entry is required.
        """
        super().__init__()
        self.without_totp = without_totp
        self.kite, self.auth_response_data = self.authenticate()
        self.kite_ws = KiteTicker(api_key=os.getenv('BROKER_API_KEY'), access_token=self.auth_response_data["access_token"])
        self.tick_counter = 0
        self.symbols = []
        
    def authenticate(self) -> Tuple[KiteConnect, Dict[str, Any]]:
        """Authenticates with the Zerodha Kite API.

        Supports two authentication flows:
        1. With TOTP: A fully automated login using environment variables.
        2. Without TOTP: Requires manually generating and entering a request token.

        Returns:
            A tuple containing:
            - KiteConnect: The authenticated Kite Connect client instance.
            - Dict[str, Any]: The authentication response data.

        Raises:
            Exception: If authentication fails at any step.
        """
        api_key = os.getenv('BROKER_API_KEY')
        api_secret = os.getenv('BROKER_API_SECRET')
        broker_id = os.getenv('BROKER_ID')
        totp_secret = os.getenv('BROKER_TOTP_KEY')
        password = os.getenv('BROKER_PASSWORD')

        if self.without_totp:
            kite = KiteConnect(api_key=os.getenv('BROKER_API_KEY'))
            print(f"Please Login to Zerodha and get the request token from the URL.\n {kite.login_url()} \nThen paste the request token here:")
            request_token = input("Request Token: ")
            resp = kite.generate_session(request_token, os.environ['BROKER_API_SECRET'])
            return kite, resp
        

        if not all([api_key, api_secret, broker_id, totp_secret]):
            raise Exception("Missing one or more required environment variables.")

        session = requests.Session()

        # Step 1: Login 
        login_url = "https://kite.zerodha.com/api/login"
        login_payload = {
            "user_id": broker_id,
            "password": password,
        }
        login_resp = session.post(login_url, data=login_payload)
        login_data = login_resp.json()
        if not login_data.get("data"):
            raise Exception(f"Login failed: {login_data}")
        request_id = login_data["data"]["request_id"]

        # Step 2: TwoFA
        twofa_url = "https://kite.zerodha.com/api/twofa"
        twofa_payload = {
            "user_id": broker_id,
            "request_id": request_id,
            "twofa_value": pyotp.TOTP(totp_secret).now(),
            # "twofa_type": "totp",
        }
        twofa_resp = session.post(twofa_url, data=twofa_payload)
        twofa_data = twofa_resp.json()
        if not twofa_data.get("data"):
            raise Exception(f"2FA failed: {twofa_data}")

        kite = KiteConnect(api_key=os.getenv('BROKER_API_KEY'))
        # Step 3: Get request_token from redirect
        connect_url = f"https://kite.trade/connect/login?api_key={api_key}"
        connect_resp = session.get(connect_url, allow_redirects=True)
        if "request_token=" not in connect_resp.url:
            raise Exception("Failed to get request_token from redirect URL.")
        request_token = connect_resp.url.split("request_token=")[1].split("&")[0]

        resp = kite.generate_session(request_token, os.environ['BROKER_API_SECRET'])
        
        return kite, resp
    
    def get_orders(self) -> List[Dict[str, Any]]:
        """Retrieves a list of all orders for the day.

        Returns:
            List[Dict[str, Any]]: A list of order details.
        """
        return self.kite.orders()
    
    def get_quote(self, symbol: str, exchange: str) -> Dict[str, Any]:
        """Retrieves a real-time quote for a given symbol.

        Args:
            symbol (str): The trading symbol.
            exchange (str): The exchange where the symbol is traded (e.g., "NSE").

        Returns:
            Dict[str, Any]: The quote data from the Kite API.
        """
        if ":" not in symbol:   
            symbol = exchange + ":" + symbol
        return self.kite.quote(symbol)
    
    def place_gtt_order(self, symbol: str, quantity: int, price: float, transaction_type: str, order_type: str, exchange: str, product: str, tag: str = "Unknown") -> int:
        """Places a Good Till Triggered (GTT) order.

        Args:
            symbol (str): The trading symbol.
            quantity (int): The number of shares.
            price (float): The trigger price for the GTT order.
            transaction_type (str): "BUY" or "SELL".
            order_type (str): "LIMIT" or "MARKET".
            exchange (str): The exchange (e.g., "NSE").
            product (str): The product type (e.g., "CNC", "MIS").
            tag (str): An optional tag for the order.

        Returns:
            int: The trigger ID of the placed GTT order.

        Raises:
            ValueError: If the order or transaction type is invalid.
        """
        if order_type not in ["LIMIT", "MARKET"]:
            raise ValueError(f"Invalid order type: {order_type}")
        
        if transaction_type not in ["BUY", "SELL"]:
            raise ValueError(f"Invalid transaction type: {transaction_type}")
        
        order_obj = {
            "exchange": exchange,
            "tradingsymbol": symbol,
            "transaction_type": transaction_type,
            "quantity": quantity,
            "order_type": order_type,
            "product": product,
            "price": price,
            "tag": tag
        }
        last_price = self.get_quote(symbol, exchange)[exchange + ":" + symbol]['last_price']
        order_id = self.kite.place_gtt(trigger_type=self.kite.GTT_TYPE_SINGLE, tradingsymbol=symbol, exchange=exchange, trigger_values=[price], last_price=last_price, orders=[order_obj])
        return order_id['trigger_id']
    
    def place_order(self, symbol: str, quantity: int, price: float, transaction_type: str, order_type: str, variety: str, exchange: str, product: str, tag: str = "Unknown") -> int:
        """Places a regular trading order.

        Args:
            symbol (str): The trading symbol.
            quantity (int): The number of shares.
            price (float): The order price (required for LIMIT orders).
            transaction_type (str): "BUY" or "SELL".
            order_type (str): "LIMIT" or "MARKET".
            variety (str): The order variety (e.g., "REGULAR").
            exchange (str): The exchange (e.g., "NSE").
            product (str): The product type (e.g., "CNC", "MIS").
            tag (str): An optional tag for the order.

        Returns:
            int: The order ID if successful, otherwise -1.

        Raises:
            ValueError: If the order, transaction, or variety type is invalid.
        """
        if order_type == "LIMIT":
            order_type = self.kite.ORDER_TYPE_LIMIT
        elif order_type == "MARKET":
            order_type = self.kite.ORDER_TYPE_MARKET
        else:
            raise ValueError(f"Invalid order type: {order_type}")
        
        if transaction_type == "BUY":
            transaction_type = self.kite.TRANSACTION_TYPE_BUY
        elif transaction_type == "SELL":
            transaction_type = self.kite.TRANSACTION_TYPE_SELL
        else:
            raise ValueError(f"Invalid transaction type: {transaction_type}")
        
        if variety == "REGULAR":
            variety = self.kite.VARIETY_REGULAR
        else:
            raise ValueError(f"Invalid variety: {variety}")
        
        logger.info(f"Placing order for {symbol} with quantity {quantity} at {price} with order type {order_type} and transaction type {transaction_type}, variety {variety}, exchange {exchange}, product {product}, tag {tag}")
        order_attempt = 0
        try:
            while order_attempt < 5:
                order_id = self.kite.place_order(
                    variety=variety,
                    exchange=exchange,
                    tradingsymbol=symbol,
                    transaction_type=transaction_type,
                    quantity=quantity,
                    product=product,
                    order_type=order_type,
                    price=price if order_type == 'LIMIT' else None,
                    tag=tag
                )
                logger.info(f"Order placed: {order_id}")
                return order_id
            logger.error(f"Order placement failed after 5 attempts")
            return -1
        except Exception as e:
            logger.error(f"Order placement failed: {e}")
            return -1
    
    def get_quote(self, symbol: str) -> Dict[str, Any]:
        """Retrieves a real-time quote for a given symbol.

        Note:
            This is a duplicate method. The other `get_quote` method includes
            the exchange as a parameter.

        Args:
            symbol (str): The trading symbol (e.g., "NSE:RELIANCE").

        Returns:
            Dict[str, Any]: The quote data from the Kite API.
        """
        return self.kite.quote(symbol)
    

    def get_positions(self) -> Dict[str, List[Dict[str, Any]]]:
        """Retrieves the current holdings and positions.

        Returns:
            Dict[str, List[Dict[str, Any]]]: A dictionary containing lists of
                                             net and day positions.
        """
        return self.kite.positions()

    def symbols_to_subscribe(self, symbols: List[int]):
        """Sets the list of instrument tokens to subscribe to via WebSocket.

        Args:
            symbols (List[int]): A list of instrument tokens.
        """
        self.symbols = symbols

    ## Websocket Calllbacks
    def on_ticks(self, ws: KiteTicker, ticks: List[Dict[str, Any]]):
        """Handles incoming ticks from the WebSocket connection.

        This is a placeholder callback and should be implemented by the user
        to process real-time market data.

        Args:
            ws (KiteTicker): The WebSocket instance.
            ticks (List[Dict[str, Any]]): A list of ticks received.
        """
        logger.info("Ticks: {}".format(ticks))

    def on_connect(self, ws: KiteTicker, response: Dict[str, Any]):
        """Handles the successful connection to the WebSocket.

        This callback subscribes to the symbols defined in `self.symbols`
        upon a successful connection.

        Args:
            ws (KiteTicker): The WebSocket instance.
            response (Dict[str, Any]): The connection response.
        """
        logger.info("Connected")
        ws.subscribe(self.symbols)
        ws.set_mode(ws.MODE_FULL, self.symbols)


    def on_order_update(self, ws: KiteTicker, data: Dict[str, Any]):
        """Handles order update messages from the WebSocket.

        This is a placeholder callback for processing order updates.

        Args:
            ws (KiteTicker): The WebSocket instance.
            data (Dict[str, Any]): The order update data.
        """
        logger.info("Order update : {}".format(data))

    def on_close(self, ws: KiteTicker, code: int, reason: str):
        """Handles the closing of the WebSocket connection.

        Args:
            ws (KiteTicker): The WebSocket instance.
            code (int): The close status code.
            reason (str): The reason for closing.
        """
        logger.info("Connection closed: {code} - {reason}".format(code=code, reason=reason))


    def on_error(self, ws: KiteTicker, code: int, reason: str):
        """Handles WebSocket connection errors.

        Args:
            ws (KiteTicker): The WebSocket instance.
            code (int): The error code.
            reason (str): The error message.
        """
        logger.info("Connection error: {code} - {reason}".format(code=code, reason=reason))


    def on_reconnect(self, ws: KiteTicker, attempts_count: int):
        """Handles reconnection attempts.

        Args:
            ws (KiteTicker): The WebSocket instance.
            attempts_count (int): The number of reconnection attempts made.
        """
        logger.info("Reconnecting: {}".format(attempts_count))


    def on_noreconnect(self, ws: KiteTicker):
        """Handles failure to reconnect after all attempts.

        Args:
            ws (KiteTicker): The WebSocket instance.
        """
        logger.info("Reconnect failed.")
    
    def download_instruments(self):
        """Downloads the latest list of all available instruments.

        The instrument list is stored in a pandas DataFrame.
        """
        instruments = self.kite.instruments()
        self.instruments_df = pd.DataFrame(instruments)
    
    def get_instruments(self) -> pd.DataFrame:
        """Returns the DataFrame of available instruments.

        Returns:
            pd.DataFrame: A DataFrame containing instrument data.
        """
        return self.instruments_df
    
    def connect_websocket(self):
        """Initializes and connects the WebSocket client.

        This method assigns all the `on_*` callbacks to the KiteTicker
        instance and starts the connection in a separate thread.
        """
        self.kite_ws.on_ticks = self.on_ticks
        self.kite_ws.on_connect = self.on_connect
        self.kite_ws.on_order_update = self.on_order_update
        self.kite_ws.on_close = self.on_close
        self.kite_ws.on_error = self.on_error
        self.kite_ws.on_reconnect = self.on_reconnect
        self.kite_ws.on_noreconnect = self.on_noreconnect
        self.kite_ws.connect(threaded=True)
        
