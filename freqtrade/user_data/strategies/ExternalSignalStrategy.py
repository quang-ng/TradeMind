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
    # ROI decay table, not a per-trade computed value.
    minimal_roi = {
        "0": 0.10,
        "60": 0.05,
        "120": 0.02,
        "240": 0,
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
