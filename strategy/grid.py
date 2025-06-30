import time
from datetime import datetime

from stock_trader import StockTrader
from util.crypto import get_crypto_price, get_crypto_balance, add_token, check_pending_positions, trade, get_token_balance
from constants.constant import PositionStatus, CONFIG, StrategyType

from util.db import get_position, update_position, create_position, record_trade
from util.sharesdao import get_fund_value, check_cash_reserve
from util.stock import is_market_open, get_stock_price, STOCKS


#For Grid trading, buy_count = arbitrage times, profit = agg gain
class GridStockTrader(StockTrader):
    def __init__(self, index, stock, logger):
        self.type = StrategyType.GRID
        self.grid_num = int(stock["GRID_NUM"])
        self.max_price = float(stock["MAX_PRICE"])
        self.min_price = float(stock["MIN_PRICE"])
        self.ticker = stock["TICKER"]
        self.index = index
        if CONFIG["BLOCKCHAIN"] == "CHIA":
            self.invested_crypto = float(stock["INVEST_XCH"])
        elif CONFIG["BLOCKCHAIN"] == "SOLANA":
            self.invested_crypto = float(stock["INVEST_SOL"])
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
        #while buy_price - self.grid_width > stock_price / crypto_price:
        #    buy_price -= self.grid_width
        volume = crypto_volume / buy_price
        timestamp = datetime.now()
        if not trade(self.ticker, "BUY", volume, crypto_volume, self.logger, self.stock):
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
        #while sell_price + self.grid_width < stock_price / crypto_price:
        #    sell_price += self.grid_width
        if liquid:
            sell_price = stock_price / crypto_price
        request_crypto = self.volume * sell_price
        timestamp = datetime.now()
        if not trade(self.ticker, "SELL", request_crypto,
                          self.volume, self.logger, self.stock, "MARKET" if liquid else "LIMIT"):
            # Failed to send order
            return
        record_trade(self.stock, "SELL", sell_price * crypto_price, self.volume, self.total_cost, request_crypto - self.total_cost)
        self.logger.info(
            f"Selling {self.volume} shares of {self.stock} at {sell_price} {CONFIG['CURRENCY']} with {request_crypto - self.total_cost} {CONFIG['CURRENCY']} profit")
        self.position_status = PositionStatus.PENDING_SELL.name if not liquid else PositionStatus.PENDING_LIQUIDATION.name
        self.last_updated = timestamp


def execute_grid(logger):
    traders = []
    for stock in CONFIG["TRADING_SYMBOLS"]:
        # Create grids for each stock
        for i in range(stock["GRID_NUM"]):
            trader = GridStockTrader(i, stock, logger)
            traders.append(trader)
    fund_crypto = 0
    while True:
        stocks_stats = {}
        stock_balance = 0

        # Check if the positions are still pending
        try:
            check_pending_positions(traders, logger)
        except Exception as e:
            logger.exception(f"Failed to check pending positions, please check your {CONFIG['BLOCKCHAIN']} wallet: {e}")
            time.sleep(60)
            continue
        if fund_crypto == 0:
            fund_crypto = get_fund_value(logger) / get_crypto_price(logger)
        logger.info(f"Fund value: {fund_crypto} {CONFIG['CURRENCY']}")
        if fund_crypto == 0:
            logger.error("Failed to get fund value, skipping...")
            continue
        crypto_balance = get_crypto_balance()
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
            if trader.position_status == PositionStatus.TRADABLE.name:
                stocks_stats[trader.ticker]["volume"] += trader.volume
            stocks_stats[trader.ticker]["value"] += trader.volume * (current_sell_price+current_buy_price) /2
            stocks_stats[trader.ticker]["cost"] += trader.total_cost
            if trader.position_status == PositionStatus.TRADABLE.name:
                try:
                    if trader.max_price / CONFIG["CRYPTO_MIN"] - trader.index * trader.grid_width >= current_sell_price / crypto_price and trader.volume == 0 and check_cash_reserve(traders, fund_crypto, True, logger):
                        trader.buy_stock(fund_crypto * trader.invested_crypto * (1 - CONFIG["RESERVE_RATIO"]) / trader.grid_num, crypto_price, current_sell_price)
                        time.sleep(3)
                    elif trader.max_price / CONFIG["CRYPTO_MIN"] - (trader.index+1) * trader.grid_width < current_buy_price / crypto_price and trader.volume > 0 and current_buy_price / crypto_price > trader.avg_price:
                        trader.sell_stock(crypto_price, current_buy_price)
                        time.sleep(3)
                except Exception as e:
                    logger.error(f"Failed to trade {trader.stock}: {e}")
            else:
                if trader.position_status == PositionStatus.PENDING_BUY.name:
                    stocks_stats[trader.ticker]["buying"] += 1
                if trader.position_status == PositionStatus.PENDING_SELL.name:
                    stocks_stats[trader.ticker]["selling"] += 1
            stock_balance += trader.volume * trader.current_price
            update_position(trader)
        # Check if reserve is enough
        try:
            if not check_cash_reserve(traders, fund_crypto, False, logger):
                logger.info("Reserve is not enough, selling stocks ...")
                # Sell the last buy
                last_trader = None
                last_buy_date = None
                for trader in traders:
                    if trader.position_status == PositionStatus.TRADABLE.name and trader.volume > 0 and (last_trader is None or trader.last_updated > last_buy_date):
                        last_trader = trader
                        last_buy_date = trader.last_updated
                if last_trader is not None:
                    logger.info(f"Selling {last_trader.volume} shares of {last_trader.stock} to cover sell order.")
                    last_trader.sell_stock(get_crypto_price(logger), get_stock_price(last_trader.ticker, logger)[0], True)
        except Exception as e:
            logger.error(f"Failed to check reserve: {e}")
        # Get crypto balance
        crypto_balance = get_crypto_balance()
        if crypto_balance is None:
            logger.error(f"Failed to get {CONFIG['CURRENCY']} balance, skipping...")
            continue
        crypto_price = get_crypto_price(logger)
        if crypto_price is None:
            logger.error(f"Failed to get {CONFIG['CURRENCY']} price, skipping...")
            continue
        total_profit = 0
        token_balance = get_token_balance()
        if token_balance is None:
            logger.error("Failed to get token balance, skipping...")
            time.sleep(60)
            continue
        for s, stats in stocks_stats.items():
            logger.info(f"Stock: {s}, Buying: {stats['buying']}, Selling: {stats['selling']}, Position Grids: {stats['position']}, Expect/Actual Volume: {stats['volume']}/{0 if STOCKS[s]['asset_id'] not in token_balance else token_balance[STOCKS[s]['asset_id']]['balance']}, Finished Arbitrages: {stats['arbitrage']}, Total Profit: {stats['profit']} {CONFIG['CURRENCY']},"
                        f" Balance: {stats['value']/crypto_price+(1-(stats['position']+stats['buying'])/stats['grid'])*stats['invest']+stats['profit']} {CONFIG['CURRENCY']}")
            total_profit += stats['profit']
        total_crypto = crypto_balance + stock_balance / crypto_price
        logger.info(
            f"Total Stock Balance: {stock_balance} USD, Unused {CONFIG['CURRENCY']} Balance: {crypto_balance} {CONFIG['CURRENCY']}, {CONFIG['CURRENCY']} In Total: {total_crypto} {CONFIG['CURRENCY']}, profit in {CONFIG['CURRENCY']}: {total_profit} {CONFIG['CURRENCY']}")
        fund_crypto = total_crypto
        if is_market_open(logger):
            time.sleep(60)  # Wait a minute before checking again
        else:
            time.sleep(300)
