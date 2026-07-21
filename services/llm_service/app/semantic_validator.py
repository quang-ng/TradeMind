from dataclasses import dataclass

from common.enums import Action

from .schemas import AnalyzeRequest, LLMOutput


@dataclass(frozen=True)
class SemanticValidationResult:
    output: LLMOutput
    action_changed: bool
    exit_confirmations: tuple[str, ...]


def validate_signal_semantics(
    request: AnalyzeRequest, output: LLMOutput, *, min_exit_profit_pct: float = 0.005
) -> SemanticValidationResult:
    """Enforce the position-aware exit rubric after schema validation.

    The local model remains responsible for producing a valid response, so a
    provider failure still fails closed to HOLD before this function runs.
    Once a response exists, however, the documented bearish confirmations are
    deterministic facts. Enforcing them here prevents a small model from
    returning HOLD while its own supplied evidence satisfies the SELL rubric,
    or from proposing an action that is impossible in the current position
    state.

    The rubric only ever forces a profit-taking exit, never a loss-cutting
    one: `unrealized_pnl_pct` must clear `min_exit_profit_pct`, or the
    confirmations are ignored and the position is left to HOLD. A bare
    `> 0` isn't enough — analyze latency plus forceexit execution slippage
    can turn a marginally positive reading at decision time into a net loss
    by fill time once round-trip fees are counted, so the threshold must
    leave a cushion above that, not just above breakeven.

    The confirmation bar is two independent signals spanning two of the
    three categories in `_CONFIRMATION_CATEGORIES` (trend, momentum, price
    action), not merely two confirmations from the same category — trend and
    momentum confirmations each come in highly-correlated pairs (e.g. price
    below both EMAs and EMA50 below EMA200 tend to move together), so a
    same-category pair is materially weaker evidence than a cross-category
    one.
    """
    has_open_position = request.position_context.has_open_position
    is_profitable = (
        request.position_context.unrealized_pnl_pct is not None
        and request.position_context.unrealized_pnl_pct > min_exit_profit_pct
    )
    confirmations = _bearish_exit_confirmations(request) if has_open_position else ()
    confirmed_categories = {_CONFIRMATION_CATEGORIES[c] for c in confirmations}
    rubric_satisfied = (
        has_open_position
        and is_profitable
        and len(confirmations) >= 2
        and len(confirmed_categories) >= 2
    )

    if rubric_satisfied:
        normalized = output.model_copy(
            update={
                "action": Action.SELL,
                "confidence": min(0.80, 0.65 + 0.05 * (len(confirmations) - 2)),
                "reasoning": (
                    f"Deterministic exit rubric found {len(confirmations)} independent "
                    f"bearish confirmations while the position was profitable "
                    f"({request.position_context.unrealized_pnl_pct:.2%}): "
                    f"{', '.join(confirmations)}."
                ),
                "key_indicators": list(confirmations),
                "invalidation_condition": (
                    "Fewer than two bearish confirmations spanning two different "
                    "categories (trend, momentum, price action) on a closed candle, "
                    "or the position is no longer profitable."
                ),
            }
        )
        return SemanticValidationResult(
            output=normalized,
            action_changed=output.action != Action.SELL,
            exit_confirmations=confirmations,
        )

    action_is_valid = output.action == Action.HOLD or (
        output.action == Action.BUY and not has_open_position
    )
    if action_is_valid:
        return SemanticValidationResult(
            output=output,
            action_changed=False,
            exit_confirmations=confirmations,
        )

    if not has_open_position:
        reason = "SELL suppressed because there is no open position."
    elif not is_profitable:
        reason = (
            "Trade action suppressed because the position does not clear the minimum "
            f"exit profit margin ({min_exit_profit_pct:.2%}) — the deterministic exit "
            "rubric only locks in gains net of expected slippage/fees, it does not cut losses."
        )
    elif len(confirmations) < 2:
        reason = (
            "Trade action suppressed because only "
            f"{len(confirmations)} independent bearish exit confirmation(s) were present."
        )
    else:
        reason = (
            "Trade action suppressed because the bearish exit confirmations present "
            f"({', '.join(confirmations)}) all fall within the same signal category — "
            "at least two categories (trend, momentum, price action) must agree."
        )
    normalized = output.model_copy(
        update={
            "action": Action.HOLD,
            "confidence": min(output.confidence, 0.64),
            "reasoning": reason,
            "key_indicators": list(confirmations),
            "invalidation_condition": (
                "At least two bearish confirmations spanning two different categories "
                "(trend, momentum, price action) while the position is open and "
                "profitable."
            ),
        }
    )
    return SemanticValidationResult(
        output=normalized,
        action_changed=True,
        exit_confirmations=confirmations,
    )


# Groups mirror the prompt's own framing (v1.py rule 3): trend and momentum
# confirmations each move in highly-correlated pairs, so requiring two
# categories (not just two confirmations) demands genuinely independent
# evidence rather than one relationship flipping twice.
_CONFIRMATION_CATEGORIES = {
    "price_below_ema50_and_ema200": "trend",
    "ema50_below_ema200": "trend",
    "bearish_macd": "momentum",
    "rsi_below_45": "momentum",
    "lower_highs_and_lows": "price_action",
    "falling_price_on_high_volume": "price_action",
}


def _bearish_exit_confirmations(request: AnalyzeRequest) -> tuple[str, ...]:
    candles = request.ohlcv
    if not candles:
        return ()

    latest = candles[-1]
    indicators = request.indicators
    confirmations: list[str] = []

    if latest.c < indicators.ema_50 and latest.c < indicators.ema_200:
        confirmations.append("price_below_ema50_and_ema200")
    if indicators.ema_50 < indicators.ema_200:
        confirmations.append("ema50_below_ema200")
    if (
        indicators.macd.histogram < 0
        and indicators.macd.macd < indicators.macd.signal
    ):
        confirmations.append("bearish_macd")
    if indicators.rsi_14 < 45:
        confirmations.append("rsi_below_45")
    if len(candles) >= 3:
        recent = candles[-3:]
        lower_highs = all(left.h > right.h for left, right in zip(recent, recent[1:]))
        lower_lows = all(left.l > right.l for left, right in zip(recent, recent[1:]))
        if lower_highs and lower_lows:
            confirmations.append("lower_highs_and_lows")
    if (
        len(candles) >= 2
        and latest.c < candles[-2].c
        and latest.v > indicators.volume_sma_20
    ):
        confirmations.append("falling_price_on_high_volume")

    return tuple(confirmations)
