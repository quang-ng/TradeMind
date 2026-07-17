"""Integration tests for POST /webhooks/freqtrade — exercises real Postgres
reads/writes (see services/risk_engine/tests/test_main_integration.py for
the same rationale). Skips gracefully if no Postgres is reachable."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest
from admin_api.app.deps import get_db_session
from admin_api.app.main import app
from admin_api.app.routers.webhooks import get_webhook_settings
from common.config import DatabaseSettings, WebhookSettings
from common.db.models import Order, Position, RiskDecision, Signal
from common.enums import Action, OrderStatus, PositionStatus, SignalStatus
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

WEBHOOK_SECRET = "test-shared-secret"


@pytest.fixture(autouse=True)
def _webhook_settings():
    app.dependency_overrides[get_webhook_settings] = lambda: WebhookSettings(
        webhook_shared_secret=WEBHOOK_SECRET
    )
    yield
    app.dependency_overrides.pop(get_webhook_settings, None)


@pytest.fixture
async def db_session_factory():
    engine = create_async_engine(DatabaseSettings().postgres_dsn)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            await session.execute(select(1))
    except Exception:
        await engine.dispose()
        pytest.skip("no live Postgres reachable (set POSTGRES_DSN or run `make up`)")

    async with session_factory() as session:
        for table in ("audit_events", "positions", "orders", "risk_decisions", "signals"):
            await session.execute(text(f"DELETE FROM {table}"))
        await session.commit()

    async def override_get_db_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_get_db_session
    yield session_factory
    app.dependency_overrides.pop(get_db_session, None)
    await engine.dispose()


async def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _seed_submitted_entry_order(
    session_factory, *, symbol: str, trade_id: int | None
) -> uuid.UUID:
    async with session_factory() as session:
        trace_id = uuid.uuid4()
        signal_id = uuid.uuid4()
        session.add(
            Signal(
                id=signal_id,
                trace_id=trace_id,
                symbol=symbol,
                timeframe="1h",
                candle_ts=datetime.now(timezone.utc),
                action=Action.BUY.value,
                confidence=Decimal("0.8"),
                reasoning="seed",
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
            freqtrade_trade_id=trade_id,
            symbol=symbol,
            side="BUY",
            status=OrderStatus.SUBMITTED.value,
            requested_amount=Decimal("0.0083"),
            dry_run=True,
        )
        session.add(order)
        await session.commit()
        return order.id


async def test_rejects_invalid_secret(db_session_factory):
    async with await _client() as client:
        response = await client.post(
            "/webhooks/freqtrade",
            json={"event": "entry_fill", "trade_id": 1, "pair": "BTC/USDT", "secret": "wrong"},
        )
    assert response.status_code == 401


async def test_entry_fill_marks_order_filled_and_opens_position(db_session_factory):
    order_id = await _seed_submitted_entry_order(db_session_factory, symbol="BTC/USDT", trade_id=42)

    async with await _client() as client:
        response = await client.post(
            "/webhooks/freqtrade",
            json={
                "event": "entry_fill",
                "trade_id": 42,
                "pair": "BTC/USDT",
                "secret": WEBHOOK_SECRET,
                "open_rate": "60050.5",
                "amount": "0.0083",
                "open_date": "2026-07-15T13:00:15+00:00",
            },
        )
    assert response.status_code == 204

    async with db_session_factory() as session:
        order = await session.get(Order, order_id)
        assert order.status == OrderStatus.FILLED.value
        assert order.avg_price == Decimal("60050.5")

        position = (
            await session.execute(select(Position).where(Position.entry_order_id == order_id))
        ).scalar_one()
        assert position.status == PositionStatus.OPEN.value
        assert position.entry_price == Decimal("60050.5")


async def test_entry_fill_backfills_missing_trade_id(db_session_factory):
    order_id = await _seed_submitted_entry_order(
        db_session_factory, symbol="ETH/USDT", trade_id=None
    )

    async with await _client() as client:
        response = await client.post(
            "/webhooks/freqtrade",
            json={
                "event": "entry_fill",
                "trade_id": 77,
                "pair": "ETH/USDT",
                "secret": WEBHOOK_SECRET,
                "open_rate": "3000",
                "amount": "0.1",
                "open_date": "2026-07-15T13:00:15+00:00",
            },
        )
    assert response.status_code == 204

    async with db_session_factory() as session:
        order = await session.get(Order, order_id)
        assert order.freqtrade_trade_id == 77
        assert order.status == OrderStatus.FILLED.value


async def test_entry_fill_does_not_match_reused_trade_id_from_another_pair(
    db_session_factory,
):
    btc_order_id = await _seed_submitted_entry_order(
        db_session_factory, symbol="BTC/USDT", trade_id=1
    )
    sol_order_id = await _seed_submitted_entry_order(
        db_session_factory, symbol="SOL/USDT", trade_id=1
    )

    async with await _client() as client:
        response = await client.post(
            "/webhooks/freqtrade",
            json={
                "event": "entry_fill",
                "trade_id": 1,
                "pair": "SOL/USDT",
                "secret": WEBHOOK_SECRET,
                "open_rate": "75.7",
                "amount": "6.605",
                "open_date": "2026-07-17T01:13:32+00:00",
            },
        )
    assert response.status_code == 204

    async with db_session_factory() as session:
        btc_order = await session.get(Order, btc_order_id)
        sol_order = await session.get(Order, sol_order_id)
        assert btc_order.status == OrderStatus.SUBMITTED.value
        assert btc_order.filled_amount is None
        assert btc_order.avg_price is None
        assert sol_order.status == OrderStatus.FILLED.value
        assert sol_order.filled_amount == Decimal("6.605")
        assert sol_order.avg_price == Decimal("75.7")


async def test_exit_fill_closes_position(db_session_factory):
    entry_order_id = await _seed_submitted_entry_order(
        db_session_factory, symbol="BTC/USDT", trade_id=10
    )
    async with db_session_factory() as session:
        entry_order = await session.get(Order, entry_order_id)
        entry_order.status = OrderStatus.FILLED.value
        entry_order.filled_amount = Decimal("0.0083")
        entry_order.avg_price = Decimal("60000")
        await session.flush()
        position = Position(
            symbol="BTC/USDT",
            status=PositionStatus.OPEN.value,
            entry_order_id=entry_order.id,
            entry_price=Decimal("60000"),
            amount=Decimal("0.0083"),
        )
        session.add(position)
        await session.flush()
        exit_order = Order(
            trace_id=entry_order.trace_id,
            risk_decision_id=entry_order.risk_decision_id,
            freqtrade_trade_id=10,
            symbol="BTC/USDT",
            side="SELL",
            status=OrderStatus.SUBMITTED.value,
            requested_amount=Decimal("0.0083"),
            dry_run=True,
        )
        session.add(exit_order)
        await session.flush()
        position.exit_order_id = exit_order.id
        await session.commit()
        exit_order_id = exit_order.id
        position_id = position.id

    async with await _client() as client:
        response = await client.post(
            "/webhooks/freqtrade",
            json={
                "event": "exit_fill",
                "trade_id": 10,
                "pair": "BTC/USDT",
                "secret": WEBHOOK_SECRET,
                "close_rate": "61000",
                "amount": "0.0083",
                "profit_amount": "8.3",
                "profit_ratio": "0.0166",
                "close_date": "2026-07-15T15:00:00+00:00",
            },
        )
    assert response.status_code == 204

    async with db_session_factory() as session:
        exit_order = await session.get(Order, exit_order_id)
        assert exit_order.status == OrderStatus.FILLED.value
        position = await session.get(Position, position_id)
        assert position.status == PositionStatus.CLOSED.value
        assert position.pnl_usdt == Decimal("8.3")
        assert position.exit_price == Decimal("61000")


async def test_unknown_trade_id_is_a_noop(db_session_factory):
    async with await _client() as client:
        response = await client.post(
            "/webhooks/freqtrade",
            json={
                "event": "entry_fill",
                "trade_id": 999,
                "pair": "BTC/USDT",
                "secret": WEBHOOK_SECRET,
                "open_rate": "60000",
                "amount": "0.01",
            },
        )
    assert response.status_code == 204


async def test_unrecognized_event_is_a_noop(db_session_factory):
    async with await _client() as client:
        response = await client.post(
            "/webhooks/freqtrade",
            json={"event": "entry", "trade_id": 1, "pair": "BTC/USDT", "secret": WEBHOOK_SECRET},
        )
    assert response.status_code == 204
