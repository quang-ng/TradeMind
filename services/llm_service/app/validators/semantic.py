from dataclasses import dataclass

from common.enums import Action

from ..models.llm import LLMOutput
from ..models.market import MarketContext


@dataclass(frozen=True)
class SemanticValidationResult:
    output: LLMOutput
    action_changed: bool
    exit_confirmations: tuple[str, ...]


def validate_signal_semantics(
    context: MarketContext,
    output: LLMOutput,
    *,
    min_exit_profit_pct: float = 0.005,
    min_exit_loss_pct: float = 0.005,
) -> SemanticValidationResult:
    """Enforce the position-aware exit rubric after structural validation.

    The local model remains responsible for producing a valid response, so a
    provider failure still fails closed to HOLD before this function runs.
    Once a response exists, however, the documented bearish confirmations
    (`context.exit_confirmations`, computed once by `ContextBuilder`) are
    deterministic facts. Enforcing them here prevents a small model from
    returning HOLD while its own supplied evidence satisfies the SELL
    rubric, or from proposing an action that is impossible in the current
    position state.

    The rubric is symmetric: it forces a profit-taking exit once
    `unrealized_pnl_pct` clears `min_exit_profit_pct`, and separately forces
    a loss-cutting exit once it falls below `-min_exit_loss_pct` — either
    way gated on the same cross-category confirmation bar. These two bands
    can never both hold at once, so at most one of them fires. A bare `> 0`
    isn't enough for the profit side — analyze latency plus forceexit
    execution slippage can turn a marginally positive reading at decision
    time into a net loss by fill time once round-trip fees are counted, so
    the threshold must leave a cushion above that, not just above breakeven.
    The loss side exists so a confirmed trend reversal gets cut before
    riding down to the wider ATR/static stop-loss (PROJECT.md Section 9.2) —
    it is deliberately not zero, so it doesn't fire on ordinary noise.

    The confirmation bar is two independent signals spanning two of the
    three categories in `_CONFIRMATION_CATEGORIES` (trend, momentum, price
    action), not merely two confirmations from the same category — trend and
    momentum confirmations each come in highly-correlated pairs (e.g. price
    below both EMAs and EMA50 below EMA200 tend to move together), so a
    same-category pair is materially weaker evidence than a cross-category
    one.
    """
    position = context.position
    has_open_position = position.has_open_position
    pnl = position.unrealized_pnl_pct
    is_profitable = pnl is not None and pnl > min_exit_profit_pct
    is_losing = pnl is not None and pnl < -min_exit_loss_pct
    confirmations = context.exit_confirmations if has_open_position else ()
    confirmed_categories = {_CONFIRMATION_CATEGORIES[c] for c in confirmations}
    confirmations_sufficient = len(confirmations) >= 2 and len(confirmed_categories) >= 2

    if has_open_position and confirmations_sufficient and (is_profitable or is_losing):
        if is_profitable:
            reasoning = (
                f"Deterministic exit rubric found {len(confirmations)} independent "
                f"bearish confirmations while the position was profitable "
                f"({pnl:.2%}): {', '.join(confirmations)}."
            )
            invalidation_condition = (
                "Fewer than two bearish confirmations spanning two different "
                "categories (trend, momentum, price action) on a closed candle, "
                "or the position is no longer profitable."
            )
        else:
            reasoning = (
                f"Deterministic loss-cut rubric found {len(confirmations)} independent "
                f"bearish confirmations while the position was losing ({pnl:.2%}): "
                f"{', '.join(confirmations)}. Cutting now rather than riding down to "
                "the wider ATR/static stop-loss."
            )
            invalidation_condition = (
                "Fewer than two bearish confirmations spanning two different "
                "categories (trend, momentum, price action) on a closed candle, "
                "or the loss no longer clears the loss-cut threshold."
            )
        normalized = output.model_copy(
            update={
                "action": Action.SELL,
                "confidence": min(0.80, 0.65 + 0.05 * (len(confirmations) - 2)),
                "reasoning": reasoning,
                "key_indicators": list(confirmations),
                "invalidation_condition": invalidation_condition,
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
    elif pnl is None:
        reason = (
            "Trade action suppressed because the position's unrealized PnL is "
            "unknown — the deterministic exit rubric needs a known PnL to decide "
            "between profit-taking and loss-cutting."
        )
    elif not is_profitable and not is_losing:
        reason = (
            "Trade action suppressed because the position's PnL "
            f"({pnl:.2%}) sits within the cushion between the loss-cut threshold "
            f"(-{min_exit_loss_pct:.2%}) and the minimum exit profit margin "
            f"({min_exit_profit_pct:.2%}) — too small a move for the deterministic "
            "rubric to act on in either direction."
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
                "(trend, momentum, price action) while the position is open and either "
                "profitable beyond the margin or losing beyond the loss-cut threshold."
            ),
        }
    )
    return SemanticValidationResult(
        output=normalized,
        action_changed=True,
        exit_confirmations=confirmations,
    )


# Groups mirror the prompt's own framing (prompts/v1.py rule 3): trend and
# momentum confirmations each move in highly-correlated pairs, so requiring
# two categories (not just two confirmations) demands genuinely independent
# evidence rather than one relationship flipping twice.
_CONFIRMATION_CATEGORIES = {
    "price_below_ema50_and_ema200": "trend",
    "ema50_below_ema200": "trend",
    "bearish_macd": "momentum",
    "rsi_below_45": "momentum",
    "lower_highs_and_lows": "price_action",
    "falling_price_on_high_volume": "price_action",
}
