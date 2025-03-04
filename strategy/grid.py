import math
import time
from datetime import datetime

from stock_trader import StockTrader
from util.chia import get_xch_price, send_asset, get_xch_balance, add_token, check_pending_positions, get_cat_balance
from constants.constant import PositionStatus, CONFIG, StrategyType

from util.db import get_position, update_position, create_position, record_trade
from util.stock import is_market_open, get_stock_price, STOCKS


#For Grid trading, buy_count = arbitrage times, profit = agg gain
class GridStockTrader(StockTrader):
    def __init__(self, index, stock, logger):
        self.type = StrategyType.GRID
        self.grid_num = int(stock["GRID_NUM"])
        self.max_price = float(stock["MAX_PRICE"])
        self.min_price = float(stock["MIN_PRICE"])
        self.invested_xch = float(stock["INVEST_XCH"])
        self.ticker = stock["TICKER"]
        self.index = index
        self.grid_width = ((self.max_price / CONFIG["XCH_MIN"] - self.min_price / CONFIG[
            "XCH_MAX"]) / self.grid_num)
        super().__init__(f"{self.ticker}-Grid{self.index}", self.ticker, logger)

    def load_position(self):
        result = get_position(self.stock)
        self.logger.info(f"Loaded position for {self.stock}: {result}")
        if result:
            date_format = "%Y-%m-%d %H:%M:%S"
            self.volume, self.buy_count, self.last_buy_price, self.total_cost, self.avg_price, self.current_price, self.profit, self.position_status, self.last_updated = result
            self.last_updated = datetime.strptime(self.last_updated.split(".")[0], date_format)
        else:
            create_position(self)

    def buy_stock(self, xch_volume, xch_price, stock_price):
        buy_price = self.max_price / CONFIG["XCH_MIN"] - (self.index+1) * self.grid_width
        while buy_price - self.grid_width > stock_price / xch_price:
            buy_price -= self.grid_width
        volume = xch_volume / buy_price
        timestamp = datetime.now()
        if not send_asset(STOCKS[self.ticker]["buy_addr"], 1, volume, xch_volume, self.logger, self.stock):
            # Failed to send order
            return
        self.volume = volume
        self.last_buy_price = buy_price
        self.total_cost = xch_volume
        self.avg_price = self.total_cost / self.volume
        self.current_price = stock_price
        self.position_status = PositionStatus.PENDING_BUY.name
        self.last_updated = timestamp
        record_trade(self.stock, "BUY", buy_price * xch_price, volume, xch_volume, 0)
        self.logger.info(f"Buying {volume} shares of {self.stock} at {buy_price} XCH")

    def sell_stock(self, xch_price, stock_price, liquid=False):
        self.current_price = stock_price
        sell_price = self.max_price / CONFIG["XCH_MIN"] - self.index * self.grid_width
        while sell_price + self.grid_width < stock_price / xch_price:
            sell_price += self.grid_width
        if liquid:
            sell_price = stock_price / xch_price
        request_xch = self.volume * sell_price
        timestamp = datetime.now()
        if not send_asset(STOCKS[self.ticker]["sell_addr"], self.wallet_id, request_xch,
                          self.volume, self.logger, self.stock, "MARKET" if liquid else "LIMIT"):
            # Failed to send order
            return
        record_trade(self.stock, "SELL", sell_price * xch_price, self.volume, self.total_cost, request_xch - self.total_cost)
        self.logger.info(
            f"Selling {self.volume} shares of {self.stock} at {sell_price} XCH with {request_xch - self.total_cost} XCH profit")
        self.position_status = PositionStatus.PENDING_SELL.name
        self.last_updated = timestamp

    def adjust_volume(self, total_volume):
        # Get current stock balance
        balance = get_cat_balance(self.ticker)
        if balance is None:
            self.logger.error(f"Failed to get balance for {self.stock}, skipping...")
            return
        if self.position_status == PositionStatus.TRADABLE.name and total_volume > 0:
            self.volume = math.floor(self.volume / total_volume * balance * 1000) / 1000
            self.logger.info(f"Adjusting volume for {self.stock} to {self.volume}")


def execute_grid(logger):
    traders = []
    for stock in CONFIG["TRADING_SYMBOLS"]:
        # Create grids for each stock
        total_volume = 0
        grid_traders = []
        for i in range(stock["GRID_NUM"]):
            trader = GridStockTrader(i, stock, logger)
            if trader.position_status == PositionStatus.TRADABLE.name:
                total_volume += trader.volume
            grid_traders.append(trader)
        traders.extend(grid_traders)
        balance = get_cat_balance(stock["TICKER"])
        if balance is None:
            logger.error(f"Failed to get balance for {stock['TICKER']}, skipping...")
            continue
        if abs(total_volume - balance) > 0.001:
            # Adjust volume for each grid trader
            for trader in grid_traders:
                trader.adjust_volume(total_volume)

    while True:
        stocks_stats = {}
        stock_balance = 0
        # Check if the positions are still pending
        try:
            check_pending_positions(traders, logger)
        except Exception as e:
            logger.error(f"Failed to check pending positions, please check your Chia wallet. {e}")
        for trader in traders:
            if trader.ticker not in stocks_stats:
                stocks_stats[trader.ticker] = {"buying": 0, "selling": 0, "position": 0, "volume": 0, "arbitrage": 0, "profit": 0, "cost": 0, "value": 0, "grid": trader.grid_num, "invest": trader.invested_xch}
            current_buy_price, current_sell_price = get_stock_price(trader.ticker, logger)
            if current_buy_price == 0:
                logger.error(f"Failed to get stock price for {trader.ticker}, skipping...")
                continue
            xch_price = get_xch_price(logger)
            if xch_price is None:
                logger.error("Failed to get XCH price, skipping...")
                continue
            if trader.volume > 0 and trader.position_status != PositionStatus.PENDING_BUY.name:
                stocks_stats[trader.ticker]["position"] += 1
            stocks_stats[trader.ticker]["arbitrage"] += trader.buy_count
            stocks_stats[trader.ticker]["profit"] += trader.profit
            stocks_stats[trader.ticker]["volume"] += trader.volume
            stocks_stats[trader.ticker]["value"] += trader.volume * (current_sell_price+current_buy_price) /2
            stocks_stats[trader.ticker]["cost"] += trader.total_cost
            if trader.position_status == PositionStatus.TRADABLE.name and is_market_open(logger):
                if trader.max_price / CONFIG["XCH_MIN"] - trader.index * trader.grid_width >= current_sell_price / xch_price and trader.volume == 0:
                    trader.buy_stock(trader.invested_xch / trader.grid_num, xch_price, current_sell_price)
                elif trader.max_price / CONFIG["XCH_MIN"] - (trader.index+1) * trader.grid_width < current_buy_price / xch_price and trader.volume > 0 and current_buy_price / xch_price > trader.avg_price:
                    trader.sell_stock(xch_price, current_buy_price)
            else:
                if trader.position_status == PositionStatus.PENDING_BUY.name:
                    stocks_stats[trader.ticker]["buying"] += 1
                if trader.position_status == PositionStatus.PENDING_SELL.name:
                    stocks_stats[trader.ticker]["selling"] += 1
            stock_balance += trader.volume * trader.current_price
            update_position(trader)

        # Get XCH balance
        xch_balance = get_xch_balance()
        xch_price = get_xch_price(logger)
        if xch_price is None:
            logger.error("Failed to get XCH price, skipping...")
            continue
        total_profit = 0
        for s, stats in stocks_stats.items():
            logger.info(f"Stock: {s}, Buying: {stats['buying']}, Selling: {stats['selling']}, Position Grids: {stats['position']}, Total Volume: {stats['volume']}, Finished Arbitrages: {stats['arbitrage']}, Total Profit: {stats['profit']} XCH,"
                        f" Balance: {stats['value']/xch_price+(1-(stats['position']+stats['buying'])/stats['grid'])*stats['invest']+stats['profit']} XCH")
            total_profit += stats['profit']
        total_xch = xch_balance + stock_balance / xch_price
        logger.info(
            f"Total Stock Balance: {stock_balance} USD, Unused XCH Balance: {xch_balance} XCH, XCH In Total: {total_xch} XCH, profit in XCH: {total_profit} XCH")
        if is_market_open(logger):
            time.sleep(60)  # Wait a minute before checking again
        else:
            time.sleep(300)
