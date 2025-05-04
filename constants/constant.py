from enum import Enum


class PositionStatus(Enum):
    TRADABLE = 1
    PENDING_BUY = 2
    PENDING_SELL = 3
    PENDING_CANCEL = 4

class OrderStatus(Enum):
    PENDING = 1
    FILLED = 2
    CANCELED = 3
    FAILED = 4
class StrategyType(Enum):
    DCA = "DCA"
    GRID = "GRID"

CURRENCY = {"SOLANA":"SOL", "CHIA":"XCH"}

REQUEST_TIMEOUT = 10
CONFIG = {}
CONFIG["MAX_ORDER_TIME_OFFSET"] = 120
# When you local system time is different from the server time, the offset between them. Don't change this unless you know what you are doing.
