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
    # Positive-expectancy plan D1: `nominal_risk_amount_usdt` is the
    # pre-clamp account-level budget (`equity_usdt * risk_per_trade_pct`);
    # `actual_risk_usdt` is what is truly at stake on *this* trade once
    # max_position_pct/free-balance/confidence clamps are applied
    # (`position_size_usdt * stop_distance_pct`) — always <= the nominal
    # figure, and the one used as the R-multiple denominator everywhere
    # expectancy math happens (never the nominal budget).
    nominal_risk_amount_usdt: Decimal
    actual_risk_usdt: Decimal


def _confidence_scale(confidence: Decimal, config: RiskConfig) -> Decimal:
    """Linearly scales from `min_confidence_size_scale` at `min_confidence`
    up to `1` at confidence `1.0`, so a signal that barely clears the entry
    bar gets less capital than one the model is fully convinced by — added
    2026-08-04 (mục 3): sizing previously ignored confidence entirely once a
    signal passed `min_confidence`, treating a 0.70 and a 0.95 identically."""
    confidence_range = Decimal("1") - config.min_confidence
    if confidence_range <= 0:
        return Decimal("1")
    ratio = _clamp((confidence - config.min_confidence) / confidence_range, Decimal("0"), Decimal("1"))
    return config.min_confidence_size_scale + (Decimal("1") - config.min_confidence_size_scale) * ratio


def compute_sizing(
    *,
    equity_usdt: Decimal,
    free_balance_usdt: Decimal,
    entry_price: Decimal,
    atr_14: Decimal,
    confidence: Decimal,
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
    position_size_usdt = max(position_size_usdt, Decimal("0")) * _confidence_scale(confidence, config)
    position_size_base = position_size_usdt / entry_price if entry_price > 0 else Decimal("0")
    stop_loss_price = entry_price * (Decimal("1") - stop_distance_pct)
    actual_risk_usdt = position_size_usdt * stop_distance_pct

    return SizingResult(
        position_size_usdt=position_size_usdt,
        position_size_base=position_size_base,
        stop_loss_price=stop_loss_price,
        stop_distance_pct=stop_distance_pct,
        risk_pct_applied=config.risk_per_trade_pct,
        nominal_risk_amount_usdt=risk_amount_usdt,
        actual_risk_usdt=actual_risk_usdt,
    )
