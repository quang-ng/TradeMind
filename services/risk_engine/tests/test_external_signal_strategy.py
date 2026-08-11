import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


def _load_strategy(monkeypatch):
    freqtrade_module = ModuleType("freqtrade")
    persistence_module = ModuleType("freqtrade.persistence")
    strategy_module = ModuleType("freqtrade.strategy")
    persistence_module.Trade = object
    strategy_module.IStrategy = object
    strategy_module.stoploss_from_absolute = (
        lambda *, stop_rate, current_rate: (stop_rate, current_rate)
    )
    monkeypatch.setitem(sys.modules, "freqtrade", freqtrade_module)
    monkeypatch.setitem(sys.modules, "freqtrade.persistence", persistence_module)
    monkeypatch.setitem(sys.modules, "freqtrade.strategy", strategy_module)

    path = (
        Path(__file__).parents[3]
        / "freqtrade"
        / "user_data"
        / "strategies"
        / "ExternalSignalStrategy.py"
    )
    spec = importlib.util.spec_from_file_location("external_signal_strategy_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ExternalSignalStrategy()


def test_relative_stop_tag_uses_authoritative_trade_open_rate(monkeypatch):
    strategy = _load_strategy(monkeypatch)
    # Peak profit (from max_rate) stays below trailing_activation_pct, so
    # the trailing branch never engages and the ATR stop alone determines
    # the result.
    trade = SimpleNamespace(enter_tag="slpct:0.02", open_rate=100.0, max_rate=100.5)

    result = strategy.custom_stoploss(
        "BTC/USDT",
        trade,
        current_time=None,
        current_rate=100.5,
        current_profit=0.005,
        after_fill=True,
    )

    assert result == (98.0, 100.5)


def test_malformed_relative_stop_tag_falls_back_to_static_stop(monkeypatch):
    strategy = _load_strategy(monkeypatch)
    trade = SimpleNamespace(enter_tag="slpct:0.50", open_rate=100.0, max_rate=100.0)

    result = strategy.custom_stoploss(
        "BTC/USDT",
        trade,
        current_time=None,
        current_rate=90.0,
        current_profit=-0.10,
        after_fill=True,
    )

    assert result == strategy.stoploss


def test_trailing_stop_activates_and_wins_when_tighter_than_atr_stop(monkeypatch):
    strategy = _load_strategy(monkeypatch)
    # 5% profit clears trailing_activation_pct (1%); the trade ran up to
    # max_rate=110 since entry, so trailing 0.75% behind that peak (109.175)
    # is tighter than the 2%-from-open ATR stop (98.0) and should win.
    trade = SimpleNamespace(enter_tag="slpct:0.02", open_rate=100.0, max_rate=110.0)

    result = strategy.custom_stoploss(
        "BTC/USDT",
        trade,
        current_time=None,
        current_rate=109.0,
        current_profit=0.05,
        after_fill=True,
    )

    assert result == pytest.approx((109.175, 109.0))


def test_trailing_stop_anchors_to_peak_not_current_rate(monkeypatch):
    strategy = _load_strategy(monkeypatch)
    # Price has pulled back from an earlier peak (max_rate=115) to
    # current_rate=112. Trailing must stay anchored to the peak
    # (115 * 0.9925 = 114.1375), not slip down to trail current_rate
    # instead — that peak-anchored stop already sits above current_rate,
    # i.e. this pullback would trigger the exit, which is the point of a
    # trailing stop.
    trade = SimpleNamespace(enter_tag="slpct:0.02", open_rate=100.0, max_rate=115.0)

    result = strategy.custom_stoploss(
        "BTC/USDT",
        trade,
        current_time=None,
        current_rate=112.0,
        current_profit=0.12,
        after_fill=True,
    )

    assert result == pytest.approx((114.1375, 112.0))


def test_trailing_stop_stays_active_when_current_profit_has_pulled_back(monkeypatch):
    strategy = _load_strategy(monkeypatch)
    # The trade peaked at max_rate=110 (10% profit) but current_profit has
    # since fallen to -2% (a sharp pullback). Gating activation on
    # current_profit instead of peak profit would silently drop back to
    # the raw ATR stop right when the trailing protection matters most —
    # this asserts the peak-derived trail (109.175) still wins.
    trade = SimpleNamespace(enter_tag="slpct:0.02", open_rate=100.0, max_rate=110.0)

    result = strategy.custom_stoploss(
        "BTC/USDT",
        trade,
        current_time=None,
        current_rate=98.0,
        current_profit=-0.02,
        after_fill=True,
    )

    assert result == pytest.approx((109.175, 98.0))
