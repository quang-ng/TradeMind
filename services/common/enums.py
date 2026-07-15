from enum import Enum


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class SignalStatus(str, Enum):
    PENDING = "PENDING"
    CONSUMED = "CONSUMED"
    EXPIRED = "EXPIRED"
