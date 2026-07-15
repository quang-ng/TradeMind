from dataclasses import dataclass
from decimal import Decimal

from common.config import RiskConfig


def _clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(value, high))


@dataclass(frozen=True)
class SizingResult:
    position_size_usdt: Decimal
    position_size_base: Decimal
    stop_loss_price: Decimal
    stop_distance_pct: Decimal
    risk_pct_applied: Decimal


def compute_sizing(
    *,
    equity_usdt: Decimal,
    free_balance_usdt: Decimal,
    entry_price: Decimal,
    atr_14: Decimal,
    config: RiskConfig,
) -> SizingResult:
    """PROJECT.md Section 9.2 — fixed-fractional risk sizing using ATR for
    stop distance, long-only (MVP). All arithmetic is `Decimal`, never
    `float` (PROJECT.md Section 14 rule 10). `stop_loss_price` is always
    computed and attached — rule 12 (Section 9.1) is an invariant of this
    function, not a separate rejection check."""
    risk_amount_usdt = equity_usdt * config.risk_per_trade_pct
    stop_distance_pct = _clamp(
        (atr_14 / entry_price) * config.atr_stop_multiplier,
        config.min_stop_loss_pct,
        config.max_stop_loss_pct,
    )
    raw_size_usdt = risk_amount_usdt / stop_distance_pct

    position_size_usdt = min(
        raw_size_usdt,
        equity_usdt * config.max_position_pct,
        free_balance_usdt,
    )
    position_size_usdt = max(position_size_usdt, Decimal("0"))
    position_size_base = position_size_usdt / entry_price if entry_price > 0 else Decimal("0")
    stop_loss_price = entry_price * (Decimal("1") - stop_distance_pct)

    return SizingResult(
        position_size_usdt=position_size_usdt,
        position_size_base=position_size_base,
        stop_loss_price=stop_loss_price,
        stop_distance_pct=stop_distance_pct,
        risk_pct_applied=config.risk_per_trade_pct,
    )
