import json
import os
from datetime import datetime
from typing import Dict, List, Optional
from logger import logger


class OrderTracker:
    """Manages the lifecycle of trading orders, including tracking and persistence.

    This class stores all orders in a dictionary, tracks their completion
    status, and persists them to a JSON file. It ensures that order data
is loaded upon initialization and saved whenever it changes.

    Attributes:
        orders_file (str): The path to the JSON file where orders are stored.
        _all_orders (Dict[str, Dict]): A dictionary mapping order IDs to order details.
        _current_order (Optional[Dict]): The most recently added order.
        _order_ids_completed (List[str]): A list of completed order IDs.
        _order_types_summary (Dict[str, int]): A summary count of completed
                                               orders by transaction type.
    """
    def __init__(self, orders_file: str = 'artifacts/orders_data.json'):
        """Initializes the OrderTracker.

        Args:
            orders_file (str): The path to the JSON file for storing orders.
                Defaults to 'artifacts/orders_data.json'.
        """
        self.orders_file = orders_file
        self._all_orders = {}       
        self._current_order = None
        self._load_orders()
        self._order_ids_completed = []
        self._order_types_summary = {}

    def _load_orders(self):
        """Loads orders from the JSON file into memory.

        This private method handles file existence checks, JSON decoding,
        and sets the `_current_order` to the one with the most recent
        timestamp upon loading.
        """
        os.makedirs(os.path.dirname(self.orders_file), exist_ok=True)

        if os.path.exists(self.orders_file) and os.path.getsize(self.orders_file) > 0:
            try:
                with open(self.orders_file, 'r') as f:
                    self._all_orders = json.load(f)
                logger.info(f"Loaded {len(self._all_orders)} orders from '{self.orders_file}'.")

                if self._all_orders:
                    latest_order = None
                    latest_timestamp = None
                    for order_id, order_details in self._all_orders.items():
                        if 'timestamp' in order_details:
                            current_ts = datetime.fromisoformat(order_details['timestamp'])
                            if latest_timestamp is None or current_ts > latest_timestamp:
                                latest_timestamp = current_ts
                                latest_order = order_details
                    self._current_order = latest_order
                    if self._current_order:
                        logger.info(f"Current order set to: {self._current_order.get('order_id')}")
                    else:
                        logger.info("No valid current order found among loaded orders.")
            except json.JSONDecodeError:
                logger.error(f"Error decoding JSON from '{self.orders_file}'. Starting with empty orders.")
                self._all_orders = {}
                self._current_order = None
            except Exception as e:
                logger.error(f"An unexpected error occurred while loading orders: {e}")
                self._all_orders = {}
                self._current_order = None
        else:
            logger.info(f"No existing order file found at '{self.orders_file}'. Starting fresh.")
            self._all_orders = {}
            self._current_order = None

    def _save_orders(self):
        """Saves the current state of all orders to the JSON file.

        This private method ensures the directory exists and pretty-prints
        the JSON for readability.
        """
        try:
            # Ensure the directory exists before saving
            os.makedirs(os.path.dirname(self.orders_file), exist_ok=True)
            with open(self.orders_file, 'w') as f:
                json.dump(self._all_orders, f, indent=4) # indent for pretty printing
            logger.info(f"Saved {len(self._all_orders)} orders to '{self.orders_file}'.")
        except IOError as e:
            logger.error(f"Error saving orders to '{self.orders_file}': {e}")
        except Exception as e:
            logger.error(f"An unexpected error occurred while saving orders: {e}")

    def add_order(self, order_details: Dict):
        """Adds a new order to the tracker and persists it.

        This method sets the new order as the current order, adds it to the
        in-memory dictionary, and triggers a save to the JSON file.

        Args:
            order_details (Dict): A dictionary with order details. Must include
                a unique 'order_id'.
        """
        order_id = order_details.get('order_id')
        if not order_id:
            logger.error("Cannot place order: 'order_id' is missing from order_details.")
            return

        if 'timestamp' not in order_details:
            order_details['timestamp'] = datetime.now().isoformat()

        self._current_order = order_details
        logger.info(f"Order being placed: {self._current_order}")

        if order_id in self._all_orders:
            logger.warning(f"Order with ID '{order_id}' already exists. Updating existing order.")
        self._all_orders[order_id] = self._current_order
        logger.info(f"Order '{order_id}' added/updated in in-memory dictionary.")

        self._save_orders()
        logger.info("Orders saved to disk.")


    @property
    def current_order(self) -> Optional[Dict]:
        """Returns the most recently placed order."""
        return self._current_order

    @property
    def all_orders(self) -> Dict[str, Dict]:
        """Returns a copy of all orders placed so far."""
        return self._all_orders.copy()

    @property
    def completed_order_ids(self) -> List[str]:
        """Returns a list of completed order IDs."""
        return list(self._order_ids_completed)

    @property
    def completed_orders(self) -> List[Dict]:
        """Returns a list of completed order details."""
        return [self._all_orders[oid] for oid in self._order_ids_completed if oid in self._all_orders]

    @property
    def non_completed_order_ids(self) -> List[str]:
        """Returns a list of non-completed order IDs."""
        return [oid for oid in self._all_orders if oid not in self._order_ids_completed]

    @property
    def non_completed_orders(self) -> List[Dict]:
        """Returns a list of non-completed order details."""
        return [self._all_orders[oid] for oid in self._all_orders if oid not in self._order_ids_completed]

    def get_order_by_id(self, order_id: str) -> Optional[Dict]:
        """Retrieves an order by its ID.

        Args:
            order_id (str): The ID of the order to retrieve.

        Returns:
            Optional[Dict]: The order details, or None if not found.
        """
        return self._all_orders.get(order_id)

    def get_total_orders_count(self) -> int:
        """Returns the total number of orders managed."""
        return len(self._all_orders)

    def get_all_orders_as_list(self) -> List[Dict]:
        """Returns all orders as a list of dictionaries."""
        return list(self._all_orders.values())
    
    def complete_order(self, order_id: str) -> bool:
        """Marks an order as completed.

        Args:
            order_id (str): The ID of the order to mark as completed.

        Returns:
            bool: True if the order was successfully marked as completed,
                  False otherwise.
        """
        if order_id in self._all_orders:
            if order_id not in self._order_ids_completed:
                self._order_ids_completed.append(order_id)
                if self._all_orders[order_id]['transaction_type'] not in self._order_types_summary:
                    self._order_types_summary[self._all_orders[order_id]['transaction_type']] = 1
                else:
                    self._order_types_summary[self._all_orders[order_id]['transaction_type']] += 1
                logger.info(f"Order '{order_id}' marked as completed.")
            else:
                logger.info(f"Order '{order_id}' already marked as completed.")
            return True
        else:
            logger.error(f"Order '{order_id}' not found in the order tracker.")
            return False