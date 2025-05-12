import time
from datetime import datetime

from util.crypto import add_token
from constants.constant import PositionStatus, CONFIG



class StockTrader:
    def __init__(self, stock, ticker, logger):
        self.logger = logger
        self.stock = stock
        self.ticker = ticker
        self.volume = 0
        self.buy_count = 0
        self.last_buy_price = 0
        self.total_cost = 0
        self.avg_price = 0
        self.current_price = 0
        self.profit = 0
        self.position_status = PositionStatus.TRADABLE.name
        self.last_updated = datetime.now()
        self.load_position()
        # Check if stock token is added to wallet
        # Only do this for Chia
        if CONFIG["BLOCKCHAIN"] == "CHIA":
            self.wallet_id = add_token(self.ticker)
            logger.info(f"{self.ticker} wallet ID: {self.wallet_id}")

    def load_position(self):
        pass

    def buy_stock(self, crypto_volume, crypto_price, stock_price):
        pass

    def sell_stock(self, crypto_price, stock_price, liquid=False):
        pass

    def handle_price_drop(self, crypto_price, stock_buy_price, stock_sell_price):
        pass

    def adjust_volume(self, total_volume):
        pass