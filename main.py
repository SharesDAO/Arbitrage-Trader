import calendar
import json
import time

import click
import logging
from logging.handlers import TimedRotatingFileHandler

import requests

from strategy.dca import DCAStockTrader, execute_dca
from strategy.grid import execute_grid, GridStockTrader
from util.chia import get_xch_price, sign_message
from constants.constant import CONFIG, REQUEST_TIMEOUT, StrategyType
from util.db import update_position
from util.stock import get_stock_price_from_dinari

logger = logging.getLogger("Rotating Log")
logger.setLevel(logging.INFO)
handler = TimedRotatingFileHandler("trader.log", when="d", interval=1, backupCount=7)
formatter = logging.Formatter("%(asctime)s [%(process)d] [%(levelname)s] %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)


@click.group()
def cli():
    pass


def load_config(wallet: int, did: str, strategy: str):
    CONFIG["WALLET_FINGERPRINT"] = wallet
    CONFIG["DID_HEX"] = did[2:] if did.startswith("0x") else did
    now = calendar.timegm(time.gmtime())
    signature = sign_message(CONFIG["DID_HEX"], f"SharesDAO|Login|{now}")
    req = {"did_id": CONFIG["DID_HEX"], "timestamp": now, "signature": signature}
    url = "https://www.sharesdao.com:8443/user/get"
    logger.info(f"Loading trading stategy {strategy} for user {did}")
    response = requests.post(url, data=json.dumps(req), timeout=REQUEST_TIMEOUT)
    if response.status_code == 200:
        strategy = json.loads(response.json()["trading_strategy"])[strategy]
        CONFIG.update(strategy)
        logger.info(f"Loaded user trading strategy: {CONFIG}")
    else:
        logger.error(f"Failed to get user trading strategy: {response.text}")
        raise Exception("Failed to get user trading strategy")


@click.command("run", help="Runs the trading bot")
@click.option(
    "-w",
    "--wallet",
    help="Your Chia wallet Fingerprint.",
    type=int,
    required=True
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
def run(wallet: int, did: str, strategy: str):
    if strategy.lower() == "dca":
        load_config(wallet, did, StrategyType.DCA.value)
        execute_dca(logger)
    if strategy.lower() == "grid":
        load_config(wallet, did, StrategyType.GRID.value)
        execute_grid(logger)
        pass


@click.command("liquid", help="Liquidates a stock")
@click.option(
    "-w",
    "--wallet",
    help="Your Chia wallet Fingerprint.",
    type=int,
    required=True
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
def liquidate(wallet: int, did: str, ticker: str, strategy: str):
    load_config(wallet, did, strategy.upper())
    if strategy.lower() == "dca":
        stock = DCAStockTrader(ticker, logger)
        if stock.volume >= 0:
            xch_price = get_xch_price(logger)
            stock_price = float(get_stock_price_from_dinari(stock.stock, logger)[1])
            stock.sell_stock(xch_price, stock_price, True)
            update_position(stock)
            print(
                f"Successfully liquidated the stock {stock.stock},  volume {stock.volume}, price {stock.current_price}, total profit {stock.profit}")
    if strategy.lower() == "grid":
        for stock in CONFIG["TRADING_SYMBOLS"]:
            if stock["TICKER"] == ticker:
                xch_price = get_xch_price(logger)
                stock_price = float(get_stock_price_from_dinari(ticker, logger)[1])
                for i in range(stock["GRID_NUM"]):
                    grid = GridStockTrader(i, stock, logger)
                    if grid.volume >= 0:
                        grid.sell_stock(xch_price, stock_price, True)
                        print(
                            f"Successfully liquidated the stock {grid.stock},  volume {grid.volume}, price {grid.current_price}, profit {grid.profit}")
                        update_position(grid)
                break


cli.add_command(run)
cli.add_command(liquidate)
if __name__ == "__main__":
    cli()
