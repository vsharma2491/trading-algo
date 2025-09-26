import os
from typing import Dict, Any, Optional, List

class BrokerBase:
    """A base class for broker implementations.

    This class provides a common structure for broker-specific classes,
    ensuring that essential methods like authentication are implemented.
    It also offers a utility to list available public methods in subclasses.

    Attributes:
        authenticated (bool): True if the broker is authenticated, otherwise False.
        access_token (Optional[str]): The access token obtained after successful
                                      authentication.
        env (os._Environ): A dictionary-like object representing the system's
                           environment variables.
    """
    def __init__(self):
        """Initializes the BrokerBase instance."""
        self.authenticated = False
        self.access_token = None
        self.env = os.environ

    def authenticate(self) -> Optional[str]:
        """Authenticates with the broker's API.

        This method must be implemented by subclasses to handle the specific
        authentication flow of the broker.

        Returns:
            Optional[str]: The access token if authentication is successful,
                           otherwise None.

        Raises:
            NotImplementedError: If the subclass does not implement this method.
        """
        raise NotImplementedError("Subclasses must implement authenticate()")

    def list_functions(self) -> List[str]:
        """Lists the public methods available in the broker subclass.

        This method inspects the subclass and returns a sorted list of public
        method names, excluding methods from BrokerBase and private methods
        (those prefixed with an underscore).

        Returns:
            List[str]: A sorted list of public method names.
        """
        base_methods = set(dir(BrokerBase))
        all_methods = set(dir(self))
        public_methods = [m for m in all_methods - base_methods if not m.startswith('_')]
        return sorted(public_methods) 