import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from admin_api.app.deps import get_redis_client
from admin_api.app.main import app
from common import redis_keys
from common.account_balance import AccountBalanceSnapshot
from common.db.models import Order, Position, RiskDecision, Signal
from common.enums import Action, OrderStatus, PositionStatus, SignalStatus


class FakeRedis:
    def __init__(self) -> None:
        snapshot = AccountBalanceSnapshot(
            equity_usdt=Decimal("115"),
            free_balance_usdt=Decimal("100"),
            captured_at=datetime.now(timezone.utc),
        )
        self.values = {
            redis_keys.ACCOUNT_BALANCE_SNAPSHOT_KEY: snapshot.model_dump_json()
        }

    async def get(self, key: str) -> str | None:
        return self.values.get(key)


@pytest.fixture(autouse=True)
def balance_redis() -> FakeRedis:
    fake = FakeRedis()

    async def override_get_redis_client():
        yield fake

    app.dependency_overrides[get_redis_client] = override_get_redis_client
    yield fake
    app.dependency_overrides.pop(get_redis_client, None)


async def test_status_requires_auth(client):
    response = await client.get("/status")
    assert response.status_code == 401


async def test_status_defaults_with_empty_db(client, auth_headers):
    response = await client.get("/status", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["killswitch_enabled"] is False
    assert body["dry_run"] is True
    assert body["open_positions"] == 0
    assert body["equity_usdt"] == "115"
    assert body["free_balance_usdt"] == "100"
    assert body["pairs"]["BTC/USDT"]["last_cycle_at"] is None
    assert body["pairs"]["ETH/USDT"]["last_action"] is None


async def test_status_fails_when_live_balance_is_unavailable(
    client, auth_headers, balance_redis
):
    balance_redis.values.clear()

    response = await client.get("/status", headers=auth_headers)

    assert response.status_code == 503
    assert response.json()["detail"] == "account balance unavailable"


async def test_status_reflects_open_position_and_latest_signal(
    client, db_session_factory, auth_headers
):
    async with db_session_factory() as session:
        trace_id = uuid.uuid4()
        signal_id = uuid.uuid4()
        session.add(
            Signal(
                id=signal_id,
                trace_id=trace_id,
                symbol="BTC/USDT",
                timeframe="1h",
                candle_ts=datetime.now(timezone.utc),
                action=Action.BUY.value,
                confidence=Decimal("0.8"),
                reasoning="test",
                model_name="test:model",
                price=Decimal("60000"),
                atr_14=Decimal("500"),
                status=SignalStatus.CONSUMED.value,
            )
        )
        await session.flush()
        decision = RiskDecision(
            trace_id=trace_id,
            signal_id=signal_id,
            approved=True,
            position_size_usdt=Decimal("500"),
            position_size_base=Decimal("0.0083"),
            stop_loss_price=Decimal("59000"),
            equity_snapshot_usdt=Decimal("10000"),
            risk_pct_applied=Decimal("0.01"),
        )
        session.add(decision)
        await session.flush()
        order = Order(
            trace_id=trace_id,
            risk_decision_id=decision.id,
            freqtrade_trade_id=1,
            symbol="BTC/USDT",
            side="BUY",
            status=OrderStatus.FILLED.value,
            requested_amount=Decimal("0.0083"),
            filled_amount=Decimal("0.0083"),
            avg_price=Decimal("60000"),
            dry_run=True,
        )
        session.add(order)
        await session.flush()
        session.add(
            Position(
                symbol="BTC/USDT",
                status=PositionStatus.OPEN.value,
                entry_order_id=order.id,
                entry_price=Decimal("60000"),
                amount=Decimal("0.0083"),
            )
        )
        await session.commit()

    response = await client.get("/status", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["open_positions"] == 1
    assert body["pairs"]["BTC/USDT"]["last_action"] == "BUY"
