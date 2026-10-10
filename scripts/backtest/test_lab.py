"""Unit tests for the strategy lab: fill rules (`lab_sim`), indicator /
resample plumbing (`lab_data`), parity of the vectorized entry/exit
confirmation bars and regime with the live `llm_service` code, and the
evaluation statistics + verdict (`lab`)."""

import _bootstrap  # noqa: F401,I001 -- must patch sys.path before the imports below
from dataclasses import dataclass, replace
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest
from lab import Evaluation, clustered_ci, evaluate  # noqa: E402
from lab_data import add_indicators  # noqa: E402
from lab_sim import OpenTrade, Trade, columns, period_mask, r_multiple, simulate  # noqa: E402
from lab_strategies import (  # noqa: E402
    CurrentSystem,
    entry_confirmation_mask,
    exit_confirmation_mask,
    trend_following_mask,
)
from llm_service.app.context.builder import ContextBuilder  # noqa: E402
from llm_service.app.models.strategy import StrategyName  # noqa: E402
from llm_service.app.models.wire import AnalyzeRequest  # noqa: E402
from llm_service.app.strategies.selector import StrategySelector  # noqa: E402
from llm_service.app.validators.semantic import (  # noqa: E402
    _entry_is_sufficient,
    _exit_is_sufficient,
)

FEE = 0.001


def _frame(bars: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=len(bars), freq="1h", tz="UTC")
    o, h, l, c = zip(*bars)  # noqa: E741
    return pd.DataFrame({"o": o, "h": h, "l": l, "c": c, "v": 1.0}, index=index)


@dataclass(frozen=True)
class _Fixed:
    """Entry at bar 0's close; stop/target as given; optional trail."""

    stop: float
    target: float | None = None
    trail_to: float | None = None  # raise the stop to this at the first close
    exit_at_bar: int | None = None
    max_hold_bars: int = 100
    name: str = "fixed"
    timeframe: str = "1h"

    def entry_mask(self, cols):
        mask = np.zeros(len(cols["c"]), dtype=bool)
        mask[0] = True
        return mask

    def plan(self, cols, i, entry_price):
        return self.stop, self.target

    def target_price(self, cols, j, trade: OpenTrade):
        return trade.target

    def on_bar(self, cols, j, trade: OpenTrade):
        if self.trail_to is not None:
            trade.stop = max(trade.stop, self.trail_to)
        return "signal_exit" if j == self.exit_at_bar else None


def _run(bars, strategy, mask=None):
    df = _frame(bars)
    cols = columns(df)
    mask = strategy.entry_mask(cols) if mask is None else mask
    return simulate("BTC/USDT", df, cols, strategy, mask, fee_pct=FEE)


FLAT = (100.0, 100.5, 99.5, 100.0)


def test_entry_fills_at_next_open_and_stop_fills_at_stop():
    trades = _run([FLAT, (100, 101, 99, 100), (100, 100, 94, 95)], _Fixed(stop=96))
    assert len(trades) == 1
    t = trades[0]
    assert t.entry_price == 100 and t.exit_price == 96 and t.reason == "stop"
    assert t.r == pytest.approx(r_multiple(100, 96, 96, FEE))


def test_open_through_the_stop_fills_at_the_open():
    t = _run([FLAT, (100, 101, 99, 100), (93, 94, 92, 93)], _Fixed(stop=96))[0]
    assert t.exit_price == 93


def test_stop_is_checked_before_target_in_the_same_candle():
    t = _run([FLAT, (100, 110, 90, 100)], _Fixed(stop=96, target=105))[0]
    assert t.reason == "stop" and t.exit_price == 96


def test_target_fills_at_target_or_better_open():
    assert _run([FLAT, (100, 106, 99, 104)], _Fixed(stop=96, target=105))[0].exit_price == 105
    assert _run([FLAT, (100, 101, 99, 100), (107, 108, 106, 107)],
                _Fixed(stop=96, target=105))[0].exit_price == 107


def test_trailing_raise_applies_from_the_next_candle_only():
    # Bar 1 dips to 98 but the trail to 99 is only set at bar 1's close.
    bars = [FLAT, (100, 103, 98, 102), (102, 102, 98.5, 99)]
    t = _run(bars, _Fixed(stop=96, trail_to=99))[0]
    assert t.exit_time == _frame(bars).index[2]
    assert t.exit_price == 99 and t.reason == "trailing_stop"


def test_close_decided_exit_fills_at_next_open():
    bars = [FLAT, (100, 101, 99, 100), (101, 102, 100, 101), (103, 104, 102, 103)]
    t = _run(bars, _Fixed(stop=90, exit_at_bar=2))[0]
    assert t.exit_price == 103 and t.reason == "signal_exit"


def test_time_exit_after_max_hold():
    bars = [FLAT] + [(100, 101, 99, 100)] * 5
    t = _run(bars, _Fixed(stop=90, max_hold_bars=2))[0]
    assert t.reason == "time" and t.bars_held == 2


def test_one_open_trade_per_symbol():
    bars = [FLAT] * 3 + [(100, 100, 94, 95), FLAT, FLAT]
    mask = np.array([True, True, True, False, True, False])
    trades = _run(bars, _Fixed(stop=96), mask=mask)
    # First trade opens at bar 1 and stops at bar 3; signals at bars 1-2
    # are ignored while it is open; bar 4's signal opens the next trade.
    assert [t.entry_time.hour for t in trades] == [1, 5]


def test_period_mask_uses_candle_close_time():
    df = _frame([FLAT] * 4)
    mask = period_mask(df, pd.Timestamp("2025-01-01T02:00Z"), pd.Timestamp("2025-01-01T04:00Z"))
    assert mask.tolist() == [False, True, True, False]


def _random_walk(n=900, seed=7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    open_ = np.r_[close[0], close[:-1]]
    spread = np.abs(rng.normal(0, 0.006, n)) * close
    df = pd.DataFrame(
        {"o": open_, "h": np.maximum(open_, close) + spread,
         "l": np.minimum(open_, close) - spread, "c": close,
         "v": rng.lognormal(3, 0.5, n)},
        index=pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC"),
    )
    return add_indicators(df)


def _context_at(df: pd.DataFrame, i: int):
    row = df.iloc[i]
    window = df.iloc[i - 3 : i + 1]
    return ContextBuilder().build(AnalyzeRequest(
        symbol="BTC/USDT", timeframe="1h", candle_close_time="2025-01-01T00:00:00+00:00",
        ohlcv=[{"t": str(t), "o": r.o, "h": r.h, "l": r.l, "c": r.c, "v": r.v}
               for t, r in window.iterrows()],
        indicators={
            "rsi_14": row.rsi14, "ema_50": row.ema50, "ema_200": row.ema200,
            "macd": {"macd": row.macd, "signal": row.macd_signal, "histogram": row.macd_hist},
            "atr_14": row.atr14, "volume_sma_20": row.vol_sma20,
        },
        position_context={"has_open_position": False},
    ))


def test_vectorized_bars_match_live_llm_service_logic():
    df = _random_walk()
    cols = columns(df)
    entry, exit_, tf = (
        entry_confirmation_mask(cols), exit_confirmation_mask(cols), trend_following_mask(cols)
    )
    checked = 0
    for i in range(250, len(df)):
        context = _context_at(df, i)
        assert entry[i] == _entry_is_sufficient(context.entry_confirmations), i
        assert exit_[i] == _exit_is_sufficient(context.exit_confirmations), i
        is_tf = StrategySelector().select(context).strategy == StrategyName.TREND_FOLLOWING
        assert tf[i] == is_tf, i
        checked += 1
    assert checked > 600 and entry.any() and exit_.any() and tf.any()


def test_current_system_stop_uses_the_risk_engine_clamp():
    df = _random_walk()
    cols = columns(df)
    i = 400
    stop, target = CurrentSystem().plan(cols, i, cols["o"][i + 1])
    dist = 1 - stop / cols["c"][i]
    assert 0.015 - 1e-12 <= dist <= 0.08 + 1e-12 and target is None


def test_4h_resample_drops_partial_buckets():
    from lab_data import load_candles

    class _Cache:
        def get_range(self, symbol, tf, a, b):
            start = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
            return [{"t": start + k * 3_600_000, "o": k, "h": k + 1, "l": k - 1, "c": k + 0.5,
                     "v": 1.0} for k in range(10)]

    df = load_candles(_Cache(), "BTC/USDT", "4h", datetime(2025, 1, 1), datetime(2025, 1, 2))
    assert len(df) == 2  # hours 0-3 and 4-7; 8-9 is partial
    assert df.iloc[0].tolist() == [0, 4, -1, 3.5, 4.0]


def _trade(day: int, r: float) -> Trade:
    t = pd.Timestamp("2025-01-06", tz="UTC") + pd.Timedelta(days=day)
    return Trade("BTC/USDT", t, t, 100, 100, 99, r, "time", 1)


def test_clustered_ci_is_wider_when_trades_share_a_week():
    spread = [_trade(7 * k, r) for k, r in enumerate([1, -1, 1, -1, 1, -1, 1, -1])]
    bunched = [_trade(k % 2, r) for k, r in enumerate([1, 1, 1, 1, -1, -1, -1, -1])]
    bunched = [replace(t, entry_time=t.entry_time + pd.Timedelta(days=7 * (k // 4)))
               for k, t in enumerate(bunched)]
    lo_s, hi_s = clustered_ci(spread)
    lo_b, hi_b = clustered_ci(bunched)
    assert hi_b - lo_b > hi_s - lo_s


def test_evaluate_and_verdict():
    trades = [_trade(k, 0.5 if k % 3 else -1.0) for k in range(300)]
    random_runs = [[_trade(k, -0.1) for k in range(50)] for _ in range(10)]
    e = evaluate("x@1h", trades, random_runs)
    assert e.n == 300 and e.random_mean_r == pytest.approx(-0.1)
    assert e.p_value == pytest.approx(1 / 11)  # no random run beat it; 10 seeds
    passed, failures = e.verdict()
    assert not passed and any("p >=" in f for f in failures)
    assert replace(e, p_value=0.01).verdict()[0] is (e.ci_low > 0 and e.quarters_positive >= 3)


def test_verdict_lists_every_failure():
    e = Evaluation("x", 10, 0.5, -0.1, -0.3, 0.1, -1.0, 5, 0.0, 0.5, 1, 4, {}, {})
    passed, failures = e.verdict()
    assert not passed and len(failures) == 5


def test_quarter_criterion_scales_with_period_length():
    e = Evaluation("x", 500, 0.5, 0.3, 0.1, 0.5, 150.0, 5, 0.0, 0.01, 14, 19, {}, {})
    assert e.verdict()[0] is False  # needs ceil(0.75 * 19) = 15
    assert replace(e, quarters_positive=15).verdict()[0] is True
    assert replace(e, quarters_positive=3, quarters_total=4).verdict()[0] is True
