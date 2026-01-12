import calendar
import json
import os
import time

import click
import logging
from logging.handlers import TimedRotatingFileHandler

import requests

from stock_trader import StockTrader
from strategy.dca import DCAStockTrader, execute_dca
from strategy.grid import execute_grid, GridStockTrader
from util.crypto import get_crypto_price, sign_message, sync_transactions_manual, check_pending_positions
from constants.constant import CONFIG, REQUEST_TIMEOUT, StrategyType, PositionStatus, EVM_CHAINS
from util.db import update_position
from util.stock import STOCKS, get_pool_list, get_stock_price

logger = logging.getLogger("Rotating Log")
logger.setLevel(logging.INFO)
handler = TimedRotatingFileHandler("trader.log", when="d", interval=1, backupCount=7)
formatter = logging.Formatter("%(asctime)s [%(process)d] [%(levelname)s] %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)


@click.group()
def cli():
    pass


def load_config(did: str, strategy: str, blockchain: str = "CHIA", wallet: int = None, evm_chain: str = None):
    CONFIG["BLOCKCHAIN"] = blockchain
    
    if blockchain == "CHIA":
        CONFIG["CURRENCY"] = "XCH"
        if wallet is None:
            raise ValueError("Wallet fingerprint is required for Chia blockchain")
        CONFIG["WALLET_FINGERPRINT"] = wallet
        CONFIG["DID_HEX"] = did[2:] if did.startswith("0x") else did
        STOCKS.update(get_pool_list(1))
    elif blockchain == "SOLANA":
        CONFIG["CURRENCY"] = "SOL"
        CONFIG["DID_HEX"] = did
        STOCKS.update(get_pool_list(2))
    elif blockchain == "EVM":
        if evm_chain is None:
            raise ValueError("Chain parameter is required for EVM blockchain (ethereum/base/arbitrum/bsc)")
        if evm_chain.lower() not in EVM_CHAINS:
            raise ValueError(f"Unsupported EVM chain: {evm_chain}. Supported chains: {list(EVM_CHAINS.keys())}")
        CONFIG["CURRENCY"] = "USDC"
        CONFIG["EVM_CHAIN"] = evm_chain.lower()
        chain_config = EVM_CHAINS[CONFIG["EVM_CHAIN"]]
        CONFIG["CHAIN_ID"] = chain_config["chain_id"]
        CONFIG["NATIVE_SYMBOL"] = chain_config["native_symbol"]
        CONFIG["USDC_ADDRESS"] = chain_config["usdc_address"]
        CONFIG["USDC_DECIMALS"] = chain_config["usdc_decimals"]
        
        # Try to use Alchemy API key if available (shared across all chains), otherwise use RPC_URL
        alchemy_api_key = os.environ.get("ALCHEMY_API_KEY")
        
        if alchemy_api_key:
            # Build Alchemy URL using shared API key
            from constants.constant import ALCHEMY_URLS
            base_url = ALCHEMY_URLS.get(evm_chain.lower())
            if base_url:
                CONFIG["RPC_URL"] = f"{base_url}/{alchemy_api_key}"
            else:
                # Fallback to RPC_URL if chain not supported by Alchemy
                CONFIG["RPC_URL"] = os.environ.get(chain_config["rpc_env"])
        else:
            CONFIG["RPC_URL"] = os.environ.get(chain_config["rpc_env"])
        
        if not CONFIG["RPC_URL"]:
            rpc_msg = f"RPC URL not found. Please set {chain_config['rpc_env']} environment variable"
            rpc_msg += " or ALCHEMY_API_KEY for Alchemy API (shared across all chains)"
            raise ValueError(rpc_msg)
        CONFIG["DID_HEX"] = did
        STOCKS.update(get_pool_list(6))
    else:
        raise ValueError(f"Unsupported blockchain: {blockchain}")

    now = calendar.timegm(time.gmtime())
    
    if blockchain == "CHIA":
        signature = sign_message(CONFIG["DID_HEX"], f"SharesDAO|Login|{now}")
    elif blockchain == "SOLANA":
        signature = sign_message(CONFIG["DID_HEX"], f"SharesDAO|Login|{now}")
    elif blockchain == "EVM":
        signature = sign_message(CONFIG["DID_HEX"], f"SharesDAO|Login|{now}")
    
    req = {"did_id": CONFIG["DID_HEX"], "timestamp": now, "signature": signature}
    url = "https://www.sharesdao.com:8443/user/get"
    logger.info(f"Loading trading stategy {strategy} for user {did} on {blockchain}" + (f" ({evm_chain})" if evm_chain else ""))
    response = requests.post(url, data=json.dumps(req), timeout=REQUEST_TIMEOUT)
    if response.status_code == 200:
        strategy_config = json.loads(response.json()["trading_strategy"])[strategy]
        CONFIG["ADDRESS"] = response.json()["address"]
        CONFIG.update(strategy_config)
        
        # Set crypto min/max based on blockchain
        if strategy == "GRID":
            CONFIG["SYMBOLS"] = [s["TICKER"] for s in CONFIG["TRADING_SYMBOLS"]]
            if CONFIG["BLOCKCHAIN"] == "CHIA":
                CONFIG["CRYPTO_MIN"] = CONFIG.get("XCH_MIN", 0)
                CONFIG["CRYPTO_MAX"] = CONFIG.get("XCH_MAX", 0)
            elif CONFIG["BLOCKCHAIN"] == "SOLANA":
                CONFIG["CRYPTO_MIN"] = CONFIG.get("SOL_MIN", 0)
                CONFIG["CRYPTO_MAX"] = CONFIG.get("SOL_MAX", 0)
            elif CONFIG["BLOCKCHAIN"] == "EVM":
                CONFIG["CRYPTO_MIN"] = CONFIG.get("USDC_MIN", 0)
                CONFIG["CRYPTO_MAX"] = CONFIG.get("USDC_MAX", 0)
        elif strategy == "DCA":
            CONFIG["SYMBOLS"] = [s for s in CONFIG["TRADING_SYMBOLS"]]
            if CONFIG["BLOCKCHAIN"] == "CHIA":
                CONFIG["INVESTED_CRYPTO"] = CONFIG.get("INVESTED_XCH", 0)
            elif CONFIG["BLOCKCHAIN"] == "SOLANA":
                CONFIG["INVESTED_CRYPTO"] = CONFIG.get("INVESTED_SOL", 0)
            elif CONFIG["BLOCKCHAIN"] == "EVM":
                CONFIG["INVESTED_CRYPTO"] = CONFIG.get("INVESTED_USDC", 0)
        logger.info(f"Loaded user trading strategy: {CONFIG}")
    else:
        logger.error(f"Failed to get user trading strategy: {response.text}")
        raise Exception("Failed to get user trading strategy")


@click.command("run", help="Runs the trading bot")
@click.option(
    "-w",
    "--wallet",
    help="Your Chia wallet Fingerprint (required for Chia blockchain).",
    type=int,
    required=False
)
@click.option(
    "-d",
    "--did",
    help="Your DID ID Hex. It must be registered on the SharesDAO.com",
    type=str,
    required=True
)
@click.option(
    "-s",
    "--strategy",
    help="Your trading strategy name, e.g DCA, Grid",
    type=str,
    required=True
)
@click.option(
    "-b",
    "--blockchain",
    help="Blockchain to use: CHIA, SOLANA, or EVM",
    type=str,
    default="SOLANA"
)
@click.option(
    "-c",
    "--chain",
    help="EVM chain to use (required for EVM blockchain): ethereum, base, arbitrum, or bsc",
    type=str,
    required=False
)
def run(did: str, strategy: str, blockchain: str, wallet: int = None, chain: str = None):
    if strategy.lower() == "dca":
        load_config(did, StrategyType.DCA.value, blockchain.upper(), wallet, chain)
        execute_dca(logger)
    if strategy.lower() == "grid":
        load_config(did, StrategyType.GRID.value, blockchain.upper(), wallet, chain)
        execute_grid(logger)


@click.command("liquid", help="Liquidates a stock")
@click.option(
    "-w",
    "--wallet",
    help="Your Chia wallet Fingerprint (required for Chia blockchain).",
    type=int,
    required=False
)
@click.option(
    "-d",
    "--did",
    help="Your DID ID Hex. It must be registered on the SharesDAO.com",
    type=str,
    required=True
)
@click.option(
    "-t",
    "--ticker",
    help="The stock ticker you want to liquidate",
    type=str,
    required=True
)
@click.option(
    "-s",
    "--strategy",
    help="Your trading strategy name, e.g DCA, Grid",
    type=str,
    required=True
)
@click.option(
    "-b",
    "--blockchain",
    help="Blockchain to use: CHIA, SOLANA, or EVM",
    type=str,
    default="CHIA"
)
@click.option(
    "-c",
    "--chain",
    help="EVM chain to use (required for EVM blockchain): ethereum, base, arbitrum, or bsc",
    type=str,
    required=False
)
def liquidate(did: str, ticker: str, strategy: str, blockchain: str, wallet: int = None, chain: str = None):
    load_config(did, strategy.upper(), blockchain.upper(), wallet, chain)
    if strategy.lower() == "dca":
        stock = DCAStockTrader(ticker, logger)
        if stock.volume >= 0:
            crypto_price = get_crypto_price(logger)
            stock_price = get_stock_price(stock.stock, logger)[0]
            stock.sell_stock(crypto_price, stock_price, True)
            update_position(stock)
            print(
                f"Successfully liquidated the stock {stock.stock}, volume {stock.volume}, price {stock.current_price}, total profit {stock.profit}")
    if strategy.lower() == "grid":
        for stock in CONFIG["TRADING_SYMBOLS"]:
            if stock["TICKER"] == ticker:
                crypto_price = get_crypto_price(logger)
                stock_price = get_stock_price(ticker, logger)[0]
                for i in range(stock["GRID_NUM"]):
                    grid = GridStockTrader(i, stock, logger)
                    if grid.volume > 0 and grid.position_status == PositionStatus.TRADABLE.name:
                        grid.sell_stock(crypto_price, stock_price, True)
                        print(
                            f"Successfully liquidated the stock {grid.stock}, volume {grid.volume}, price {grid.current_price}, profit {grid.profit}")
                        update_position(grid)
                break


@click.command("reset", help="Reset a stock position")
@click.option(
    "-v",
    "--volume",
    help="The actual volume of your stock",
    type=str,
    required=False
)
@click.option(
    "-w",
    "--wallet",
    help="Your Chia wallet Fingerprint (required for Chia blockchain).",
    type=int,
    required=False
)
@click.option(
    "-d",
    "--did",
    help="Your DID ID Hex. It must be registered on the SharesDAO.com",
    type=str,
    required=True
)
@click.option(
    "-t",
    "--ticker",
    help="The stock ticker you want to reset",
    type=str,
    required=True
)
@click.option(
    "-s",
    "--strategy",
    help="Your trading strategy name, e.g DCA, Grid",
    type=str,
    required=True
)
@click.option(
    "-b",
    "--blockchain",
    help="Blockchain to use: CHIA, SOLANA, or EVM",
    type=str,
    default="CHIA"
)
@click.option(
    "-c",
    "--chain",
    help="EVM chain to use (required for EVM blockchain): ethereum, base, arbitrum, or bsc",
    type=str,
    required=False
)
def reset(volume: str, ticker: str, strategy: str, did: str, blockchain: str, wallet: int = None, chain: str = None):
    load_config(did, strategy.upper(), blockchain.upper(), wallet, chain)
    if strategy.lower() == "dca":
        stock = DCAStockTrader(ticker, logger)
        if volume is not None:
            stock.volume = float(volume)
        stock.position_status = PositionStatus.TRADABLE.name
        update_position(stock)
        print(f"Successfully reset the stock {stock.stock}, volume {stock.volume}")
    if strategy.lower() == "grid":
        print(f"Grid position cannot be reset!")


@click.command("sync", help="Manually sync transactions and check pending orders")
@click.option(
    "-d",
    "--did",
    help="Your DID ID Hex. It must be registered on the SharesDAO.com",
    type=str,
    required=True
)
@click.option(
    "-s",
    "--strategy",
    help="Your trading strategy name, e.g DCA, Grid",
    type=str,
    required=True
)
@click.option(
    "-b",
    "--blockchain",
    help="Blockchain to use: CHIA, SOLANA, or EVM",
    type=str,
    default="EVM"
)
@click.option(
    "-c",
    "--chain",
    help="EVM chain to use (required for EVM blockchain): ethereum, base, arbitrum, or bsc",
    type=str,
    required=False
)
@click.option(
    "--days",
    help="Number of days to look back (e.g., 7 for 7 days)",
    type=int,
    required=False
)
@click.option(
    "--from-block",
    help="Specific block number to start from",
    type=int,
    required=False
)
@click.option(
    "--reset",
    help="Reset last_checked_block to force re-checking all transactions",
    is_flag=True,
    default=False
)
def sync(did: str, strategy: str, blockchain: str, chain: str = None, days: int = None, from_block: int = None, reset: bool = False):
    if blockchain.upper() != "EVM":
        print("Manual sync is only supported for EVM chains")
        return
    
    load_config(did, strategy.upper(), blockchain.upper(), None, chain)
    
    # Sync transactions
    result = sync_transactions_manual(logger, days=days, from_block=from_block, reset_last_checked=reset)
    
    if not result.get("success"):
        print(f"Sync failed: {result.get('error', 'Unknown error')}")
        return
    
    print(f"Sync completed:")
    print(f"  From block: {result.get('from_block')}")
    print(f"  Current block: {result.get('current_block')}")
    print(f"  Transactions found: {result.get('transactions_found')}")
    print(f"  Token breakdown: {result.get('token_txs')}")
    
    # Load traders and check pending positions
    if strategy.lower() == "dca":
        from strategy.dca import DCAStockTrader
        traders = [DCAStockTrader(stock, logger) for stock in CONFIG["TRADING_SYMBOLS"]]
    elif strategy.lower() == "grid":
        from strategy.grid import GridStockTrader
        traders = []
        for stock in CONFIG["TRADING_SYMBOLS"]:
            ticker = stock if isinstance(stock, str) else stock.get("TICKER")
            grid_num = stock.get("GRID_NUM", 5) if isinstance(stock, dict) else 5
            for i in range(grid_num):
                traders.append(GridStockTrader(i, ticker, logger))
    else:
        print(f"Unknown strategy: {strategy}")
        return
    
    # Check pending positions
    try:
        check_pending_positions(traders, logger)
        print("Pending positions check completed")
    except Exception as e:
        logger.error(f"Failed to check pending positions: {e}")
        print(f"Error checking pending positions: {e}")


cli.add_command(run)
cli.add_command(liquidate)
cli.add_command(reset)
cli.add_command(sync)
if __name__ == "__main__":
    cli()
