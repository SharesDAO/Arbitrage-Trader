import logging
from logging.handlers import TimedRotatingFileHandler

from stock_trader import execute_trading

if __name__ == "__main__":
    logger = logging.getLogger("Rotating Log")
    logger.setLevel(logging.INFO)
    handler = TimedRotatingFileHandler("trader.log", when="d", interval=1, backupCount=7)
    formatter = logging.Formatter("%(asctime)s [%(process)d] [%(levelname)s] %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    execute_trading(logger)