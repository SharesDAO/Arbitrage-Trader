from enum import Enum


class PositionStatus(Enum):
    TRADABLE = 1
    PENDING_BUY = 2
    PENDING_SELL = 3
    PENDING_CANCEL = 4


CONFIG = {}
CONFIG["MAX_ORDER_TIME_OFFSET"] = 120
# When you local system time is different from the server time, the offset between them. Don't change this unless you know what you are doing.
