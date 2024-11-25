import time
from datetime import datetime

from chia import get_xch_price, send_asset, get_xch_balance, add_token, check_pending_positions
from constant import BUY_VOLUME, MIN_PROFIT, STOCKS, PositionStatus, MAX_BUY_TIMES, DCA_PERCENTAGE, INVESTED_XCH, \
    TRADING_SYMBOLS, MAX_LOSS_PERCENTAGE

from db import cursor, conn, get_position, update_position, create_position, record_trade, get_last_trade
from stock import is_market_open, get_stock_price_from_dinari


class StockTrader:
    def __init__(self, stock, logger):
        self.logger = logger
        self.stock = stock
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
        # Check if stock token is added to Chia wallet
        self.wallet_id = add_token(self.stock)
        logger.info(f"{self.stock} wallet ID: {self.wallet_id}")

    def load_position(self):
        # Load existing position from the database if available
        result = get_position(self.stock)
        self.logger.info(f"Loaded position for {self.stock}: {result}")
        if result:
            date_format = "%Y-%m-%d %H:%M:%S"
            self.volume, self.buy_count, self.last_buy_price, self.total_cost, self.avg_price, self.current_price, self.profit, self.position_status, self.last_updated = result
            self.last_updated = datetime.strptime(self.last_updated.split(".")[0], date_format)
        else:
            create_position(self)

    def buy_stock(self, xch_volume, xch_price):
        price = float(get_stock_price_from_dinari(self.stock)[1])

        volume = xch_volume * xch_price / price
        if not send_asset(STOCKS[self.stock]["buy_addr"], 1, volume, xch_volume, self.logger):
            # Failed to send order
            return
        self.volume += volume
        self.last_buy_price = price
        self.total_cost += xch_volume
        self.avg_price = self.total_cost / self.volume
        self.current_price = price
        self.profit = self.volume * self.current_price / xch_price / self.total_cost - 1
        self.position_status = PositionStatus.PENDING_BUY.name
        self.buy_count += 1
        self.last_updated = datetime.now()
        record_trade(self.stock, "BUY", price, volume, xch_volume, 0)
        self.logger.info(f"Bought {volume} shares of {self.stock} at ${price}")

    def sell_stock(self, xch_price):
        self.current_price = float(get_stock_price_from_dinari(self.stock)[0])

        request_xch = self.volume * self.current_price / xch_price
        self.profit = request_xch / self.total_cost - 1
        if self.profit >= MIN_PROFIT:
            if not send_asset(STOCKS[self.stock]["sell_addr"], self.wallet_id, request_xch,
                              self.volume, self.logger):
                # Failed to send order
                return
            record_trade(self.stock, "SELL", self.current_price, self.volume, self.total_cost, self.profit)
            self.logger.info(
                f"Sold {self.volume} shares of {self.stock} at ${self.current_price} with {self.profit * 100:.2f}% profit")
            self.position_status = PositionStatus.PENDING_SELL.name
            self.last_updated = datetime.now()

    def handle_price_drop(self, xch_price):
        self.current_price = float(get_stock_price_from_dinari(self.stock)[1])
        self.profit = self.volume * self.current_price / xch_price / self.total_cost - 1
        last_trade = get_last_trade(self.stock)
        last_price = last_trade[5]/last_trade[4]
        drop_percentage = (last_price -  self.current_price/xch_price) / last_price
        self.logger.debug(f"Previous price: {last_price}, Current price: {xch_price / self.current_price}, Drop percentage: {drop_percentage * 100:.2f}%")
        if self.buy_count == MAX_BUY_TIMES and self.profit < -MAX_LOSS_PERCENTAGE:
            request_xch = self.volume * self.current_price / xch_price
            if not send_asset(STOCKS[self.stock]["sell_addr"], self.wallet_id, request_xch,
                              self.volume, self.logger):
                # Failed to send order
                return
            record_trade(self.stock, "SELL", self.current_price, self.volume, self.total_cost, self.profit)
            self.logger.info(
                f"Sold {self.volume} shares of {self.stock} at ${self.current_price} with {self.profit * 100:.2f}% profit, since the loss exceeded the maximum loss percentage")
            self.position_status = PositionStatus.PENDING_SELL.name
            self.last_updated = datetime.now()
            return
        if drop_percentage >= DCA_PERCENTAGE and self.buy_count < MAX_BUY_TIMES:  # 5% drop
            self.logger.info(f"Price dropped by 5% for {self.stock}, repurchasing...")
            self.buy_stock(BUY_VOLUME, xch_price)  # Repurchase the same volume



def execute_trading(logger):
    # Current market status
    traders = [StockTrader(stock, logger) for stock in TRADING_SYMBOLS]

    while True:
        xch_price = get_xch_price(logger)

        stock_balance = 0
        for trader in traders:
            if trader.position_status == PositionStatus.TRADABLE.name and is_market_open():
                if trader.volume == 0:
                    trader.buy_stock(BUY_VOLUME, xch_price)
                elif trader.volume > 0:
                    trader.sell_stock(xch_price)  # Try to sell if profit threshold is met
                    if trader.position_status == PositionStatus.TRADABLE.name:
                        trader.handle_price_drop(xch_price)  # Handle price drop and repurchase logic
            else:
                trader.current_price = float(get_stock_price_from_dinari(trader.stock)[1])
                if trader.total_cost > 0:
                    trader.profit = trader.volume * trader.current_price / xch_price / trader.total_cost - 1

            # log stock current price, acg price, and profit
            logger.info(
                f"{trader.stock}: Current Price: {trader.current_price / xch_price} XCH, Average Price: {trader.avg_price} XCH, Profit: {trader.profit * 100:.2f}%, Bought Count: {trader.buy_count}, Value: {trader.volume * trader.current_price} status: {trader.position_status}")
            stock_balance += trader.volume * trader.current_price
            update_position(trader)
        # Check if the positions are still pending
        check_pending_positions(traders, logger)
        # Get XCH balance
        xch_balance = get_xch_balance()
        total_xch = xch_balance + stock_balance / xch_price
        logger.info(
            f"Total Stock Balance: {stock_balance} USD, Total XCH Balance: {xch_balance} XCH, XCH In Total: {total_xch} XCH, profit: {(total_xch / INVESTED_XCH - 1) * 100:.2f}%")
        if is_market_open():
            time.sleep(60)  # Wait a minute before checking again
        else:
            time.sleep(300)
