from enum import Enum


class PositionStatus(Enum):
    TRADABLE = 1
    PENDING_BUY = 2
    PENDING_SELL = 3
    PENDING_CANCEL = 4
    PENDING_LIQUIDATION = 5


class StrategyType(Enum):
    DCA = "DCA"
    GRID = "GRID"


class BlockchainType(Enum):
    CHIA = "CHIA"
    SOLANA = "SOLANA"


REQUEST_TIMEOUT = 60
CONFIG = {}
CONFIG["MAX_ORDER_TIME_OFFSET"] = 120
CONFIG["RESERVE_RATIO"] = 0.1
# When you local system time is different from the server time, the offset between them. Don't change this unless you know what you are doing.
