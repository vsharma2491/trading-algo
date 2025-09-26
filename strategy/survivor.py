import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml
from logger import logger

class SurvivorStrategy:
    """Implements the Survivor options trading strategy.

    This strategy systematically sells options based on NIFTY index movements,
    aiming to capture premium decay while managing risk through dynamic gap
    adjustments.

    The strategy operates on both puts (PE) and calls (CE):
    - PE trades are triggered when NIFTY moves up beyond a `pe_gap`.
    - CE trades are triggered when NIFTY moves down beyond a `ce_gap`.

    Key features include gap-based execution with multipliers, dynamic strike
    selection, and a reset mechanism to keep reference points aligned with
    the market.

    Note:
        This strategy is designed for the Zerodha broker and may require
        modifications for other brokers like Fyers.

    Attributes:
        broker: An instance of a broker class for market data and execution.
        order_manager: An instance of OrderTracker for managing orders.
        instruments (pd.DataFrame): A DataFrame of available instruments.
        nifty_pe_last_value (float): The reference price for PE trades.
        nifty_ce_last_value (float): The reference price for CE trades.
    """
    
    def __init__(self, broker, config: dict, order_manager):
        """Initializes the SurvivorStrategy.

        Args:
            broker: An instance of a broker class (e.g., ZerodhaBroker).
            config (dict): A dictionary containing strategy parameters.
            order_manager: An instance of the OrderTracker class.
        """
        for k, v in config.items():
            setattr(self, f'strat_var_{k}', v)
        # External dependencies
        self.broker = broker
        self.symbol_initials = self.strat_var_symbol_initials
        self.order_manager = order_manager  # Store OrderTracker
        self.broker.download_instruments()
        self.instruments = self.broker.instruments_df[self.broker.instruments_df['tradingsymbol'].str.startswith(self.symbol_initials)]   # For Zerodha
        if self.instruments.shape[0] == 0:
            logger.error(f"No instruments found for {self.symbol_initials}")
            logger.error(f"Instument {self.symbol_initials} not found. Please check the symbol initials")
            return
        
        self.strike_difference = None      
        self._initialize_state()
        
        # Calculate and store strike difference for the option series
        self.strike_difference = self._get_strike_difference(self.symbol_initials)
        logger.info(f"Strike difference for {self.symbol_initials} is {self.strike_difference}")

    def _nifty_quote(self) -> dict:
        """Retrieves the current quote for the NIFTY 50 index.

        Returns:
            dict: The quote data for NIFTY 50.
        """
        symbol_code = "NSE:NIFTY 50"
        return self.broker.get_quote(symbol_code)

    def _initialize_state(self):
        """Initializes the strategy's state variables.

        This method sets the initial reference prices for PE and CE trades
        (either from the config or the current market price) and resets
        the trade flags.
        """
        self.pe_reset_gap_flag = 0
        self.ce_reset_gap_flag = 0
        
        current_quote = self._nifty_quote()
        print(current_quote)
        
        if self.strat_var_pe_start_point == 0:
            self.nifty_pe_last_value = current_quote[self.strat_var_index_symbol]['last_price']
            logger.debug(f"Nifty PE Start Point is 0, so using LTP: {self.nifty_pe_last_value}")
        else:
            self.nifty_pe_last_value = self.strat_var_pe_start_point

        if self.strat_var_ce_start_point == 0:
            self.nifty_ce_last_value = current_quote[self.strat_var_index_symbol]['last_price']
            logger.debug(f"Nifty CE Start Point is 0, so using LTP: {self.nifty_ce_last_value}")
        else:
            self.nifty_ce_last_value = self.strat_var_ce_start_point
            
        logger.info(f"Nifty PE Start Value during initialization: {self.nifty_pe_last_value}, "
                   f"Nifty CE Start Value during initialization: {self.nifty_ce_last_value}")

    def _get_strike_difference(self, symbol_initials: str) -> int:
        """Calculates the difference between consecutive strike prices.

        Args:
            symbol_initials (str): The prefix for the option series
                (e.g., "NIFTY25JAN30").

        Returns:
            int: The difference between strikes (e.g., 50 or 100).
        """
        if self.strike_difference is not None:
            return self.strike_difference
            
        ce_instruments = self.instruments[
            self.instruments['tradingsymbol'].str.startswith(symbol_initials) & 
            self.instruments['tradingsymbol'].str.endswith('CE')
        ]
        
        if ce_instruments.shape[0] < 2:
            logger.error(f"Not enough CE instruments found for {symbol_initials} to calculate strike difference")
            return 0

        ce_instruments_sorted = ce_instruments.sort_values('strike')
        top2 = ce_instruments_sorted.head(2)
        self.strike_difference = abs(top2.iloc[1]['strike'] - top2.iloc[0]['strike'])
        return self.strike_difference

    def on_ticks_update(self, ticks: dict):
        """The main entry point for the strategy on each market data tick.

        This method is called by the trading loop whenever new market data is
        received. It extracts the current price and triggers the evaluation
        of PE and CE trading opportunities, as well as the reset logic.

        Args:
            ticks (dict): A dictionary containing market data, including the
                'last_price' of the underlying index.
        """
        current_price = ticks['last_price']
        
        self._handle_pe_trade(current_price)
        self._handle_ce_trade(current_price)
        self._reset_reference_values(current_price)

    def _check_sell_multiplier_breach(self, sell_multiplier: int) -> bool:
        """Checks if the position scaling multiplier exceeds a risk threshold.

        This method serves as a risk management check to prevent oversized
        positions during large, sudden price movements.

        Args:
            sell_multiplier (int): The calculated multiplier for position sizing.

        Returns:
            bool: True if the multiplier is over the threshold, False otherwise.
        """
        if sell_multiplier > self.strat_var_sell_multiplier_threshold:
            logger.warning(f"Sell multiplier {sell_multiplier} breached the threshold {self.strat_var_sell_multiplier_threshold}")
            return True
        return False

    def _handle_pe_trade(self, current_price: float):
        """Evaluates and executes PE (Put) option trades.

        This method is triggered when the NIFTY index moves up. It sells PE
        options to profit from the upward movement.

        The process involves:
        1.  Checking if the upward price movement exceeds `pe_gap`.
        2.  Calculating a `sell_multiplier` based on the gap magnitude.
        3.  Validating the multiplier against risk limits.
        4.  Finding a suitable PE strike with an adequate premium.
        5.  Executing the trade and updating the `nifty_pe_last_value`.

        Args:
            current_price (float): The current price of the NIFTY index.
        """
        if current_price <= self.nifty_pe_last_value:
            self._log_stable_market(current_price)
            return

        price_diff = round(current_price - self.nifty_pe_last_value, 0)
        if price_diff > self.strat_var_pe_gap:
            sell_multiplier = int(price_diff / self.strat_var_pe_gap)
            
            if self._check_sell_multiplier_breach(sell_multiplier):
                logger.warning(f"Sell multiplier {sell_multiplier} breached the threshold {self.strat_var_sell_multiplier_threshold}")
                return

            self.nifty_pe_last_value += self.strat_var_pe_gap * sell_multiplier
            total_quantity = sell_multiplier * self.strat_var_pe_quantity

            temp_gap = self.strat_var_pe_symbol_gap
            while True:
                instrument = self._find_nifty_symbol_from_gap("PE", current_price, gap=temp_gap)
                if not instrument:
                    logger.warning("No suitable instrument found for PE with gap %s", temp_gap)
                    return 
                
                symbol_code = self.strat_var_exchange + ":" + instrument['tradingsymbol']
                quote = self.broker.get_quote(symbol_code)[symbol_code]
                
                if quote['last_price'] < self.strat_var_min_price_to_sell:
                    logger.info(f"Last price {quote['last_price']} is less than min price to sell {self.strat_var_min_price_to_sell}")
                    temp_gap -= self.strat_var_nifty_lot_size
                    continue
                    
                logger.info(f"Execute PE sell @ {instrument['tradingsymbol']} × {total_quantity}, Market Price")
                self._place_order(instrument['tradingsymbol'], total_quantity)
                
                self.pe_reset_gap_flag = 1
                break

    def _handle_ce_trade(self, current_price: float):
        """Evaluates and executes CE (Call) option trades.

        This method is triggered when the NIFTY index moves down. It sells CE
        options to profit from the downward movement.

        The process involves:
        1.  Checking if the downward price movement exceeds `ce_gap`.
        2.  Calculating a `sell_multiplier` based on the gap magnitude.
        3.  Validating the multiplier against risk limits.
        4.  Finding a suitable CE strike with an adequate premium.
        5.  Executing the trade and updating the `nifty_ce_last_value`.

        Args:
            current_price (float): The current price of the NIFTY index.
        """
        # No action needed if price hasn't moved down sufficiently
        if current_price >= self.nifty_ce_last_value:
            self._log_stable_market(current_price)
            return

        # Calculate price difference and check if it exceeds gap threshold
        price_diff = round(self.nifty_ce_last_value - current_price, 0)  
        if price_diff > self.strat_var_ce_gap:
            # Calculate multiplier for position sizing
            sell_multiplier = int(price_diff / self.strat_var_ce_gap)
            
            # Risk check: Ensure multiplier doesn't exceed threshold
            if self._check_sell_multiplier_breach(sell_multiplier):
                logger.warning(f"Sell multiplier {sell_multiplier} breached the threshold {self.strat_var_sell_multiplier_threshold}")
                return

            # Update reference value based on executed gaps
            self.nifty_ce_last_value -= self.strat_var_ce_gap * sell_multiplier
            
            # Calculate total quantity to trade
            total_quantity = sell_multiplier * self.strat_var_ce_quantity

            # Find suitable CE option with adequate premium
            temp_gap = self.strat_var_ce_symbol_gap 
            while True:
                # Find CE instrument at specified gap from current price
                instrument = self._find_nifty_symbol_from_gap("CE", current_price, gap=temp_gap)
                if not instrument:
                    logger.warning("No suitable instrument found for CE with gap %s", temp_gap)
                    return
                    
                # Get current quote for the selected instrument
                symbol_code = self.strat_var_exchange + ":" + instrument['tradingsymbol']
                quote = self.broker.get_quote(symbol_code)[symbol_code]
                print("=======", quote)
                
                # Check if premium meets minimum threshold
                if quote['last_price'] < self.strat_var_min_price_to_sell:
                    logger.info(f"Last price {quote['last_price']} is less than min price to sell {self.strat_var_min_price_to_sell}, trying next strike")
                    # Try closer strike if premium is too low
                    temp_gap -= self.strat_var_nifty_lot_size
                    continue
                    
                # Execute the trade
                logger.info(f"Execute CE sell @ {instrument['tradingsymbol']} × {total_quantity}, Market Price")
                self._place_order(instrument['tradingsymbol'], total_quantity)
                
                # Set reset flag to enable reset logic
                self.ce_reset_gap_flag = 1
                break

    def _reset_reference_values(self, current_price: float):
        """Resets reference prices when the market moves favorably.

        This mechanism prevents reference points from drifting too far from the
        current market price, ensuring the strategy remains responsive.

        -   The PE reference is reset if the price drops significantly after a
            PE trade has occurred.
        -   The CE reference is reset if the price rises significantly after a
            CE trade has occurred.

        Args:
            current_price (float): The current price of the NIFTY index.
        """
        if (self.nifty_pe_last_value - current_price) > self.strat_var_pe_reset_gap and self.pe_reset_gap_flag:
            logger.info(f"Resetting PE value from {self.nifty_pe_last_value} to {current_price + self.strat_var_pe_reset_gap}")
            self.nifty_pe_last_value = current_price + self.strat_var_pe_reset_gap

        if (current_price - self.nifty_ce_last_value) > self.strat_var_ce_reset_gap and self.ce_reset_gap_flag:
            logger.info(f"Resetting CE value from {self.nifty_ce_last_value} to {current_price - self.strat_var_ce_reset_gap}")
            self.nifty_ce_last_value = current_price - self.strat_var_ce_reset_gap

    def _find_nifty_symbol_from_gap(self, option_type: str, ltp: float, gap: int) -> dict:
        """Finds the best option instrument based on a strike distance from the LTP.

        This method selects an option by:
        1.  Calculating a target strike price (LTP +/- gap).
        2.  Filtering available instruments for the correct type (PE/CE) and series.
        3.  Finding the instrument with the strike closest to the target.

        Args:
            option_type (str): The type of option to find ('PE' or 'CE').
            ltp (float): The last traded price of the underlying index.
            gap (int): The desired distance from the LTP to the target strike.

        Returns:
            dict: The instrument details of the best match, or None if not found.
        """
        if option_type == "PE":
            symbol_gap = -gap
        else:
            symbol_gap = gap
            
        target_strike = ltp + symbol_gap
        
        df = self.instruments[
            (self.instruments['tradingsymbol'].str.startswith(self.strat_var_symbol_initials)) &
            (self.instruments['instrument_type'] == option_type) &
            (self.instruments['segment'] == "NFO-OPT")
        ]
        
        if df.empty:
            return None
            
        df['target_strike_diff'] = (df['strike'] - target_strike).abs()
        
        tolerance = self._get_strike_difference(self.strat_var_symbol_initials) / 2
        df = df[df['target_strike_diff'] <= tolerance]
        
        if df.empty:
            logger.error(f"No instrument found for {self.strat_var_symbol_initials} {option_type} "
                        f"within {tolerance} of {target_strike}")
            return None
            
        best = df.sort_values('target_strike_diff').iloc[0]
        return best.to_dict()

    def _find_price_eligible_symbol(self, option_type: str) -> dict:
        """Finds an option symbol that meets the minimum premium requirement.

        This method iteratively searches for an option that has a premium
        above the configured `min_price_to_sell`.

        Note:
            This method appears to have issues and may not be actively used.
            The core trading logic in `_handle_pe_trade` and `_handle_ce_trade`
            contains a more direct implementation of this functionality.

        Args:
            option_type (str): The type of option to find ('PE' or 'CE').

        Returns:
            dict: The instrument details if a suitable option is found, otherwise None.
        """
        temp_gap = self.strat_var_pe_symbol_gap if option_type == "PE" else self.strat_var_ce_symbol_gap
        
        while True:
            ltp = self._nifty_quote()['last_price']
            
            instrument = self._find_nifty_symbol_from_gap(
                self.instruments, self.strat_var_symbol_initials, temp_gap, option_type, ltp, self.strat_var_nifty_lot_size
            )
            
            if instrument is None:
                return None
                
            symbol_code = f"{self.strat_var_exchange}:{instrument['tradingsymbol']}"
            price = float(self.kite.quote(symbol_code)[symbol_code]['last_price'])
            
            if price < self.strat_var_min_price_to_sell:
                temp_gap -= self.strat_var_nifty_lot_size
            else:
                return instrument

    def _place_order(self, symbol: str, quantity: int):
        """Places a market order through the broker.

        This method executes the order and adds it to the `OrderTracker` for
        management.

        Args:
            symbol (str): The trading symbol of the option.
            quantity (int): The number of shares/lots to trade.
        """
        order_id = self.broker.place_order(
            symbol, 
            quantity, 
            price=None,
            transaction_type=self.strat_var_trans_type, 
            order_type=self.strat_var_order_type, 
            variety="REGULAR", 
            exchange=self.strat_var_exchange, 
            product=self.strat_var_product_type, 
            tag="Survivor"
        )
        
        if order_id == -1:
            logger.error(f"Order placement failed for {symbol} × {quantity}, Market Price")
            return
            
        logger.info(f"Placing order for {symbol} × {quantity}, Market Price")
        
        from datetime import datetime
        order_details = {
            "order_id": order_id,
            "symbol": symbol,
            "transaction_type": self.strat_var_trans_type,
            "quantity": quantity,
            "price": None,
            "timestamp": datetime.now().isoformat(),
        }
        
        self.order_manager.add_order(order_details)
        

    def _log_stable_market(self, current_val: float):
        """Logs the market state when no trading action is taken.

        Args:
            current_val (float): The current price of the NIFTY index.
        """
        logger.info(
            f"{self.strat_var_symbol_initials} Nifty under control. "
            f"PE = {self.nifty_pe_last_value}, "
            f"CE = {self.nifty_ce_last_value}, "
            f"Current = {current_val}, "
            f"CE Gap = {self.strat_var_ce_gap}, "
            f"PE Gap = {self.strat_var_pe_gap}"
        )


# Below Logic is for
# 1. command line arguments and 
# 2. run the strategy in a loop

# =============================================================================
# MAIN SCRIPT EXECUTION
# =============================================================================
# 
# This section provides a complete command-line interface for running the
# Survivor Strategy with flexible configuration options.
#
# FEATURES:
# =========
# 1. **Configuration Management**: 
#    - Loads defaults from YAML file
#    - Supports command-line overrides
#    - Validates all parameters
#
# 2. **Argument Parsing**:
#    - Comprehensive help and examples
#    - Type validation and choices
#    - Hierarchical configuration (CLI > YAML > defaults)
#
# 3. **Trading Loop**:
#    - Real-time websocket data processing
#    - Strategy execution on each tick
#    - Error handling and recovery
#    - Order tracking and management
#
# USAGE EXAMPLES:
# ==============
# 
# # Basic usage with defaults
# python system/main.py
# 
# # Override specific parameters
# python system/main.py --symbol-initials NIFTY25807 --pe-gap 25 --ce-gap 25
# 
# # Full customization
# python system/main.py \
#     --symbol-initials NIFTY25807 \
#     --pe-symbol-gap 250 --ce-symbol-gap 250 \
#     --pe-gap 25 --ce-gap 25 \
#     --pe-quantity 75 --ce-quantity 75
#
# =============================================================================

if __name__ == "__main__":
    import time
    import yaml
    import sys
    import argparse
    from dispatcher import DataDispatcher
    from orders import OrderTracker
    from strategy.survivor import SurvivorStrategy
    from brokers.zerodha import ZerodhaBroker
    from logger import logger
    from queue import Queue
    import random
    import traceback
    import warnings
    warnings.filterwarnings("ignore")

    import logging
    logger.setLevel(logging.INFO)
    
    # ==========================================================================
    # SECTION 1: CONFIGURATION LOADING AND PARSING
    # ==========================================================================
    
    # Load default configuration from YAML file
    config_file = os.path.join(os.path.dirname(__file__), "configs/survivor.yml")
    with open(config_file, 'r') as f:
        config = yaml.safe_load(f)['default']

    def create_argument_parser() -> argparse.ArgumentParser:
        """Creates and configures the argument parser for the strategy.

        This function sets up all the command-line arguments, including their
        types, help messages, and default values.

        Returns:
            argparse.ArgumentParser: The configured argument parser.
        """
        parser = argparse.ArgumentParser(
            description="Survivor",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""
    Examples:
    # Use default configuration from survivor.yml
    python system/main.py
    
    # Override specific parameters
    python system/main.py --symbol-initials NIFTY25807 --pe-gap 25 --ce-gap 25
    
    # Full parameter override
    python system/main.py \\
        --symbol-initials NIFTY25807 \\
        --pe-symbol-gap 200 --ce-symbol-gap 200 \\
        --exchange NFO \\
        --pe-gap 20 --ce-gap 20 \\
        --pe-reset-gap 30 --ce-reset-gap 30 \\
        --pe-quantity 50 --ce-quantity 50 \\
        --pe-start-point 0 --ce-start-point 0 \\
        --order-type MARKET --product-type NRML \\
        --min-price-to-sell 15 --trans-type SELL

CONFIGURATION HIERARCHY:
=======================
1. Command line arguments (highest priority)
2. survivor.yml default values (fallback)

PARAMETER GROUPS:
================
• Core Parameters: symbol-initials, index-symbol
• Gap Parameters: pe-gap, ce-gap, pe-reset-gap, ce-reset-gap  
• Strike Selection: pe-symbol-gap, ce-symbol-gap
• Order Management: order-type, product-type, exchange
• Risk Management: min-price-to-sell, sell-multiplier-threshold
• Position Sizing: pe-quantity, ce-quantity
            """
        )
        
        # =======================================================================
        # CORE TRADING PARAMETERS
        # =======================================================================
        
        parser.add_argument('--symbol-initials', type=str,
                        help='Option series identifier (e.g., NIFTY25JAN30). '
                             'Must be at least 9 characters. This identifies the specific '
                             'option expiry series to trade.')
        
        parser.add_argument('--index-symbol', type=str,
                        help='Underlying index symbol for price tracking (e.g., NSE:NIFTY 50). '
                             'This is the reference index whose price movements trigger trades.')
        
        # =======================================================================
        # STRIKE SELECTION PARAMETERS  
        # =======================================================================
        
        parser.add_argument('--pe-symbol-gap', type=int,
                        help='Distance below current price for PE strike selection. '
                             'E.g., if NIFTY is at 24500 and pe-symbol-gap is 200, '
                             'PE strikes around 24300 will be selected.')
        
        parser.add_argument('--ce-symbol-gap', type=int,
                        help='Distance above current price for CE strike selection. '
                             'E.g., if NIFTY is at 24500 and ce-symbol-gap is 200, '
                             'CE strikes around 24700 will be selected.')
        
        # =======================================================================
        # EXCHANGE AND ORDER SETTINGS
        # =======================================================================
        
        parser.add_argument('--exchange', type=str, choices=['NFO'],
                        help='Exchange for trading (NFO for F&O, NSE for equity)')
        parser.add_argument('--order-type', type=str, choices=['MARKET', 'LIMIT'],
                        help='Order type for placing trades')
        parser.add_argument('--product-type', type=str, choices=['NRML'],
                        help='Product type for orders')
        
        # =======================================================================
        # GAP PARAMETERS FOR TRADE TRIGGERING
        # =======================================================================
        
        parser.add_argument('--pe-gap', type=float,
                        help='NIFTY upward movement threshold to trigger PE sells. '
                             'E.g., if pe-gap is 25 and NIFTY moves up 30 points, '
                             'PE options will be sold (multiplier = 30/25 = 1).')
        
        parser.add_argument('--ce-gap', type=float,
                        help='NIFTY downward movement threshold to trigger CE sells. '
                             'E.g., if ce-gap is 25 and NIFTY moves down 30 points, '
                             'CE options will be sold (multiplier = 30/25 = 1).')
        
        # =======================================================================
        # RESET GAP PARAMETERS
        # =======================================================================
        
        parser.add_argument('--pe-reset-gap', type=float,
                        help='Favorable movement threshold to reset PE reference value. '
                             'When NIFTY moves down by this amount after PE trades, '
                             'the PE reference is reset closer to current price.')
        
        parser.add_argument('--ce-reset-gap', type=float,
                        help='Favorable movement threshold to reset CE reference value. '
                             'When NIFTY moves up by this amount after CE trades, '
                             'the CE reference is reset closer to current price.')
        
        # =======================================================================
        # QUANTITY PARAMETERS
        # =======================================================================
        
        parser.add_argument('--pe-quantity', type=int,
                        help='Base quantity for PE option trades. Total quantity = '
                             'pe-quantity × sell-multiplier. E.g., if pe-quantity=50 '
                             'and multiplier=2, total PE quantity = 100.')
        
        parser.add_argument('--ce-quantity', type=int,
                        help='Base quantity for CE option trades. Total quantity = '
                             'ce-quantity × sell-multiplier. E.g., if ce-quantity=50 '
                             'and multiplier=2, total CE quantity = 100.')
        
        # =======================================================================
        # STARTING REFERENCE POINTS
        # =======================================================================
        
        parser.add_argument('--pe-start-point', type=int,
                        help='Initial PE reference value. If 0, uses current market price. '
                             'If specified, uses that value as starting reference. '
                             'E.g., --pe-start-point 24500 starts PE tracking from 24500.')
        
        parser.add_argument('--ce-start-point', type=int,
                        help='Initial CE reference value. If 0, uses current market price. '
                             'If specified, uses that value as starting reference. '
                             'E.g., --ce-start-point 24500 starts CE tracking from 24500.')
        
        # =======================================================================
        # RISK MANAGEMENT PARAMETERS
        # =======================================================================
        
        parser.add_argument('--trans-type', type=str, choices=['BUY', 'SELL'],
                        help='Transaction type for all orders. Typically SELL for '
                             'premium collection strategies like this one.')
        
        parser.add_argument('--min-price-to-sell', type=float,
                        help='Minimum option premium threshold for execution. Options '
                             'with premium below this value will be skipped. Prevents '
                             'trading illiquid or very cheap options.')
        
        parser.add_argument('--sell-multiplier-threshold', type=float,
                        help='Maximum allowed position multiplier. Prevents excessive '
                             'position sizes during large market moves. E.g., if threshold '
                             'is 3 and calculated multiplier is 4, trade will be blocked.')
        
        # =======================================================================
        # UTILITY OPTIONS
        # =======================================================================
        
        parser.add_argument('--show-config', action='store_true',
                        help='Display current configuration (after applying overrides) and exit. '
                             'Useful for verifying parameter values before trading.')
        
        parser.add_argument('--config-file', type=str, default=config_file,
                        help='Path to YAML configuration file containing default values. '
                             'Defaults to system/strategy/configs/survivor.yml')
        
        return parser

    def show_config(config: dict):
        """Displays the current strategy configuration in a formatted table.

        Args:
            config (dict): The configuration dictionary to display.
        """
        print("\n" + "="*80)
        print("SURVIVOR STRATEGY CONFIGURATION")
        print("="*80)
        
        # Group parameters by functionality for better readability
        sections = {
            "Index & Symbol Configuration": [
                'index_symbol', 'symbol_initials'
            ],
            "Exchange & Order Management": [
                'exchange', 'order_type', 'product_type', 'trans_type'
            ],
            "Gap Parameters (Trade Triggers)": [
                'pe_gap', 'ce_gap', 'pe_reset_gap', 'ce_reset_gap'
            ],
            "Strike Selection (Distance from Spot)": [
                'pe_symbol_gap', 'ce_symbol_gap'
            ],
            "Position Sizing": [
                'pe_quantity', 'ce_quantity'
            ],
            "Reference Points (Starting Values)": [
                'pe_start_point', 'ce_start_point'
            ],
            "Risk Management": [
                'min_price_to_sell', 'sell_multiplier_threshold'
            ]
        }
        
        for section, fields in sections.items():
            print(f"\n{section}:")
            print("-" * len(section))
            for field in fields:
                value = config.get(field, 'NOT SET')
                # Add units/context for clarity
                unit_context = {
                    'pe_gap': 'points',
                    'ce_gap': 'points', 
                    'pe_reset_gap': 'points',
                    'ce_reset_gap': 'points',
                    'pe_symbol_gap': 'points from spot',
                    'ce_symbol_gap': 'points from spot',
                    'pe_quantity': 'units',
                    'ce_quantity': 'units',
                    'min_price_to_sell': 'rupees'
                }
                unit = unit_context.get(field, '')
                print(f"  {field:25}: {value} {unit}".strip())
        
        print("\n" + "="*80)
        print("TRADING LOGIC SUMMARY:")
        print("="*80)
        print(f"• PE Sells triggered when NIFTY rises >{config.get('pe_gap', 'N/A')} points")
        print(f"• CE Sells triggered when NIFTY falls >{config.get('ce_gap', 'N/A')} points") 
        print(f"• PE strikes selected ~{config.get('pe_symbol_gap', 'N/A')} points below spot")
        print(f"• CE strikes selected ~{config.get('ce_symbol_gap', 'N/A')} points above spot")
        print(f"• Minimum option premium: ₹{config.get('min_price_to_sell', 'N/A')}")
        print(f"• Maximum position multiplier: {config.get('sell_multiplier_threshold', 'N/A')}x")
        print("="*80)

    # ==========================================================================
    # SECTION 2: ARGUMENT PARSING AND CONFIGURATION MERGING
    # ==========================================================================
    
    # Parse command line arguments
    parser = create_argument_parser()
    args = parser.parse_args()

    # Define mapping between argument names and configuration keys
    # This allows clean separation between CLI argument naming conventions
    # and internal configuration parameter names
    arg_to_config_mapping = {
        'symbol_initials': 'symbol_initials',
        'index_symbol': 'index_symbol',
        'pe_symbol_gap': 'pe_symbol_gap',
        'ce_symbol_gap': 'ce_symbol_gap',
        'exchange': 'exchange',
        'order_type': 'order_type',
        'product_type': 'product_type',
        'pe_gap': 'pe_gap',
        'ce_gap': 'ce_gap',
        'pe_reset_gap': 'pe_reset_gap',
        'ce_reset_gap': 'ce_reset_gap',
        'pe_quantity': 'pe_quantity',
        'ce_quantity': 'ce_quantity',
        'pe_start_point': 'pe_start_point',
        'ce_start_point': 'ce_start_point',
        'trans_type': 'trans_type',
        'min_price_to_sell': 'min_price_to_sell',
        'sell_multiplier_threshold': 'sell_multiplier_threshold'
    }

    # Apply command line overrides to configuration
    # Priority: Command line args > YAML config > defaults
    overridden_params = []
    for arg_name, config_key in arg_to_config_mapping.items():
        # Convert dashes to underscores for argument attribute access
        arg_value = getattr(args, arg_name.replace('-', '_'))
        if arg_value is not None:
            config[config_key] = arg_value
            overridden_params.append(f"{config_key}={arg_value}")

    # Handle utility options
    if args.show_config:
        show_config(config)
        sys.exit(0)

    # ==========================================================================
    # SECTION 3: CONFIGURATION VALIDATION AND LOGGING
    # ==========================================================================
    
    # Validate that user has updated default configuration values
    def validate_configuration(config: dict) -> bool:
        """Validates the strategy configuration.

        This function checks if the user has updated the default parameters.
        - If all parameters are at their defaults, it fails validation.
        - If some are at defaults, it issues a warning and asks for user
          confirmation to proceed.

        Args:
            config (dict): The configuration dictionary to validate.

        Returns:
            bool: True if the configuration is valid or confirmed by the user,
                  False otherwise.
        """
        # Define default values that indicate user hasn't updated config
        default_values = {
            'symbol_initials': 'NIFTY25807',  
            'pe_gap': 20,
            'ce_gap': 20,
            'pe_quantity': 75,
            'ce_quantity': 75,
            'pe_symbol_gap': 200,
            'ce_symbol_gap': 200,
            'min_price_to_sell': 15,
            'pe_reset_gap': 30,
            'ce_reset_gap': 30,
            'pe_start_point': 0,
            'ce_start_point': 0,
            'sell_multiplier_threshold': 5
        }
        
        # Check which values are still at defaults
        unchanged_values = []
        changed_values = []
        for key, default_value in default_values.items():
            if config.get(key) == default_value:
                unchanged_values.append(key)
            else:
                changed_values.append(key)
        
        # If ALL values are still at defaults, show error and exit
        if len(changed_values) == 0:
            print("\n" + "="*80)
            print("❌ CONFIGURATION VALIDATION FAILED")
            print("="*80)
            print("ALL configuration values are still at their defaults!")
            print("You must update at least some parameters before running the strategy.")
            print()
            print("CRITICAL PARAMETERS TO UPDATE:")
            print("• symbol_initials: Must match current option series (e.g., NIFTY25JAN30)")
            print("• pe_gap/ce_gap: Price movement thresholds for your strategy")
            print("• pe_quantity/ce_quantity: Position sizes based on your capital")
            print("• min_price_to_sell: Minimum option premium threshold")
            print()
            print("Example command line usage:")
            print("python survivor.py \\")
            print("    --symbol-initials NIFTY25JAN30 \\")
            print("    --pe-gap 25 --ce-gap 25 \\")
            print("    --pe-quantity 50 --ce-quantity 50 \\")
            print("    --min-price-to-sell 20")
            print("="*80)
            return False
        
        # If SOME values are still at defaults, show warning and ask for confirmation
        if len(unchanged_values) > 0:
            print("\n" + "="*80)
            print("⚠️  CONFIGURATION WARNING")
            print("="*80)
            print("Some configuration values are still at their defaults:")
            print()
            
            for value in unchanged_values:
                print(f"  ⚠️  {value}: {config.get(value)} (default)")
            
            if len(changed_values) > 0:
                print("\nUpdated values:")
                for value in changed_values:
                    print(f"  ✅ {value}: {config.get(value)} (updated)")
            
            print("\n" + "="*80)
            print("⚠️  WARNING: Running with default values may result in:")
            print("   • Trading wrong option series")
            print("   • Incorrect position sizes")
            print("   • Poor risk management")
            print("   • Potential losses")
            print("="*80)
            
            # Ask for user confirmation
            while True:
                response = input("\nDo you want to proceed with this configuration? (yes/no): ").lower().strip()
                if response in ['yes', 'y']:
                    print("\n✅ Proceeding with current configuration...")
                    return True
                elif response in ['no', 'n']:
                    print("\n❌ Strategy execution cancelled by user.")
                    print("Please update your configuration and try again.")
                    return False
                else:
                    print("Please enter 'yes' or 'no'.")
        
        # If all values have been updated, proceed without confirmation
        print("\n" + "="*80)
        print("✅ CONFIGURATION VALIDATION PASSED")
        print("="*80)
        print("All critical parameters have been updated from defaults.")
        print("Proceeding with strategy execution...")
        print("="*80)
        return True
    
    # Run configuration validation
    if not validate_configuration(config):
        logger.error("Configuration validation failed. Please update your configuration.")
        sys.exit(1)

    # Log configuration source and overrides
    if overridden_params:
        logger.info(f"Configuration loaded from {config_file} with command line overrides:")
        for param in overridden_params:
            logger.info(f"  Override: {param}")
    else:
        logger.info(f"Using default configuration from {config_file}")

    # Log key trading parameters for verification
    logger.info(f"Trading Configuration:")
    logger.info(f"  Symbol: {config['symbol_initials']}, Exchange: {config['exchange']}")
    logger.info(f"  Gap Triggers - PE: {config['pe_gap']}, CE: {config['ce_gap']}")
    logger.info(f"  Strike Selection - PE: -{config['pe_symbol_gap']}, CE: +{config['ce_symbol_gap']}")
    logger.info(f"  Base Quantities - PE: {config['pe_quantity']}, CE: {config['ce_quantity']}")
    logger.info(f"  Risk Limits - Min Premium: ₹{config['min_price_to_sell']}, Max Multiplier: {config['sell_multiplier_threshold']}x")

    # ==========================================================================
    # SECTION 4: TRADING INFRASTRUCTURE SETUP
    # ==========================================================================
    
    
    # Create broker interface for market data and order execution
    if os.getenv("BROKER_TOTP_ENABLE") == "true":
        logger.info("Using TOTP login flow")
        broker = ZerodhaBroker(without_totp=False)
    else:
        logger.info("Using normal login flow")
        broker = ZerodhaBroker(without_totp=True)
    
    # Create order tracking system for position management
    order_tracker = OrderTracker() 
    
    # Get instrument token for the underlying index
    # This token is used for websocket subscription to receive real-time price updates
    try:
        quote_data = broker.get_quote(config['index_symbol'])
        instrument_token = quote_data[config['index_symbol']]['instrument_token']
        logger.info(f"✓ Index instrument token obtained: {instrument_token}")
    except Exception as e:
        logger.error(f"Failed to get instrument token for {config['index_symbol']}: {e}")
        sys.exit(1)

    # Initialize data dispatcher for handling real-time market data
    # The dispatcher manages queues and routes market data to strategy
    dispatcher = DataDispatcher()
    dispatcher.register_main_queue(Queue())

    # ==========================================================================
    # SECTION 5: WEBSOCKET CALLBACK CONFIGURATION  
    # ==========================================================================
    
    # Define websocket event handlers for real-time data processing
    
    def on_ticks(ws, ticks: list):
        """Callback function to handle incoming ticks from the WebSocket.

        Args:
            ws: The WebSocket instance.
            ticks (list): A list of tick data dictionaries.
        """
        logger.debug("Received ticks: {}".format(ticks))
        dispatcher.dispatch(ticks)

    def on_connect(ws, response: dict):
        """Callback function for when the WebSocket connection is established.

        Args:
            ws: The WebSocket instance.
            response (dict): The connection response from the server.
        """
        logger.info("Websocket connected successfully: {}".format(response))
        
        ws.subscribe([instrument_token])
        logger.info(f"✓ Subscribed to instrument token: {instrument_token}")
        
        ws.set_mode(ws.MODE_FULL, [instrument_token])

    def on_order_update(ws, data: dict):
        """Callback function for handling order update messages.

        Args:
            ws: The WebSocket instance.
            data (dict): The order update data.
        """
        logger.info("Order update received: {}".format(data))
        

    # Assign callbacks to broker's websocket instance
    broker.on_ticks = on_ticks
    broker.on_connect = on_connect
    broker.on_order_update = on_order_update

    # ==========================================================================
    # SECTION 6: STRATEGY INITIALIZATION AND WEBSOCKET START
    # ==========================================================================
    
    # Start websocket connection for real-time data
    broker.connect_websocket()

    # Initialize the trading strategy with all dependencies
    strategy = SurvivorStrategy(broker, config, order_tracker)

    # ==========================================================================
    # SECTION 7: MAIN TRADING LOOP
    # ==========================================================================
    
    try:
        while True:
            try:
                # STEP 1: Get market data from dispatcher queue
                # This call blocks until new tick data arrives from websocket
                tick_data = dispatcher._main_queue.get()
                
                # STEP 2: Extract the primary instrument data
                # tick_data is a list, we process the first instrument
                symbol_data = tick_data[0]
                
                # STEP 3: Optional data simulation for testing
                # You also need to move `tick_data = dispatcher._main_queue.get()` above 
                # outside of the while loop for this to work
                # if isinstance(symbol_data, dict) and 'last_price' in symbol_data:
                #     original_price = symbol_data['last_price']
                #     variation = random.uniform(-50, 50)  # ±50 point random variation
                #     symbol_data['last_price'] += variation
                #     logger.debug(f"Testing mode - Original: {original_price}, "
                #                 f"Modified: {symbol_data['last_price']} (Δ{variation:+.1f})")
                
                # STEP 4: Process tick through strategy
                # This triggers the main strategy logic for PE/CE evaluation
                strategy.on_ticks_update(symbol_data)
                
            except KeyboardInterrupt:
                # Handle graceful shutdown on Ctrl+C
                logger.info("SHUTDOWN REQUESTED - Stopping strategy...")
                break
                
            except Exception as tick_error:
                # Handle individual tick processing errors
                logger.error(f"Error processing tick data: {tick_error}")
                logger.error("Continuing with next tick...")
                # Continue the loop - don't stop for individual tick errors
                continue

    except Exception as fatal_error:
        # Handle fatal errors that require strategy shutdown
        logger.error("FATAL ERROR in main trading loop:")
        logger.error(f"Error: {fatal_error}")
        traceback.print_exc()
        
    finally:
        logger.info("STRATEGY SHUTDOWN COMPLETE")
