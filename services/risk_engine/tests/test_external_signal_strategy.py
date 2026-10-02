import importlib.util
import inspect
import sys
from enum import Enum
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


class _ExitType(Enum):
    NONE = ""
    STOP_LOSS = "stop_loss"
    TRAILING_STOP_LOSS = "trailing_stop_loss"


class _ExitCheckTuple:
    def __init__(self, exit_type: _ExitType, exit_reason: str = "") -> None:
        self.exit_type = exit_type
        self.exit_reason = exit_reason or exit_type.value


class _FakeIStrategy:
    """Mirrors the Freqtrade 2026.8 branch the strategy compensates for:
    `IStrategy.ft_stoploss_reached` only reports a touched stop when
    stoploss_on_exchange is off or in dry-run (strategy/interface.py:1649-1653).
    """

    def __init__(self) -> None:
        self.order_types: dict = {}
        self.config: dict = {"dry_run": False}
        self.super_calls: list[dict] = []

    def ft_stoploss_reached(self, **kwargs):
        self.super_calls.append(kwargs)
        trade = kwargs["trade"]
        rate = kwargs["low"] or kwargs["current_rate"]
        if trade.stop_loss >= rate and (
            not self.order_types.get("stoploss_on_exchange") or self.config["dry_run"]
        ):
            return _ExitCheckTuple(
                _ExitType.TRAILING_STOP_LOSS
                if trade.is_stop_loss_trailing
                else _ExitType.STOP_LOSS
            )
        return _ExitCheckTuple(_ExitType.NONE)


def _load_strategy(monkeypatch):
    freqtrade_module = ModuleType("freqtrade")
    enums_module = ModuleType("freqtrade.enums")
    persistence_module = ModuleType("freqtrade.persistence")
    strategy_module = ModuleType("freqtrade.strategy")
    enums_module.ExitType = _ExitType
    enums_module.ExitCheckTuple = _ExitCheckTuple
    persistence_module.Trade = object
    strategy_module.IStrategy = _FakeIStrategy
    strategy_module.stoploss_from_absolute = (
        lambda *, stop_rate, current_rate: (stop_rate, current_rate)
    )
    monkeypatch.setitem(sys.modules, "freqtrade", freqtrade_module)
    monkeypatch.setitem(sys.modules, "freqtrade.enums", enums_module)
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
    # 10% peak profit clears trailing_activation_pct (2%); the trade ran
    # up to max_rate=110 since entry, so trailing 1.5% behind that peak
    # (108.35) is tighter than the 2%-from-open ATR stop (98.0) and wins.
    trade = SimpleNamespace(enter_tag="slpct:0.02", open_rate=100.0, max_rate=110.0)

    result = strategy.custom_stoploss(
        "BTC/USDT",
        trade,
        current_time=None,
        current_rate=109.0,
        current_profit=0.05,
        after_fill=True,
    )

    assert result == pytest.approx((108.35, 109.0))


def test_trailing_stop_anchors_to_peak_not_current_rate(monkeypatch):
    strategy = _load_strategy(monkeypatch)
    # Price has pulled back from an earlier peak (max_rate=115) to
    # current_rate=111. Trailing must stay anchored to the peak
    # (115 * 0.985 = 113.275), not slip down to trail current_rate
    # instead — that peak-anchored stop already sits above current_rate,
    # i.e. this pullback would trigger the exit, which is the point of a
    # trailing stop.
    trade = SimpleNamespace(enter_tag="slpct:0.02", open_rate=100.0, max_rate=115.0)

    result = strategy.custom_stoploss(
        "BTC/USDT",
        trade,
        current_time=None,
        current_rate=111.0,
        current_profit=0.11,
        after_fill=True,
    )

    assert result == pytest.approx((113.275, 111.0))


def test_trailing_stop_stays_active_when_current_profit_has_pulled_back(monkeypatch):
    strategy = _load_strategy(monkeypatch)
    # The trade peaked at max_rate=110 (10% profit) but current_profit has
    # since fallen to -2% (a sharp pullback). Gating activation on
    # current_profit instead of peak profit would silently drop back to
    # the raw ATR stop right when the trailing protection matters most —
    # this asserts the peak-derived trail (108.35) still wins.
    trade = SimpleNamespace(enter_tag="slpct:0.02", open_rate=100.0, max_rate=110.0)

    result = strategy.custom_stoploss(
        "BTC/USDT",
        trade,
        current_time=None,
        current_rate=98.0,
        current_profit=-0.02,
        after_fill=True,
    )

    assert result == pytest.approx((108.35, 98.0))


def _open_trade(*, stop_loss: float, trailing: bool = False, sl_order_ids=()):
    sl_orders = [SimpleNamespace(order_id=order_id) for order_id in sl_order_ids]
    return SimpleNamespace(
        pair="BTC/USDT",
        stop_loss=stop_loss,
        is_stop_loss_trailing=trailing,
        is_short=False,
        open_sl_orders=sl_orders,
        has_open_sl_orders=bool(sl_orders),
    )


def _check_stop(strategy, trade, current_rate: float):
    return strategy.ft_stoploss_reached(
        current_rate=current_rate,
        trade=trade,
        current_time=None,
        current_profit=0.0,
        force_stoploss=0,
    )


def _live_with_exchange_stop(strategy):
    strategy.order_types = {"stoploss_on_exchange": True}
    strategy.config = {"dry_run": False}
    return strategy


def test_bot_side_stop_still_fires_live_with_stoploss_on_exchange(monkeypatch):
    # Freqtrade skips its own check here; the exchange stop-limit may have
    # been gapped through, so the bot must still market-exit on its own.
    strategy = _live_with_exchange_stop(_load_strategy(monkeypatch))
    trade = _open_trade(stop_loss=98.0)

    result = _check_stop(strategy, trade, current_rate=97.5)

    assert result.exit_type == _ExitType.STOP_LOSS
    assert result.exit_reason == "stop_loss"


def test_bot_side_stop_fires_when_rate_equals_stop(monkeypatch):
    strategy = _live_with_exchange_stop(_load_strategy(monkeypatch))

    result = _check_stop(strategy, _open_trade(stop_loss=98.0), current_rate=98.0)

    assert result.exit_type == _ExitType.STOP_LOSS


def test_bot_side_trailing_stop_keeps_trailing_exit_type(monkeypatch):
    strategy = _live_with_exchange_stop(_load_strategy(monkeypatch))
    trade = _open_trade(stop_loss=108.35, trailing=True)

    result = _check_stop(strategy, trade, current_rate=108.0)

    assert result.exit_type == _ExitType.TRAILING_STOP_LOSS
    assert result.exit_reason == "trailing_stop_loss"


def test_bot_side_stop_does_not_fire_above_stop(monkeypatch):
    strategy = _live_with_exchange_stop(_load_strategy(monkeypatch))

    result = _check_stop(strategy, _open_trade(stop_loss=98.0), current_rate=98.01)

    assert result.exit_type == _ExitType.NONE


@pytest.mark.parametrize(
    ("order_types", "dry_run"),
    [({"stoploss_on_exchange": False}, False), ({"stoploss_on_exchange": True}, True)],
)
def test_freqtrade_own_stop_result_is_passed_through_unchanged(
    monkeypatch, order_types, dry_run
):
    # Flag off (rollback) or dry-run: Freqtrade's own check already reports
    # the hit — the override must return that result, not add a second one.
    strategy = _load_strategy(monkeypatch)
    strategy.order_types = order_types
    strategy.config = {"dry_run": dry_run}
    trade = _open_trade(stop_loss=98.0, trailing=True)

    result = _check_stop(strategy, trade, current_rate=97.0)

    assert result.exit_type == _ExitType.TRAILING_STOP_LOSS
    assert len(strategy.super_calls) == 1


def test_bot_side_stop_forwards_every_argument_to_freqtrade(monkeypatch):
    # super() is what calls custom_stoploss and moves trade.stop_loss up —
    # every argument (incl. backtest low/high and bound_profit) must reach it.
    strategy = _live_with_exchange_stop(_load_strategy(monkeypatch))
    trade = _open_trade(stop_loss=90.0)

    strategy.ft_stoploss_reached(
        current_rate=100.0,
        trade=trade,
        current_time="now",
        current_profit=0.01,
        force_stoploss=0.05,
        low=99.0,
        high=101.0,
        bound_profit=0.02,
    )

    assert strategy.super_calls == [
        {
            "current_rate": 100.0,
            "trade": trade,
            "current_time": "now",
            "current_profit": 0.01,
            "force_stoploss": 0.05,
            "low": 99.0,
            "high": 101.0,
            "bound_profit": 0.02,
        }
    ]


def test_bot_side_stop_override_matches_pinned_freqtrade_signature(monkeypatch):
    # Overrides a Freqtrade-internal method: this is the 2026.8 signature
    # (strategy/interface.py). If a Freqtrade bump changes it, update the
    # override and this list together — see the method's docstring.
    strategy = _load_strategy(monkeypatch)

    params = list(inspect.signature(strategy.ft_stoploss_reached).parameters)

    assert params == [
        "current_rate",
        "trade",
        "current_time",
        "current_profit",
        "force_stoploss",
        "low",
        "high",
        "bound_profit",
    ]


def test_custom_stoploss_keeps_after_fill_parameter(monkeypatch):
    # Freqtrade only re-runs custom_stoploss right after the entry fills if
    # the signature declares after_fill — that is what places the first
    # exchange stop at the ATR stop rather than the static -8% floor.
    strategy = _load_strategy(monkeypatch)

    assert "after_fill" in inspect.signature(strategy.custom_stoploss).parameters


def _confirm_exit(strategy, trade) -> bool:
    return strategy.confirm_trade_exit(
        pair="BTC/USDT",
        trade=trade,
        order_type="market",
        amount=0.001,
        rate=97.0,
        time_in_force="GTC",
        exit_reason="stop_loss",
        current_time=None,
    )


def test_exit_denied_while_exchange_stop_still_open_after_cancel(monkeypatch):
    # Cancel failed (e.g. Binance -2011: the stop filled a moment earlier) —
    # selling now could only sell coins that aren't this trade's.
    strategy = _live_with_exchange_stop(_load_strategy(monkeypatch))
    trade = _open_trade(stop_loss=98.0, sl_order_ids=("123",))

    assert _confirm_exit(strategy, trade) is False


def test_exit_allowed_once_exchange_stop_is_cancelled(monkeypatch):
    strategy = _live_with_exchange_stop(_load_strategy(monkeypatch))

    assert _confirm_exit(strategy, _open_trade(stop_loss=98.0)) is True


def test_exit_guard_is_inactive_with_stoploss_on_exchange_off(monkeypatch):
    # After a rollback, stop orders cancelled by hand on Binance stay "open"
    # in Freqtrade's DB forever (nothing re-fetches them) — guarding here
    # would block every exit.
    strategy = _load_strategy(monkeypatch)
    strategy.order_types = {"stoploss_on_exchange": False}
    trade = _open_trade(stop_loss=98.0, sl_order_ids=("123",))

    assert _confirm_exit(strategy, trade) is True
