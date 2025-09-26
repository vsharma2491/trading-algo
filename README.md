# Algorithmic Trading Framework

This repository contains a Python-based framework for developing and running algorithmic trading strategies. It currently features the "Survivor" options trading strategy and includes a modular architecture that supports multiple brokers.

## Disclaimer

This framework is provided for **educational and informational purposes only**. Trading in financial markets involves substantial risk, and you may lose all or more than your initial investment. By using this software, you acknowledge that all trading decisions are made at your own risk. The creators assume no liability for any financial losses incurred through its use. **Always do your own research and consult with a qualified financial advisor before trading.**

## Architecture

The framework is designed with a modular architecture to separate concerns and allow for easy extension. The core components are:

-   `brokers/`: Contains broker-specific implementations for handling authentication, market data, and order execution.
    -   `base.py`: An abstract base class that defines the common interface for all brokers.
    -   `fyers.py`: Implementation for the Fyers API.
    -   `zerodha.py`: Implementation for the Zerodha Kite Connect API.
-   `strategy/`: Houses the trading strategy logic.
    -   `survivor.py`: Implements the Survivor options trading strategy.
    -   `configs/survivor.yml`: The default configuration file for the Survivor strategy.
-   `dispatcher.py`: A centralized data dispatcher that routes market data from the broker's WebSocket to the strategy for processing.
-   `orders.py`: An `OrderTracker` class that manages the lifecycle of orders, including persistence to a JSON file.
-   `logger.py`: A centralized logging system that sets up file and console logging for the application.

## Setup

### 1. Install Dependencies

This project uses `uv` for fast dependency management.

First, install `uv`:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# Or, if you prefer pip:
# pip install uv
```

Then, sync the project dependencies:
```bash
uv sync
```
Alternatively, you can generate a `requirements.txt` from `pyproject.toml` and use `pip`.

### 2. Configure Environment Variables

Create a `.env` file by copying the sample file:
```bash
cp .sample.env .env
```

Now, edit the `.env` file and provide your broker credentials.

**For Fyers:**
```
BROKER_NAME=fyers
BROKER_API_KEY=<YOUR_API_KEY>
BROKER_API_SECRET=<YOUR_API_SECRET>
BROKER_TOTP_ENABLE=true
BROKER_ID=<YOUR_FYERS_ID>
BROKER_TOTP_REDIDRECT_URI=<YOUR_TOTP_REDIRECT_URI>
BROKER_TOTP_KEY=<YOUR_TOTP_SECRET>
BROKER_TOTP_PIN=<YOUR_4_DIGIT_PIN>
```

**For Zerodha (with TOTP):**
```
BROKER_NAME=zerodha
BROKER_API_KEY=<YOUR_API_KEY>
BROKER_API_SECRET=<YOUR_API_SECRET>
BROKER_TOTP_ENABLE=true
BROKER_ID=<YOUR_ZERODHA_ID>
BROKER_PASSWORD=<YOUR_ZERODHA_PASSWORD>
BROKER_TOTP_KEY=<YOUR_TOTP_SECRET>
```

**For Zerodha (without TOTP - Manual Login):**
If you prefer to log in manually by providing a request token, set `BROKER_TOTP_ENABLE` to `false`.
```
BROKER_NAME=zerodha
BROKER_API_KEY=<YOUR_API_KEY>
BROKER_API_SECRET=<YOUR_API_SECRET>
BROKER_TOTP_ENABLE=false
```

## Running the Survivor Strategy

The main execution script for the Survivor strategy is located in `strategy/survivor.py`.

### Basic Usage
To run the strategy with the default configuration from `strategy/configs/survivor.yml`, navigate to the `strategy` directory and run:
```bash
cd strategy/
python survivor.py
```
**Note:** The script includes a validation step that requires you to confirm if you are running with default parameters.

### Customizing Parameters
You can override any parameter from the command line.

**Example:** Run with a different option series and custom gaps.
```bash
cd strategy/
python survivor.py \
    --symbol-initials NIFTY25DEC45 \
    --pe-gap 30 --ce-gap 30 \
    --pe-quantity 50 --ce-quantity 50 \
    --min-price-to-sell 20
```

### Viewing Configuration
To see the final configuration after applying command-line overrides, use the `--show-config` flag:
```bash
cd strategy/
python survivor.py --show-config
```

For a full list of configurable parameters, run:
```bash
cd strategy/
python survivor.py --help
```

## Codebase Documentation

The entire codebase is documented with comprehensive docstrings following the Google Python Style Guide. You can explore the source code to understand the implementation details of each component.

For more details, check the individual broker implementations and the strategy logic in the `strategy/` folder.