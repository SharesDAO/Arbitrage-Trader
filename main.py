import json
import logging
import sys
from logging.handlers import TimedRotatingFileHandler

from chia import get_xch_price
from stock_trader import execute_trading, StockTrader

if __name__ == "__main__":
    logger = logging.getLogger("Rotating Log")
    logger.setLevel(logging.INFO)
    handler = TimedRotatingFileHandler("trader.log", when="d", interval=1, backupCount=7)
    formatter = logging.Formatter("%(asctime)s [%(process)d] [%(levelname)s] %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    args = sys.argv
    if len(args) <= 1:
        execute_trading(logger)
    else:
        action = args[1]
        if action == "liquid":
            if len(args) == 3:
                stock = StockTrader(args[2], logger)
                if stock.volume >= 0:
                    xch_price = get_xch_price(logger)
                    stock.sell_stock(xch_price, True)
                    print(f"Successfully liquidated the stock {stock}")
            else:
                print("Please input the stock ticker you want to liquidate")
        else:
            print("Invalid action")