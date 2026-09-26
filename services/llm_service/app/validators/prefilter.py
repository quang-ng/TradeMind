from ..models.market import MarketContext
from ..models.strategy import StrategyName
from ..strategies.selector import StrategySelector
from .semantic import llm_can_change_outcome


class LLMCallPrefilter:
    """Skips the LLM call when the deterministic rubric in `semantic.py`
    would normalize every possible model answer to HOLD anyway.

    Pure cost control (2026-09-26): replaying 6,334 live signals showed
    ~81% of `/analyze` calls could not have produced a trade, yet each was
    a paid Anthropic request. The decision logic itself is unchanged — see
    `llm_can_change_outcome` for the equivalence argument; this class only
    holds the same thresholds `ResponseValidator` uses and phrases the
    deterministic HOLD reason that replaces the model's.
    """

    def __init__(
        self,
        *,
        min_exit_profit_pct: float = 0.005,
        min_exit_loss_pct: float = 0.005,
        hard_loss_cut_pct: float = 0.015,
    ):
        self._min_exit_profit_pct = min_exit_profit_pct
        self._min_exit_loss_pct = min_exit_loss_pct
        self._hard_loss_cut_pct = hard_loss_cut_pct

    def should_skip_llm(self, context: MarketContext) -> bool:
        return not llm_can_change_outcome(
            context,
            min_exit_profit_pct=self._min_exit_profit_pct,
            min_exit_loss_pct=self._min_exit_loss_pct,
            hard_loss_cut_pct=self._hard_loss_cut_pct,
        )

    def hold_reason(self, context: MarketContext) -> str:
        position = context.position
        if not position.has_open_position:
            confirmations = context.entry_confirmations
            if StrategySelector().select(context).strategy == StrategyName.TREND_FOLLOWING:
                why = "the setup is trend_following, where BUY is always suppressed"
            else:
                why = "entry needs at least three including both trend and momentum"
            return (
                "Pre-filter HOLD without an LLM call: no open position and "
                f"{len(confirmations)} entry confirmation(s) "
                f"({', '.join(confirmations) or 'none'}); {why}."
            )

        confirmations = context.exit_confirmations
        pnl = position.unrealized_pnl_pct
        pnl_text = "unknown" if pnl is None else f"{pnl:.2%}"
        return (
            "Pre-filter HOLD without an LLM call: open position with pnl "
            f"{pnl_text} and {len(confirmations)} exit confirmation(s) "
            f"({', '.join(confirmations) or 'none'}); exit needs a known pnl "
            "beyond the cushion and two confirmations from different categories."
        )
