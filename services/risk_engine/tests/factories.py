"""Shared test fixtures for the Risk Engine rule set. Not a pytest
conftest — plain factory functions each test file imports explicitly, so
every rule test only has to override the field its rule actually cares
about."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from common.config import RiskConfig
from common.enums import Action
from risk_engine.app import sizing
from risk_engine.app.schemas import AccountState, RuleContext, SignalView
from risk_engine.app.sizing import SizingResult

NOW = datetime(2026, 7, 15, 13, 0, tzinfo=timezone.utc)


def make_signal(**overrides) -> SignalView:
    defaults = dict(
        id="11111111-1111-1111-1111-111111111111",
        symbol="BTC/USDT",
        action=Action.BUY,
        confidence=Decimal("0.80"),
        candle_ts=NOW - timedelta(minutes=1),
        price=Decimal("60000"),
        atr_14=Decimal("500"),
    )
    defaults.update(overrides)
    return SignalView(**defaults)


def make_account(**overrides) -> AccountState:
    defaults = dict(
        equity_usdt=Decimal("10000"),
        free_balance_usdt=Decimal("10000"),
        open_position_symbols=frozenset(),
        total_exposure_usdt=Decimal("0"),
        daily_pnl_pct=Decimal("0"),
        consecutive_losses=0,
        last_loss_closed_at=None,
        symbol_last_closed_at={},
    )
    defaults.update(overrides)
    return AccountState(**defaults)


def make_context(
    *,
    signal: SignalView | None = None,
    account: AccountState | None = None,
    config: RiskConfig | None = None,
    now: datetime = NOW,
    killswitch_enabled: bool = False,
    is_duplicate_decision: bool = False,
    candidate: SizingResult | None = None,
) -> RuleContext:
    signal = signal or make_signal()
    account = account or make_account()
    config = config or RiskConfig()
    candidate = candidate or sizing.compute_sizing(
        equity_usdt=account.equity_usdt,
        free_balance_usdt=account.free_balance_usdt,
        entry_price=signal.price,
        atr_14=signal.atr_14,
        config=config,
    )
    return RuleContext(
        signal=signal,
        account=account,
        config=config,
        now=now,
        killswitch_enabled=killswitch_enabled,
        is_duplicate_decision=is_duplicate_decision,
        candidate=candidate,
    )
