from logger import logger
import queue # Using Python's built-in queue for simplicity in example
              # For multiprocessing, you'd use multiprocessing.Queue

# --- Data Dispatcher Definition (Simplified for Main Queue) ---
class DataDispatcher:
    """
    Routes incoming market data to a single main worker queue.
    """

    def __init__(self):
        """
        Initializes the DataDispatcher.
        It expects a single main queue to be registered.
        """
        self._main_queue = None  # The single queue for all dispatches
        logger.debug(f"DataDispatcher initialized, awaiting main queue registration.")

    def register_main_queue(self, q):
        """
        Registers the single main queue where all data will be dispatched.

        Args:
            q (multiprocessing.Queue or queue.Queue): The main queue object.
        """
        if self._main_queue is not None:
            logger.warning("Main queue is already registered. Overwriting.")
        self._main_queue = q
        logger.info(f"Main queue registered for DataDispatcher.")

    def dispatch(self, data):
        """
        Dispatch a data item to the main queue.

        Args:
            data (dict): The data item (e.g., market data bar) to be dispatched.
        """
        if self._main_queue is None:
            logger.error("Attempted to dispatch data, but no main queue has been registered.")
            return

        try:
            self._main_queue.put(data)
            logger.debug(f"Dispatched data '{data[0].get('symbol', 'N/A')}' to main queue.")
        except Exception as e:
            logger.error(f"Error dispatching data to main queue: {e}")

# --- Example Usage ---
if __name__ == "__main__":
    main_processing_queue = queue.Queue() # Using standard queue for demo

    dispatcher = DataDispatcher()

    dispatcher.register_main_queue(main_processing_queue)

    # 4. Simulate incoming data
    data_item_1 = {"symbol": "AAPL", "price": 170.50, "volume": 1000, "timestamp": "2025-07-23T10:00:00Z"}
    data_item_2 = {"symbol": "GOOG", "price": 150.25, "volume": 500, "timestamp": "2025-07-23T10:01:00Z"}
    data_item_3 = {"symbol": "MSFT", "price": 450.00, "volume": 750, "timestamp": "2025-07-23T10:02:00Z"}

    print("\n--- Dispatching Data ---")
    dispatcher.dispatch(data_item_1)
    dispatcher.dispatch(data_item_2)
    dispatcher.dispatch(data_item_3)
    dispatcher.dispatch({"invalid_data": True}) # Example of data without 'symbol'

    # 5. Simulate the main worker consuming data from the queue
    print("\n--- Main Worker Consuming Data ---")
    while not main_processing_queue.empty():
        received_data = main_processing_queue.get()
        print(f"Worker received: {received_data}")

    # Demonstrate error if no queue is registered
    print("\n--- Dispatcher without registered queue ---")
    another_dispatcher = DataDispatcher()
    another_dispatcher.dispatch({"symbol": "TEST", "price": 10}) # This will log an error