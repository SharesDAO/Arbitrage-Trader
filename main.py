import calendar
import json
import time

import click
import logging
from logging.handlers import TimedRotatingFileHandler

import requests

from stock_trader import StockTrader
from strategy.dca import DCAStockTrader, execute_dca
from strategy.grid import execute_grid, GridStockTrader
from util.crypto import get_crypto_price, sign_message_by_key
from constants.constant import CONFIG, REQUEST_TIMEOUT, StrategyType, PositionStatus
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


def load_config(did: str, strategy: str, blockchain: str = "CHIA", wallet: int = None):
    CONFIG["BLOCKCHAIN"] = blockchain
    CONFIG["CURRENCY"] = "XCH" if blockchain == "CHIA" else "SOL"
    
    if blockchain == "CHIA":
        if wallet is None:
            raise ValueError("Wallet fingerprint is required for Chia blockchain")
        CONFIG["WALLET_FINGERPRINT"] = wallet
        CONFIG["DID_HEX"] = did[2:] if did.startswith("0x") else did
        STOCKS.update(get_pool_list(1))
    else:  # SOLANA
        CONFIG["DID_HEX"] = did
        STOCKS.update(get_pool_list(2))

    now = calendar.timegm(time.gmtime())
    
    if blockchain == "CHIA":
        signature = sign_message_by_key(f"SharesDAO|Login|{now}", did=CONFIG["DID_HEX"])
    else:  # SOLANA
        signature = sign_message_by_key(f"SharesDAO|Login|{now}")
    
    req = {"did_id": CONFIG["DID_HEX"], "timestamp": now, "signature": signature}
    url = "https://www.sharesdao.com:8443/user/get"
    logger.info(f"Loading trading stategy {strategy} for user {did} on {blockchain}")
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
        elif strategy == "DCA":
            CONFIG["SYMBOLS"] = [s for s in CONFIG["TRADING_SYMBOLS"]]
            if CONFIG["BLOCKCHAIN"] == "CHIA":
                CONFIG["INVESTED_CRYPTO"] = CONFIG.get("INVESTED_XCH", 0)
            elif CONFIG["BLOCKCHAIN"] == "SOLANA":
                CONFIG["INVESTED_CRYPTO"] = CONFIG.get("INVESTED_SOL", 0)
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
    help="Blockchain to use: CHIA or SOLANA",
    type=str,
    default="SOLANA"
)
def run(did: str, strategy: str, blockchain: str, wallet: int = None):
    if strategy.lower() == "dca":
        load_config(did, StrategyType.DCA.value, blockchain.upper(), wallet)
        execute_dca(logger)
    if strategy.lower() == "grid":
        load_config(did, StrategyType.GRID.value, blockchain.upper(), wallet)
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
    help="Blockchain to use: CHIA or SOLANA",
    type=str,
    default="CHIA"
)
def liquidate(did: str, ticker: str, strategy: str, blockchain: str, wallet: int = None):
    load_config(did, strategy.upper(), blockchain.upper(), wallet)
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
    help="Blockchain to use: CHIA or SOLANA",
    type=str,
    default="CHIA"
)
def reset(volume: str, ticker: str, strategy: str, did: str, blockchain: str, wallet: int = None):
    load_config(did, strategy.upper(), blockchain.upper(), wallet)
    if strategy.lower() == "dca":
        stock = DCAStockTrader(ticker, logger)
        if volume is not None:
            stock.volume = float(volume)
        stock.position_status = PositionStatus.TRADABLE.name
        update_position(stock)
        print(f"Successfully reset the stock {stock.stock}, volume {stock.volume}")
    if strategy.lower() == "grid":
        print(f"Grid position cannot be reset!")


cli.add_command(run)
cli.add_command(liquidate)
cli.add_command(reset)
if __name__ == "__main__":
    cli()
