import calendar
import json
import time
import os
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
from util.sharesdao import get_pool_by_id
from util.stock import STOCKS, get_stock_price

logger = logging.getLogger("Rotating Log")
logger.setLevel(logging.INFO)
handler = TimedRotatingFileHandler("trader.log", when="d", interval=1, backupCount=7)
formatter = logging.Formatter("%(asctime)s [%(process)d] [%(levelname)s] %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)


@click.group()
def cli():
    pass


def load_config(strategy: str):
    CONFIG["POOL_ID"] = os.environ["SHARESDAO_FUND_POOL_ID"]
    pool = get_pool_by_id(CONFIG["POOL_ID"])
    CONFIG["BLOCKCHAIN"] = "CHIA" if pool["blockchain"] == 1 else "SOLANA"
    # Exclude stocks that are not supported by the current blockchain in a for loop
    for stock in STOCKS:
        if stock["blockchain"] != pool["blockchain"]:
            STOCKS.remove(stock)
    CONFIG["CURRENCY"] = "XCH" if pool["currency"] == 1 else "SOL"
    now = calendar.timegm(time.gmtime())
    signature = sign_message_by_key(f"SharesDAO|Login|{now}")
    req = {"did_id": pool["owner_did"], "timestamp": now, "signature": signature}
    url = "https://www.sharesdao.com:8443/user/get"
    logger.info(f"Loading trading stategy {strategy} for user {pool['owner_did']}")
    response = requests.post(url, data=json.dumps(req), timeout=REQUEST_TIMEOUT)
    if response.status_code == 200:
        strategy = json.loads(response.json()["trading_strategy"])[strategy]
        CONFIG.update(strategy)
        if strategy == "DCA":
            if CONFIG["BLOCKCHAIN"] == "CHIA":
                CONFIG["CRYPTO_MIN"] = CONFIG["XCH_MIN"]
                CONFIG["CRYPTO_MAX"] = CONFIG["XCH_MAX"]
            elif CONFIG["BLOCKCHAIN"] == "SOLANA":
                CONFIG["CRYPTO_MIN"] = CONFIG["SOL_MIN"]
                CONFIG["CRYPTO_MAX"] = CONFIG["SOL_MAX"]
        logger.info(f"Loaded user trading strategy: {CONFIG}")
        data = json.loads(pool["description"])
        CONFIG["ADDRESS"] = data["address"]
        CONFIG["VAULT_HOST"] = data["host"]
    else:
        logger.error(f"Failed to get user trading strategy: {response.text}")
        raise Exception("Failed to get user trading strategy")


@click.command("run", help="Runs the trading bot")
@click.option(
    "-s",
    "--strategy",
    help="Your trading strategy name, e.g DCA, Grid",
    type=str,
    required=True
)
def run(strategy: str):
    if strategy.lower() == "dca":
        load_config(StrategyType.DCA.value)
        execute_dca(logger)
    elif strategy.lower() == "grid":
        load_config(StrategyType.GRID.value)
        execute_grid(logger)


@click.command("liquid", help="Liquidates a stock")
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
def liquidate(ticker: str, strategy: str):
    load_config(strategy.upper())
    if strategy.lower() == "dca":
        stock = DCAStockTrader(ticker, logger)
        if stock.volume >= 0:
            xch_price = get_crypto_price(logger)
            stock_price = get_stock_price(stock.stock, logger)[0]
            stock.sell_stock(xch_price, stock_price, True)
            update_position(stock)
            print(
                f"Successfully liquidated the stock {stock.stock},  volume {stock.volume}, price {stock.current_price}, total profit {stock.profit}")
    if strategy.lower() == "grid":
        for stock in CONFIG["TRADING_SYMBOLS"]:
            if stock["TICKER"] == ticker:
                xch_price = get_crypto_price(logger)
                stock_price = get_stock_price(ticker, logger)[0]
                for i in range(stock["GRID_NUM"]):
                    grid = GridStockTrader(i, stock, logger)
                    if grid.volume >= 0:
                        grid.sell_stock(xch_price, stock_price, True)
                        print(
                            f"Successfully liquidated the stock {grid.stock},  volume {grid.volume}, price {grid.current_price}, profit {grid.profit}")
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
    help="Your Chia wallet Fingerprint.",
    type=int,
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
def reset(volume: str, wallet: int, ticker: str, strategy: str):
    CONFIG["WALLET_FINGERPRINT"] = wallet
    if strategy.lower() == "dca":
        stock = DCAStockTrader(ticker, logger)
        if volume is not None:
            stock.volume = float(volume)
        stock.position_status = PositionStatus.TRADABLE.name
        update_position(stock)
        print(f"Successfully reset the stock {stock.stock},  volume {stock.volume}")
    if strategy.lower() == "grid":
        print(f"Grid position cannot be reset!")


cli.add_command(run)
cli.add_command(liquidate)
cli.add_command(reset)
if __name__ == "__main__":
    cli()
