import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from common.config import DatabaseSettings
from common.db.models import AuditEvent, Order, Position, RiskDecision, Signal
from common.enums import Action, AuditEventType, OrderStatus, PositionStatus, SignalStatus
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from risk_engine.app.reconciliation import reconcile_submitted_orders
from risk_engine.app.schemas import FreqtradeTrade

NOW = datetime(2026, 7, 15, 15, 0, tzinfo=timezone.utc)


class FakeFreqtradeClient:
    def __init__(self, trade: FreqtradeTrade) -> None:
        self.trade = trade

    async def get_trade(self, *, trade_id: int) -> FreqtradeTrade:
        assert trade_id == self.trade.trade_id
        return self.trade


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
    yield session_factory
    await engine.dispose()


async def _seed_order(session_factory, *, trade_id: int | None) -> uuid.UUID:
    async with session_factory() as session:
        trace_id = uuid.uuid4()
        signal = Signal(
            trace_id=trace_id,
            symbol="BTC/USDT",
            timeframe="1h",
            candle_ts=NOW - timedelta(hours=1),
            action=Action.BUY.value,
            confidence=Decimal("0.80"),
            reasoning="test",
            model_name="test:model",
            price=Decimal("60000"),
            atr_14=Decimal("500"),
            status=SignalStatus.CONSUMED.value,
        )
        session.add(signal)
        await session.flush()
        decision = RiskDecision(
            trace_id=trace_id,
            signal_id=signal.id,
            approved=True,
            position_size_usdt=Decimal("600"),
            position_size_base=Decimal("0.01"),
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
            symbol="BTC/USDT",
            side="BUY",
            status=OrderStatus.SUBMITTED.value,
            requested_amount=Decimal("0.01"),
            dry_run=True,
            created_at=NOW - timedelta(minutes=20),
        )
        session.add(order)
        await session.commit()
        return order.id


async def test_reconciles_stale_entry_and_opens_position(db_session_factory):
    order_id = await _seed_order(db_session_factory, trade_id=42)
    client = FakeFreqtradeClient(
        FreqtradeTrade(
            trade_id=42,
            pair="BTC/USDT",
            is_open=True,
            amount=Decimal("0.01"),
            open_rate=Decimal("60000"),
            open_date=NOW - timedelta(minutes=19),
        )
    )

    async with db_session_factory() as session:
        count = await reconcile_submitted_orders(
            session, client, now=NOW, stale_after=timedelta(minutes=10)
        )

    assert count == 1
    async with db_session_factory() as session:
        order = await session.get(Order, order_id)
        position = (
            await session.execute(select(Position).where(Position.entry_order_id == order_id))
        ).scalar_one()
        assert order.status == OrderStatus.FILLED.value
        assert position.status == PositionStatus.OPEN.value


async def test_missing_trade_id_creates_only_one_operator_alert(db_session_factory):
    order_id = await _seed_order(db_session_factory, trade_id=None)
    client = FakeFreqtradeClient(
        FreqtradeTrade(trade_id=42, pair="BTC/USDT", is_open=True)
    )

    async with db_session_factory() as session:
        await reconcile_submitted_orders(
            session, client, now=NOW, stale_after=timedelta(minutes=10)
        )
        await reconcile_submitted_orders(
            session, client, now=NOW, stale_after=timedelta(minutes=10)
        )

    async with db_session_factory() as session:
        events = (
            await session.execute(
                select(AuditEvent).where(
                    AuditEvent.event_type
                    == AuditEventType.RECONCILIATION_REQUIRED.value
                )
            )
        ).scalars().all()
        order = await session.get(Order, order_id)
        assert len(events) == 1
        assert order.status == OrderStatus.SUBMITTED.value


async def test_pair_mismatch_fails_closed_and_creates_operator_alert(db_session_factory):
    order_id = await _seed_order(db_session_factory, trade_id=1)
    client = FakeFreqtradeClient(
        FreqtradeTrade(
            trade_id=1,
            pair="SOL/USDT",
            is_open=True,
            amount=Decimal("6.605"),
            open_rate=Decimal("75.7"),
        )
    )

    async with db_session_factory() as session:
        count = await reconcile_submitted_orders(
            session, client, now=NOW, stale_after=timedelta(minutes=10)
        )

    assert count == 0
    async with db_session_factory() as session:
        order = await session.get(Order, order_id)
        event = (
            await session.execute(
                select(AuditEvent).where(
                    AuditEvent.event_type
                    == AuditEventType.RECONCILIATION_REQUIRED.value
                )
            )
        ).scalar_one()
        assert order.status == OrderStatus.SUBMITTED.value
        assert order.filled_amount is None
        assert order.avg_price is None
        assert event.payload["reason"] == "freqtrade_pair_mismatch"
