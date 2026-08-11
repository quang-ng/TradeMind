from datetime import datetime

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy, stoploss_from_absolute
from pandas import DataFrame


class ExternalSignalStrategy(IStrategy):
    """PROJECT.md Section 6 / Section 14 rule 8: this strategy has no
    autonomous entry or exit logic. Entries occur only via authenticated
    `forceenter` calls from `risk_engine`; SELL signals that resolve to an
    exit also come from `risk_engine`, via `forceexit`. This class exists
    to satisfy Freqtrade's `IStrategy` contract and to provide the safety
    nets described in PROJECT.md Section 9.2.

    `stoploss` is the conservative strategy-wide upper bound
    (`RiskConfig.max_stop_loss_pct`) and stays the fallback. The tighter,
    per-trade ATR-based `stop_loss_price` the Risk Engine computes
    (PROJECT.md Section 9.2) is passed at entry via `forceenter`'s
    `entry_tag` (`slpct:<distance>`, see `risk_engine/app/main.py`) and applied
    per-trade by `custom_stoploss()` below, which falls back to the static
    `stoploss` if the tag is missing or malformed — it must never fail
    open to "no stop."
    """

    INTERFACE_VERSION = 3

    timeframe = "1h"

    # Take-profit safety net (PROJECT.md Section 9.2) — a simple, auditable
    # ROI decay table, not a per-trade computed value. Sized as a rare
    # backstop, not the primary exit: 1h-candle ATR on these pairs runs
    # ~0.4-0.75% of price, so tiers require several times a pair's expected
    # cumulative drift over that window before firing, leaving room for the
    # position-aware SELL rubric (services/llm_service/app/semantic_validator.py)
    # to catch real reversals instead of ROI closing every trade on noise.
    # The floor never drops below `min_exit_profit_pct` in that module.
    #
    # 2026-08-10: the old table (24h+ tier floor 1%) was found to be firing
    # as the *primary* exit, not a rare backstop — the position-aware rubric
    # needs two cross-category bearish confirmations, which a slow grinding
    # market rarely produces, so positions sat in HOLD until ROI's decaying
    # floor caught whatever profit existed. Pushed the decay tail further
    # out in time (each tier now roughly halves, out to 96h) so a winner
    # gets more time for either the trend to keep running or the rubric to
    # find a real reversal, before the timer settles for a small win.
    minimal_roi = {
        "0": 0.06,
        "240": 0.03,
        "720": 0.02,
        "1440": 0.015,
        "2880": 0.01,
        "5760": 0.005,
    }
    # Conservative static floor — see class docstring. Also the fallback
    # used by custom_stoploss() below whenever the per-trade tag is absent
    # or unparseable.
    stoploss = -0.08
    use_custom_stoploss = True

    # 2026-08-11: trailing-stop tuning for custom_stoploss() below. The
    # per-trade ATR stop is a fixed distance from entry and never moves,
    # so a winner that ran up and gave most of it back before the ROI
    # table or the rubric caught it kept none of the extra gain. Once a
    # trade clears TRAILING_ACTIVATION_PCT, custom_stoploss also trails
    # TRAILING_DISTANCE_PCT behind the trade's peak price (trade.max_rate),
    # so a run past that point locks in progressively more as it climbs
    # instead of only being bounded by the entry-anchored ATR stop or the
    # ROI decay table's current time tier. ~1-1.5x a typical 1h-candle ATR
    # (0.4-0.75% on these pairs, per the minimal_roi comment above) is
    # tight enough to hold most of a real reversal's giveback without
    # closing out on ordinary candle noise. Both are starting guesses, not
    # backtested values — worth validating with
    # scripts/backtest/mechanical_replay.py before leaning on them hard.
    trailing_activation_pct = 0.01
    trailing_distance_pct = 0.0075

    process_only_new_candles = True
    use_exit_signal = False
    can_short = False

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = 0
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        return dataframe

    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> float | None:
        """Apply the Risk Engine's per-trade ATR stop, then trail it behind
        the trade's peak price once comfortably in profit.

        New entries carry `slpct:<distance>` so the absolute stop is derived
        from the authoritative filled `Trade.open_rate`, not the earlier
        signal price. Legacy `sl:<absolute price>` tags remain supported for
        already-open trades. Missing or malformed tags fail closed to the
        static strategy stop, untouched by the trailing step below — that
        safety net must never fail open to "no stop."

        Once the trade's peak profit (from `trade.max_rate`, not the
        possibly-since-pulled-back `current_profit`) clears
        `trailing_activation_pct`, also compute a stop
        `trailing_distance_pct` behind that peak and use whichever of it
        and the ATR stop is tighter. Gating on peak profit rather than
        current profit matters: this is checked fresh on every candle, and
        a pullback is exactly when current_profit can drop back under the
        activation bar even though the trade did reach it — using
        current_profit here would silently drop the trailing protection at
        the moment it's most needed. Freqtrade never loosens a trade's
        stored stoploss (`Trade.adjust_stop_loss`), so returning the looser
        candidate is always a safe no-op.
        """
        tag = trade.enter_tag or ""
        try:
            if tag.startswith("slpct:"):
                stop_distance_pct = float(tag[len("slpct:") :])
                if not 0 < stop_distance_pct <= abs(self.stoploss):
                    return self.stoploss
                stop_rate = trade.open_rate * (1 - stop_distance_pct)
            elif tag.startswith("sl:"):
                stop_rate = float(tag[len("sl:") :])
            else:
                return self.stoploss
        except (TypeError, ValueError):
            return self.stoploss

        peak_profit = (trade.max_rate - trade.open_rate) / trade.open_rate
        if peak_profit >= self.trailing_activation_pct:
            trailing_rate = trade.max_rate * (1 - self.trailing_distance_pct)
            stop_rate = max(stop_rate, trailing_rate)

        return stoploss_from_absolute(stop_rate=stop_rate, current_rate=current_rate)
