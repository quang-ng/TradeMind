"""Pre-registered strategies for the strategy lab (`lab.py`).

Every parameter here was fixed *before* looking at any result, and is
recorded in `docs/research/2026-10-10_strategy_lab_preregistration.md`.
Changing one after seeing holdout numbers turns the holdout into another
design set — make a new, separately named strategy instead.

* `current_system` — approximation of what trades live today: the entry
  confirmation bar (>=3 incl. trend + momentum, `semantic.py`), the
  TREND_FOLLOWING suppression, `risk_engine` ATR stop clamp, the
  pre-PR#18 `minimal_roi` ladder + 2%/1.5% trailing, the exit rubric and
  the 1.5% hard loss-cut. It is the yardstick, not a candidate.
* `trend_breakout` — buy a 20-candle high while the daily trend is up;
  chandelier trail, hold up to 10 days.
* `range_reversion` — buy an oversold lower-band tag in a flat market;
  target the 20-candle mean, hold up to 48 candles.
* `squeeze_breakout` — buy a volume-backed upper-band break after a
  volatility squeeze; stop under the range, target 2R, hold up to 72.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd
from lab_sim import Columns, OpenTrade

_BARS_PER_DAY = {"1h": 24, "4h": 6}

# --- current_system: mirrors of live constants (see module docstring) ---
_TREND_GAP = 0.015  # strategies/selector.py _TREND_GAP_THRESHOLD_PCT
_ATR_STOP_MULT, _MIN_STOP, _MAX_STOP = 2.0, 0.015, 0.08  # RiskConfig defaults
_ROI_LADDER_MIN = ((5760, 0.005), (2880, 0.01), (1440, 0.015), (720, 0.02), (240, 0.03), (0, 0.06))
_TRAIL_ACTIVATION, _TRAIL_DISTANCE = 0.02, 0.015
_HARD_LOSS_CUT, _EXIT_CUSHION = 0.015, 0.005  # LLMServiceSettings defaults


def _shift(a: np.ndarray, k: int) -> np.ndarray:
    out = np.full_like(a, np.nan)
    out[k:] = a[:-k]
    return out


def entry_confirmation_mask(cols: Columns) -> np.ndarray:
    """`ContextBuilder._entry_confirmations` + `_entry_is_sufficient`,
    vectorized: >=3 of 6, including a trend and a momentum one."""
    c, h, l, v = cols["c"], cols["h"], cols["l"], cols["v"]  # noqa: E741
    trend1 = (c > cols["ema50"]) & (c > cols["ema200"])
    trend2 = cols["ema50"] > cols["ema200"]
    mom1 = (cols["macd_hist"] > 0) & (cols["macd"] > cols["macd_signal"])
    mom2 = (cols["rsi14"] >= 45) & (cols["rsi14"] < 70)
    pa1 = (_shift(h, 2) < _shift(h, 1)) & (_shift(h, 1) < h) & (_shift(l, 2) < _shift(l, 1)) & (
        _shift(l, 1) < l
    )
    pa2 = (c > _shift(c, 1)) & (v > cols["vol_sma20"])
    count = sum(x.astype(int) for x in (trend1, trend2, mom1, mom2, pa1, pa2))
    return (count >= 3) & (trend1 | trend2) & (mom1 | mom2)


def exit_confirmation_mask(cols: Columns) -> np.ndarray:
    """`_exit_confirmations` + `_exit_is_sufficient`: >=2 bearish
    confirmations spanning >=2 of trend / momentum / price action."""
    c, h, l, v = cols["c"], cols["h"], cols["l"], cols["v"]  # noqa: E741
    trend = ((c < cols["ema50"]) & (c < cols["ema200"])).astype(int) + (
        cols["ema50"] < cols["ema200"]
    ).astype(int)
    mom = ((cols["macd_hist"] < 0) & (cols["macd"] < cols["macd_signal"])).astype(int) + (
        cols["rsi14"] < 45
    ).astype(int)
    pa = (
        (_shift(h, 2) > _shift(h, 1)) & (_shift(h, 1) > h)
        & (_shift(l, 2) > _shift(l, 1)) & (_shift(l, 1) > l)
    ).astype(int) + ((c < _shift(c, 1)) & (v > cols["vol_sma20"])).astype(int)
    categories = (trend > 0).astype(int) + (mom > 0).astype(int) + (pa > 0).astype(int)
    return (trend + mom + pa >= 2) & (categories >= 2)


def trend_following_mask(cols: Columns) -> np.ndarray:
    """`StrategySelector` primary regime == TREND_FOLLOWING."""
    c, gap = cols["c"], cols["ema_gap"]
    up_aligned = (c > cols["ema50"]) & (c > cols["ema200"])
    down_aligned = (c < cols["ema50"]) & (c < cols["ema200"])
    aligned = np.where(gap > 0, up_aligned, down_aligned)
    return (np.abs(gap) >= _TREND_GAP) & aligned


@dataclass(frozen=True)
class CurrentSystem:
    timeframe: str = "1h"
    name: str = "current_system"
    max_hold_bars: int = 24 * 14

    def entry_mask(self, cols: Columns) -> np.ndarray:
        return entry_confirmation_mask(cols) & ~trend_following_mask(cols)

    def plan(self, cols: Columns, i: int, entry_price: float) -> tuple[float, float | None]:
        signal_price = cols["c"][i]
        dist = min(max(_ATR_STOP_MULT * cols["atr14"][i] / signal_price, _MIN_STOP), _MAX_STOP)
        return signal_price * (1 - dist), None

    def target_price(self, cols: Columns, j: int, trade: OpenTrade) -> float | None:
        elapsed_min = (j - trade.entry_i + 1) * 60
        roi = next(r for mark, r in _ROI_LADDER_MIN if elapsed_min >= mark)
        return trade.entry_price * (1 + roi)

    def on_bar(self, cols: Columns, j: int, trade: OpenTrade) -> str | None:
        if trade.peak_high >= trade.entry_price * (1 + _TRAIL_ACTIVATION):
            trade.stop = max(trade.stop, trade.peak_high * (1 - _TRAIL_DISTANCE))
        pnl = cols["c"][j] / trade.entry_price - 1
        if pnl <= -_HARD_LOSS_CUT:
            return "hard_loss_cut"
        if "_exit_ok" not in cols:  # computed once per series, on first use
            cols["_exit_ok"] = exit_confirmation_mask(cols)
        if cols["_exit_ok"][j] and abs(pnl) > _EXIT_CUSHION:
            return "signal_exit"
        return None


@dataclass(frozen=True)
class TrendBreakout:
    timeframe: str
    name: str = "trend_breakout"
    breakout_lookback: int = 20
    stop_atr: float = 2.0
    trail_atr: float = 3.0
    max_hold_days: int = 10

    @property
    def max_hold_bars(self) -> int:
        return self.max_hold_days * _BARS_PER_DAY[self.timeframe]

    def entry_mask(self, cols: Columns) -> np.ndarray:
        prior_high = pd.Series(cols["h"]).shift(1).rolling(self.breakout_lookback).max()
        return (cols["c"] > prior_high.to_numpy()) & (
            cols["daily_close_prev"] > cols["daily_ema50_prev"]
        )

    def plan(self, cols: Columns, i: int, entry_price: float) -> tuple[float, float | None]:
        return entry_price - self.stop_atr * cols["atr14"][i], None

    def target_price(self, cols: Columns, j: int, trade: OpenTrade) -> float | None:
        return None

    def on_bar(self, cols: Columns, j: int, trade: OpenTrade) -> str | None:
        trade.stop = max(trade.stop, trade.peak_high - self.trail_atr * cols["atr14"][j])
        return None


@dataclass(frozen=True)
class RangeReversion:
    timeframe: str
    name: str = "range_reversion"
    rsi_max: float = 30.0
    stop_atr: float = 1.5
    max_hold_bars: int = 48

    def entry_mask(self, cols: Columns) -> np.ndarray:
        return (
            (np.abs(cols["ema_gap"]) < _TREND_GAP)
            & (cols["rsi14"] < self.rsi_max)
            & (cols["c"] < cols["bb_lower"])
        )

    def plan(self, cols: Columns, i: int, entry_price: float) -> tuple[float, float | None]:
        target = cols["bb_mid"][i]
        if not target > entry_price:
            return float("nan"), None  # already back at the mean: no trade
        return entry_price - self.stop_atr * cols["atr14"][i], target

    def target_price(self, cols: Columns, j: int, trade: OpenTrade) -> float | None:
        return trade.target

    def on_bar(self, cols: Columns, j: int, trade: OpenTrade) -> str | None:
        return None


@dataclass(frozen=True)
class SqueezeBreakout:
    timeframe: str
    name: str = "squeeze_breakout"
    target_r: float = 2.0
    max_hold_bars: int = 72

    def entry_mask(self, cols: Columns) -> np.ndarray:
        width = pd.Series(cols["bb_width"]).shift(1)
        squeeze = width <= width.rolling(120).quantile(0.2)
        return (
            squeeze.to_numpy()
            & (cols["c"] > cols["bb_upper"])
            & (cols["v"] > 1.5 * cols["vol_sma20"])
        )

    def plan(self, cols: Columns, i: int, entry_price: float) -> tuple[float, float | None]:
        atr = cols["atr14"][i]
        stop = min(max(cols["ll20_prev"][i], entry_price - 3 * atr), entry_price - atr)
        return stop, entry_price + self.target_r * (entry_price - stop)

    def target_price(self, cols: Columns, j: int, trade: OpenTrade) -> float | None:
        return trade.target

    def on_bar(self, cols: Columns, j: int, trade: OpenTrade) -> str | None:
        return None


def registry() -> list:
    """Every pre-registered (strategy, timeframe) pair."""
    out: list = [CurrentSystem()]
    for tf in ("1h", "4h"):
        out += [TrendBreakout(tf), RangeReversion(tf), SqueezeBreakout(tf)]
    return out


def design_variants() -> list:
    """Appendix A of the pre-registration: 4h-only variants, fixed before
    they were run. Each has its own name so its holdout record is separate."""
    return [
        TrendBreakout("4h", name="trend_breakout_l55", breakout_lookback=55),
        TrendBreakout("4h", name="trend_breakout_t45", trail_atr=4.5),
        TrendBreakout("4h", name="trend_breakout_l55_t45", breakout_lookback=55, trail_atr=4.5),
        SqueezeBreakout("4h", name="squeeze_breakout_r3", target_r=3.0, max_hold_bars=120),
        RangeReversion("4h", name="range_reversion_rsi35", rsi_max=35.0),
    ]


def label(strategy) -> str:
    return f"{strategy.name}@{strategy.timeframe}"
