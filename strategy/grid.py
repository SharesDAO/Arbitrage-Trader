import math
import time
from datetime import datetime

from stock_trader import StockTrader
from util.crypto import get_crypto_price, send_asset, get_crypto_balance, add_token, check_pending_positions, get_token_balance
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
        self.invested_crypto = float(stock["INVEST_CRYPTO"])
        self.ticker = stock["TICKER"]
        self.index = index
        self.grid_width = ((self.max_price / CONFIG["CRYPTO_MIN"] - self.min_price / CONFIG[
            "CRYPTO_MAX"]) / self.grid_num)
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

    def buy_stock(self, crypto_volume, crypto_price, stock_price):
        buy_price = self.max_price / CONFIG["CRYPTO_MIN"] - (self.index+1) * self.grid_width
        while buy_price - self.grid_width > stock_price / crypto_price:
            buy_price -= self.grid_width
        volume = crypto_volume / buy_price
        timestamp = datetime.now()
        if not send_asset(STOCKS[self.ticker]["buy_addr"], 1, volume, crypto_volume, self.logger, self.stock):
            # Failed to send order
            return
        self.volume = volume
        self.last_buy_price = buy_price
        self.total_cost = crypto_volume
        self.avg_price = self.total_cost / self.volume
        self.current_price = stock_price
        self.position_status = PositionStatus.PENDING_BUY.name
        self.last_updated = timestamp
        record_trade(self.stock, "BUY", buy_price * crypto_price, volume, crypto_volume, 0)
        self.logger.info(f"Buying {volume} shares of {self.stock} at {buy_price} {CONFIG['CURRENCY']}")

    def sell_stock(self, crypto_price, stock_price, liquid=False):
        self.current_price = stock_price
        sell_price = self.max_price / CONFIG["CRYPTO_MIN"] - self.index * self.grid_width
        while sell_price + self.grid_width < stock_price / crypto_price:
            sell_price += self.grid_width
        if liquid:
            sell_price = stock_price / crypto_price
        request_crypto = self.volume * sell_price
        timestamp = datetime.now()
        if not send_asset(STOCKS[self.ticker]["sell_addr"], self.wallet_id, request_crypto,
                          self.volume, self.logger, self.stock, "MARKET" if liquid else "LIMIT"):
            # Failed to send order
            return
        record_trade(self.stock, "SELL", sell_price * crypto_price, self.volume, self.total_cost, request_crypto - self.total_cost)
        self.logger.info(
            f"Selling {self.volume} shares of {self.stock} at {sell_price} {CONFIG['CURRENCY']} with {request_crypto - self.total_cost} {CONFIG['CURRENCY']} profit")
        self.position_status = PositionStatus.PENDING_SELL.name
        self.last_updated = timestamp

    def adjust_volume(self, total_volume):
        # Get current stock balance
        balance = get_token_balance()
        if balance is None or self.ticker not in balance:
            self.logger.error(f"Failed to get balance for {self.stock}, skipping...")
            return
        if self.position_status == PositionStatus.TRADABLE.name and total_volume > 0:
            self.volume = math.floor(self.volume / total_volume * balance[self.ticker] * 1000) / 1000
            self.logger.info(f"Adjusting volume for {self.stock} to {self.volume}")


def execute_grid(logger):
    traders = []
    for stock in CONFIG["TRADING_SYMBOLS"]:
        # Update invest key based on blockchain
        if CONFIG["BLOCKCHAIN"] == "CHIA":
            invest_key = "INVEST_XCH"
        else:  # SOLANA
            invest_key = "INVEST_SOL"
            
        if invest_key in stock:
            stock["INVEST_CRYPTO"] = stock[invest_key]
        else:
            stock["INVEST_CRYPTO"] = 0
            
        # Create grids for each stock
        total_volume = 0
        grid_traders = []
        for i in range(stock["GRID_NUM"]):
            trader = GridStockTrader(i, stock, logger)
            if trader.position_status == PositionStatus.TRADABLE.name:
                total_volume += trader.volume
            grid_traders.append(trader)
        traders.extend(grid_traders)
        balance = get_token_balance()
        if balance is None or stock["TICKER"] not in balance:
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
            logger.error(f"Failed to check pending positions, please check your {CONFIG['CURRENCY']} wallet. {e}")
        for trader in traders:
            if trader.ticker not in stocks_stats:
                stocks_stats[trader.ticker] = {"buying": 0, "selling": 0, "position": 0, "volume": 0, "arbitrage": 0, "profit": 0, "cost": 0, "value": 0, "grid": trader.grid_num, "invest": trader.invested_crypto}
            current_buy_price, current_sell_price = get_stock_price(trader.ticker, logger)
            if current_buy_price == 0:
                logger.error(f"Failed to get stock price for {trader.ticker}, skipping...")
                continue
            crypto_price = get_crypto_price(logger)
            if crypto_price is None:
                logger.error(f"Failed to get {CONFIG['CURRENCY']} price, skipping...")
                continue
            if trader.volume > 0 and trader.position_status != PositionStatus.PENDING_BUY.name:
                stocks_stats[trader.ticker]["position"] += 1
            stocks_stats[trader.ticker]["arbitrage"] += trader.buy_count
            stocks_stats[trader.ticker]["profit"] += trader.profit
            stocks_stats[trader.ticker]["volume"] += trader.volume
            stocks_stats[trader.ticker]["value"] += trader.volume * (current_sell_price+current_buy_price) /2
            stocks_stats[trader.ticker]["cost"] += trader.total_cost
            if trader.position_status == PositionStatus.TRADABLE.name and is_market_open(logger):
                if trader.max_price / CONFIG["CRYPTO_MIN"] - trader.index * trader.grid_width >= current_sell_price / crypto_price and trader.volume == 0:
                    trader.buy_stock(trader.invested_crypto / trader.grid_num, crypto_price, current_sell_price)
                elif trader.max_price / CONFIG["CRYPTO_MIN"] - (trader.index+1) * trader.grid_width < current_buy_price / crypto_price and trader.volume > 0 and current_buy_price / crypto_price > trader.avg_price:
                    trader.sell_stock(crypto_price, current_buy_price)
            else:
                if trader.position_status == PositionStatus.PENDING_BUY.name:
                    stocks_stats[trader.ticker]["buying"] += 1
                if trader.position_status == PositionStatus.PENDING_SELL.name:
                    stocks_stats[trader.ticker]["selling"] += 1
            stock_balance += trader.volume * trader.current_price
            update_position(trader)

        # Get crypto balance
        crypto_balance = get_crypto_balance()
        crypto_price = get_crypto_price(logger)
        if crypto_price is None:
            logger.error(f"Failed to get {CONFIG['CURRENCY']} price, skipping...")
            continue
        total_profit = 0
        for s, stats in stocks_stats.items():
            logger.info(f"Stock: {s}, Buying: {stats['buying']}, Selling: {stats['selling']}, Position Grids: {stats['position']}, Total Volume: {stats['volume']}, Finished Arbitrages: {stats['arbitrage']}, Total Profit: {stats['profit']} {CONFIG['CURRENCY']},"
                        f" Balance: {stats['value']/crypto_price+(1-(stats['position']+stats['buying'])/stats['grid'])*stats['invest']+stats['profit']} {CONFIG['CURRENCY']}")
            total_profit += stats['profit']
        total_crypto = crypto_balance + stock_balance / crypto_price
        logger.info(
            f"Total Stock Balance: {stock_balance} USD, Unused {CONFIG['CURRENCY']} Balance: {crypto_balance} {CONFIG['CURRENCY']}, {CONFIG['CURRENCY']} In Total: {total_crypto} {CONFIG['CURRENCY']}, profit in {CONFIG['CURRENCY']}: {total_profit} {CONFIG['CURRENCY']}")
        if is_market_open(logger):
            time.sleep(60)  # Wait a minute before checking again
        else:
            time.sleep(300)
