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
    `entry_tag` (`sl:<price>`, see `risk_engine/app/main.py`) and applied
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
    minimal_roi = {
        "0": 0.06,
        "240": 0.025,
        "720": 0.015,
        "1440": 0.01,
    }
    # Conservative static floor — see class docstring. Also the fallback
    # used by custom_stoploss() below whenever the per-trade tag is absent
    # or unparseable.
    stoploss = -0.08
    use_custom_stoploss = True

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
        """Apply the Risk Engine's per-trade ATR stop, carried in via
        `forceenter`'s `entry_tag` as `sl:<absolute price>`
        (`risk_engine/app/main.py::_submit_entry_order`). Any missing or
        malformed tag falls back to the static `stoploss` — never fail
        open to no stop at all."""
        tag = trade.enter_tag or ""
        if not tag.startswith("sl:"):
            return self.stoploss
        try:
            stop_rate = float(tag[len("sl:") :])
        except ValueError:
            return self.stoploss
        return stoploss_from_absolute(stop_rate=stop_rate, current_rate=current_rate)
