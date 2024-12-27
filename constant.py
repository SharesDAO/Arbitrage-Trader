from enum import Enum


class PositionStatus(Enum):
    TRADABLE = 1
    PENDING_BUY = 2
    PENDING_SELL = 3
    PENDING_CANCEL = 4


# For each buy how many percentage of XCH you want to spend, 0.1 means 10%
BUY_PERCENTAGE = 0.05
# Chia install folder
CHIA_PATH = "G:\\Program Files\\Chia\\resources\\app.asar.unpacked\\daemon\\chia.exe"
# Gas fee for  each transaction
CHIA_TX_FEE = 0
MAX_BUY_TIMES = 3  # Maximum number of repurchases for each stock
# Your DID HEX. You need to register it on the www.sharesdao.com before trading!
DID_HEX = 'a61489cbc7645829fc826606aba4ab5b09fdb2a69f40eb4b0bdae7a7dda7cf10'
# You Chia wallet fingerprint
WALLET_FINGERPRINT = 2701109320
# Sell all volume when profit is more than this
MIN_PROFIT = 0.01
# Repurchase the stock if the (last buy price - current price) / last buy price is less than this
DCA_PERCENTAGE = 0.05
# If the buy count = MAX_BUY_TIMES and the profit is less than this, liquid the stock
MAX_LOSS_PERCENTAGE = 0.25
# How much XCH you invested, required to set before your run
INVESTED_XCH = 140
# How much equivalent USD you invested
INVESTED_USD = 2720
# Tickers you want to trade
TRADING_SYMBOLS = ["AAPL", "AMZN", "GOOGL", "MSFT", "NVDA", "TSLA", "META", "PYPL", "RDDT", "AMD",
                   "MCD","COIN", "GBTC", "TQQQ", "MSTU", "ETHE", "USHY", "IAU", "ARKX","ARKK", "VXX"]
# Tickers you want to sell only
SELL_ONLY_SYMBOLS = {"AAPL", "AMZN", "GOOGL", "MSFT", "NVDA", "TSLA", "META", "PYPL", "RDDT", "AMD", "MCD", "COIN", "GBTC"}
# When you local system time is different from the server time, the offset between them. Don't change this unless you know what you are doing.
MAX_ORDER_TIME_OFFSET = 120

