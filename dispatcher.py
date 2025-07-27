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

