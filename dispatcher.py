from logger import logger
from typing import Union, Dict, Any

class DataDispatcher:
    """A centralized dispatcher for routing market data to a worker queue.

    This class is designed to receive data from various sources and put it
    onto a single, registered queue. This is useful for decoupling data
    producers from consumers in a trading system.

    Attributes:
        _main_queue (Union[multiprocessing.Queue, queue.Queue, None]): The
            queue where all data is dispatched. It is `None` until registered.
    """

    def __init__(self):
        """Initializes the DataDispatcher."""
        self._main_queue = None
        logger.debug("DataDispatcher initialized, awaiting main queue registration.")

    def register_main_queue(self, q):
        """Registers the main queue for data dispatch.

        All data received by the `dispatch` method will be sent to this queue.

        Args:
            q (Union[multiprocessing.Queue, queue.Queue]): The queue to be used
                for dispatching data.
        """
        if self._main_queue is not None:
            logger.warning("Main queue is already registered. Overwriting.")
        self._main_queue = q
        logger.info(f"Main queue registered for DataDispatcher.")

    def dispatch(self, data: Dict[str, Any]):
        """Dispatches a data item to the registered main queue.

        If no queue is registered, an error is logged and the data is discarded.

        Args:
            data (Dict[str, Any]): The data item to be dispatched, typically a
                dictionary representing market data.
        """
        if self._main_queue is None:
            logger.error("Attempted to dispatch data, but no main queue has been registered.")
            return

        try:
            self._main_queue.put(data)
            logger.debug(f"Dispatched data to main queue.")
        except Exception as e:
            logger.error(f"Error dispatching data to main queue: {e}", exc_info=True)

