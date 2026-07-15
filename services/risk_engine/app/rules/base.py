from dataclasses import dataclass
from typing import Callable

from common.enums import RejectionReason

from ..schemas import RuleContext


@dataclass(frozen=True)
class RuleViolation:
    reason: RejectionReason
    auto_trip_killswitch: bool = False


RuleFunc = Callable[[RuleContext], "RuleViolation | None"]
