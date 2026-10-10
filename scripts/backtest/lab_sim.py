"""Trade simulator for the strategy lab (`lab.py`).

A strategy is a complete package: when to buy (`entry_mask`), where the
initial stop and any target sit (`plan`), and how the trade is managed bar
by bar (`target_price`, `on_bar`, `max_hold_bars`). Long-only, one open
trade per symbol, every trade measured in R = net P&L / initial risk.

Fill rules (the same for every strategy, so comparisons stay fair):

* entry at the open of the candle after the decision candle;
* within a candle the stop is checked before the target (pessimistic when
  both are touched); an open already beyond the stop fills at the open;
* trailing stops and other state are updated at the candle close and only
  apply from the next candle — no same-candle "peak then pullback" guess;
* exits decided at a close (signal, time limit) fill at the next open.

`random_control` reruns a strategy with random entry candles but the exact
same `plan`/management, so "strategy mean R minus random mean R" isolates
what the *entry* selection contributes.
"""

from dataclasses import dataclass
from typing import Protocol

import numpy as np
import pandas as pd

Columns = dict[str, np.ndarray]


@dataclass
class OpenTrade:
    entry_i: int
    entry_price: float
    initial_stop: float
    stop: float  # stop in force from the next candle's open
    target: float | None
    peak_high: float
    bars_held: int = 0


@dataclass(frozen=True)
class Trade:
    symbol: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    initial_stop: float
    r: float
    reason: str
    bars_held: int


class Strategy(Protocol):
    name: str
    timeframe: str
    max_hold_bars: int

    def entry_mask(self, cols: Columns) -> np.ndarray: ...

    def plan(self, cols: Columns, i: int, entry_price: float) -> tuple[float, float | None]:
        """(initial stop, fixed target or None) for a decision at close `i`."""
        ...

    def target_price(self, cols: Columns, j: int, trade: OpenTrade) -> float | None:
        """Intrabar take-profit level for candle `j` (may vary with time)."""
        ...

    def on_bar(self, cols: Columns, j: int, trade: OpenTrade) -> str | None:
        """Close-of-candle hook: may raise `trade.stop`; a returned reason
        exits at the next open."""
        ...


def columns(df: pd.DataFrame) -> Columns:
    return {name: df[name].to_numpy(dtype=float) for name in df.columns}


def r_multiple(entry: float, exit_: float, stop: float, fee_pct: float) -> float:
    net = exit_ * (1 - fee_pct) - entry * (1 + fee_pct)
    return net / (entry - stop)


def simulate(
    symbol: str,
    df: pd.DataFrame,
    cols: Columns,
    strategy: Strategy,
    mask: np.ndarray,
    *,
    fee_pct: float,
) -> list[Trade]:
    o, h, l, c = cols["o"], cols["h"], cols["l"], cols["c"]  # noqa: E741
    index = df.index
    n = len(o)
    trades: list[Trade] = []
    free_from = 0  # first decision candle allowed (one trade per symbol)
    for i in np.flatnonzero(mask):
        if i < free_from or i + 1 >= n:
            continue
        entry = o[i + 1]
        stop, target = strategy.plan(cols, i, entry)
        if not np.isfinite(stop) or stop >= entry:
            continue
        trade = OpenTrade(i + 1, entry, stop, stop, target, entry)
        exit_i, exit_price, reason = None, 0.0, ""
        j = i + 1
        while j < n:
            if o[j] <= trade.stop:
                exit_i, exit_price, reason = j, o[j], "stop"
                break
            if l[j] <= trade.stop:
                exit_i, exit_price, reason = j, trade.stop, "stop"
                break
            tp = strategy.target_price(cols, j, trade)
            if tp is not None and h[j] >= tp:
                exit_i, exit_price, reason = j, max(o[j], tp), "target"
                break
            trade.bars_held += 1
            trade.peak_high = max(trade.peak_high, h[j])
            signal = strategy.on_bar(cols, j, trade)
            if signal is None and trade.bars_held >= strategy.max_hold_bars:
                signal = "time"
            if signal is not None:
                if j + 1 < n:
                    exit_i, exit_price, reason = j + 1, o[j + 1], signal
                else:
                    exit_i, exit_price, reason = j, c[j], "data_end"
                break
            j += 1
        if exit_i is None:  # ran out of data while open: mark at last close
            exit_i, exit_price, reason = n - 1, c[n - 1], "data_end"
        if trade.stop > trade.initial_stop and reason == "stop":
            reason = "trailing_stop"
        trades.append(Trade(
            symbol=symbol,
            entry_time=index[trade.entry_i],
            exit_time=index[exit_i],
            entry_price=entry,
            exit_price=exit_price,
            initial_stop=trade.initial_stop,
            r=r_multiple(entry, exit_price, trade.initial_stop, fee_pct),
            reason=reason,
            bars_held=trade.bars_held,
        ))
        free_from = exit_i
    return trades


def period_mask(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> np.ndarray:
    """Decision candles whose *close* falls in `[start, end)`."""
    step = df.index[1] - df.index[0] if len(df) > 1 else pd.Timedelta(hours=1)
    close_time = df.index + step
    return np.asarray((close_time >= start) & (close_time < end))


def random_control(
    symbol: str,
    df: pd.DataFrame,
    cols: Columns,
    strategy: Strategy,
    in_period: np.ndarray,
    density: float,
    *,
    seeds: int,
    fee_pct: float,
    base_seed: int = 0,
) -> list[list[Trade]]:
    """`seeds` runs of the strategy with entries drawn uniformly at random
    over the same period at `density` (the strategy's own signal rate)."""
    runs = []
    for seed in range(seeds):
        rng = np.random.default_rng(base_seed + seed)
        mask = in_period & (rng.random(len(in_period)) < density)
        runs.append(simulate(symbol, df, cols, strategy, mask, fee_pct=fee_pct))
    return runs
