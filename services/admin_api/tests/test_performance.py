"""Integration tests for GET /performance — exercises real Postgres
reads/writes (see conftest.py). Skips gracefully without Postgres."""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from admin_api.app.deps import get_redis_client
from admin_api.app.main import app
from common import redis_keys
from common.account_balance import AccountBalanceSnapshot
from common.db.models import Order, Position, RiskDecision, Signal
from common.enums import OrderStatus, PositionStatus, SignalStatus

_T0 = datetime(2026, 8, 1, tzinfo=timezone.utc)


class FakeRedis:
    def __init__(self) -> None:
        snapshot = AccountBalanceSnapshot(
            equity_usdt=Decimal("1000"),
            free_balance_usdt=Decimal("900"),
            captured_at=datetime.now(timezone.utc),
        )
        self.values = {redis_keys.ACCOUNT_BALANCE_SNAPSHOT_KEY: snapshot.model_dump_json()}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def aclose(self) -> None:
        return None


@pytest.fixture(autouse=True)
def balance_redis() -> FakeRedis:
    fake = FakeRedis()

    async def override_get_redis_client():
        yield fake

    app.dependency_overrides[get_redis_client] = override_get_redis_client
    yield fake
    app.dependency_overrides.pop(get_redis_client, None)


async def _seed_closed_position(
    session_factory,
    *,
    symbol: str = "BTC/USDT",
    pnl_usdt: str,
    r_multiple: str | None,
    fees_usdt: str | None = "0.10",
    market_regime: str | None = None,
    trade_score: int | None = None,
    minutes: int = 0,
) -> None:
    async with session_factory() as session:
        trace_id = uuid.uuid4()
        signal = Signal(
            trace_id=trace_id,
            symbol=symbol,
            timeframe="1h",
            candle_ts=_T0,
            action="BUY",
            confidence=Decimal("0.8"),
            reasoning="seed",
            model_name="test:model",
            price=Decimal("100"),
            atr_14=Decimal("2"),
            status=SignalStatus.CONSUMED.value,
        )
        session.add(signal)
        await session.flush()
        decision = RiskDecision(
            trace_id=trace_id,
            signal_id=signal.id,
            approved=True,
            position_size_usdt=Decimal("500"),
            position_size_base=Decimal("5"),
            stop_loss_price=Decimal("98"),
            equity_snapshot_usdt=Decimal("1000"),
            risk_pct_applied=Decimal("0.01"),
        )
        session.add(decision)
        await session.flush()
        order = Order(
            trace_id=trace_id,
            risk_decision_id=decision.id,
            symbol=symbol,
            side="BUY",
            status=OrderStatus.FILLED.value,
            requested_amount=Decimal("5"),
            filled_amount=Decimal("5"),
            avg_price=Decimal("100"),
            dry_run=False,
        )
        session.add(order)
        await session.flush()
        session.add(
            Position(
                symbol=symbol,
                status=PositionStatus.CLOSED.value,
                entry_order_id=order.id,
                exit_order_id=order.id,
                entry_price=Decimal("100"),
                exit_price=Decimal("101"),
                amount=Decimal("5"),
                pnl_usdt=Decimal(pnl_usdt),
                pnl_pct=Decimal("0.01"),
                opened_at=_T0 + timedelta(minutes=minutes),
                closed_at=_T0 + timedelta(minutes=minutes + 30),
                exit_reason="minimal_roi",
                fees_usdt=None if fees_usdt is None else Decimal(fees_usdt),
                fees_estimated=True,
                r_multiple=None if r_multiple is None else Decimal(r_multiple),
                market_regime=market_regime,
                trade_score=trade_score,
            )
        )
        await session.commit()


async def test_performance_requires_auth(client):
    assert (await client.get("/performance")).status_code == 401


async def test_performance_empty_history(client, auth_headers):
    body = (await client.get("/performance", headers=auth_headers)).json()
    assert body["trades"] == 0
    assert body["expectancy_r"] is None
    assert body["profit_factor"] is None
    assert body["total_pnl_usdt"] == "0"
    assert body["total_slippage_usdt"] is None


async def test_performance_computes_r_metrics(client, db_session_factory, auth_headers):
    for i in range(9):
        await _seed_closed_position(
            db_session_factory, pnl_usdt="21", r_multiple="2.1", minutes=i
        )
    for i in range(11):
        await _seed_closed_position(
            db_session_factory, pnl_usdt="-10", r_multiple="-1.0", minutes=9 + i
        )

    body = (await client.get("/performance", headers=auth_headers)).json()

    assert body["trades"] == 20
    assert body["trades_with_r"] == 20
    assert Decimal(body["win_rate"]) == Decimal("0.45")
    assert Decimal(body["avg_win_r"]) == Decimal("2.1")
    assert Decimal(body["avg_loss_r"]) == Decimal("-1.0")
    assert Decimal(body["expectancy_r"]) == Decimal("0.395")
    assert Decimal(body["total_fees_usdt"]) == Decimal("2.0")
    # drawdown resolved against the FakeRedis 1000 USDT equity anchor
    assert body["max_drawdown_pct"] is not None


async def test_performance_filters_by_symbol_regime_and_score(
    client, db_session_factory, auth_headers
):
    await _seed_closed_position(
        db_session_factory,
        symbol="BTC/USDT",
        pnl_usdt="20",
        r_multiple="2.0",
        market_regime="trend_pullback",
        trade_score=80,
        minutes=0,
    )
    await _seed_closed_position(
        db_session_factory,
        symbol="ETH/USDT",
        pnl_usdt="-10",
        r_multiple="-1.0",
        market_regime="mean_reversion",
        trade_score=40,
        minutes=1,
    )

    by_symbol = (
        await client.get("/performance?symbol=BTC/USDT", headers=auth_headers)
    ).json()
    assert by_symbol["trades"] == 1
    assert Decimal(by_symbol["total_r"]) == Decimal("2.0")
    assert by_symbol["filters"]["symbol"] == "BTC/USDT"

    by_regime = (
        await client.get("/performance?regime=mean_reversion", headers=auth_headers)
    ).json()
    assert by_regime["trades"] == 1
    assert Decimal(by_regime["total_r"]) == Decimal("-1.0")

    by_score = (
        await client.get("/performance?score_min=70&score_max=100", headers=auth_headers)
    ).json()
    assert by_score["trades"] == 1
    assert Decimal(by_score["expectancy_r"]) == Decimal("2.0")


async def test_performance_rejects_inverted_score_bucket(client, auth_headers):
    response = await client.get(
        "/performance?score_min=80&score_max=20", headers=auth_headers
    )
    assert response.status_code == 400


async def test_performance_drawdown_null_without_equity_anchor(
    client, db_session_factory, auth_headers, balance_redis
):
    balance_redis.values.clear()
    await _seed_closed_position(db_session_factory, pnl_usdt="-10", r_multiple="-1.0")

    body = (await client.get("/performance", headers=auth_headers)).json()

    assert body["trades"] == 1
    assert body["max_drawdown_pct"] is None
    assert body["avg_drawdown_pct"] is None
    assert body["starting_equity_usdt"] is None
    # non-drawdown metrics unaffected
    assert Decimal(body["expectancy_r"]) == Decimal("-1.0")
