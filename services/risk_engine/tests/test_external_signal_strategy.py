import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


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
    trade = SimpleNamespace(enter_tag="slpct:0.02", open_rate=100.0)

    result = strategy.custom_stoploss(
        "BTC/USDT",
        trade,
        current_time=None,
        current_rate=105.0,
        current_profit=0.05,
        after_fill=True,
    )

    assert result == (98.0, 105.0)


def test_malformed_relative_stop_tag_falls_back_to_static_stop(monkeypatch):
    strategy = _load_strategy(monkeypatch)
    trade = SimpleNamespace(enter_tag="slpct:0.50", open_rate=100.0)

    result = strategy.custom_stoploss(
        "BTC/USDT",
        trade,
        current_time=None,
        current_rate=90.0,
        current_profit=-0.10,
        after_fill=True,
    )

    assert result == strategy.stoploss
