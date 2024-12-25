from enum import Enum


class PositionStatus(Enum):
    TRADABLE = 1
    PENDING_BUY = 2
    PENDING_SELL = 3
    PENDING_CANCEL = 4


# For each buy how many XCH you want to spend
BUY_VOLUME = 5
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
MAX_LOSS_PERCENTAGE = 0.40
# How much XCH you invested
INVESTED_XCH = 140
# How much equivalent USD you invested
INVESTED_USD = 2720
# Symbols you want to trade
TRADING_SYMBOLS = ["AAPL", "AMZN", "GOOGL", "MSFT", "NVDA", "TSLA", "META", "PYPL", "RDDT", "COIN", "GBTC", "AMD",
                   "MCD", ]
MAX_ORDER_TIME_OFFSET = 120

