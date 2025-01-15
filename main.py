import calendar
import json
import time

import click
import logging
import sys
from logging.handlers import TimedRotatingFileHandler

import requests

from chia import get_xch_price, sign_message
from constant import CONFIG, REQUEST_TIMEOUT, PositionStatus
from db import update_position
from stock_trader import execute_trading, StockTrader

logger = logging.getLogger("Rotating Log")
logger.setLevel(logging.INFO)
handler = TimedRotatingFileHandler("trader.log", when="d", interval=1, backupCount=7)
formatter = logging.Formatter("%(asctime)s [%(process)d] [%(levelname)s] %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)


@click.group()
def cli():
    pass


def load_config(wallet: int, did: str):
    CONFIG["WALLET_FINGERPRINT"] = wallet
    CONFIG["DID_HEX"] = did[2:] if did.startswith("0x") else did
    now = calendar.timegm(time.gmtime())
    signature = sign_message(CONFIG["DID_HEX"], f"SharesDAO|Login|{now}")
    req = {"did_id": CONFIG["DID_HEX"], "timestamp": now, "signature": signature}
    url = "https://www.sharesdao.com:8443/user/get"
    response = requests.post(url, data=json.dumps(req), timeout=REQUEST_TIMEOUT)
    if response.status_code == 200:
        dca = json.loads(response.json()["trading_strategy"])["DCA"]
        CONFIG.update(dca)
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
def run(wallet: int, did: str):
    load_config(wallet, did)
    execute_trading(logger)


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
def liquidate(wallet: int, did: str, ticker: str):
    load_config(wallet, did)
    stock = StockTrader(ticker, logger)
    if stock.volume >= 0:
        xch_price = get_xch_price(logger)
        stock.sell_stock(xch_price, True)
        update_position(stock)
        print(
            f"Successfully liquidated the stock {stock.stock},  volume {stock.volume}, price {stock.current_price}, total profit {stock.profit}")

@click.command("reset", help="Reset a stock")
@click.option(
    "-v",
    "--volume",
    help="The actual volume of your stock",
    type=str,
    required=False
)
@click.option(
    "-t",
    "--ticker",
    help="The stock ticker you want to reset",
    type=str,
    required=True
)
def reset(volume: str, ticker: str):
    stock = StockTrader(ticker, logger)
    if volume is not None:
        stock.volume = float(volume)
    stock.position_status = PositionStatus.TRADABLE
    update_position(stock)


cli.add_command(run)
cli.add_command(liquidate)
cli.add_command(reset)
if __name__ == "__main__":
    cli()
