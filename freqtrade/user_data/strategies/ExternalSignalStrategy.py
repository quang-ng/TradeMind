from freqtrade.strategy import IStrategy
from pandas import DataFrame


class ExternalSignalStrategy(IStrategy):
    """PROJECT.md Section 6 / Section 14 rule 8: this strategy has no
    autonomous entry or exit logic. Entries occur only via authenticated
    `forceenter` calls from `risk_engine`; SELL signals that resolve to an
    exit also come from `risk_engine`, via `forceexit`. This class exists
    to satisfy Freqtrade's `IStrategy` contract and to provide the static
    safety net described in PROJECT.md Section 9.2.

    `stoploss` is intentionally the conservative upper bound
    (`RiskConfig.max_stop_loss_pct`), not the tighter, per-trade ATR-based
    `stop_loss_price` the Risk Engine computes (PROJECT.md Section 9.2) —
    Freqtrade's `forceenter` API has no field for a per-trade custom stop,
    only this strategy-wide static value. The computed value is still
    persisted to `RiskDecision.stop_loss_price` for audit.
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
    # Conservative static floor — see class docstring.
    stoploss = -0.08

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
