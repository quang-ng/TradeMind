"""Integration tests for the order-submission wiring in main.py.

Unlike the rest of the risk_engine suite (pure functions over in-memory
fakes), this exercises real Postgres reads/writes — faking SQLAlchemy Core
`select().where(...)` statements convincingly isn't worth it. CI provides a
`postgres` service container (.github/workflows/ci.yml); locally this
skips gracefully if no Postgres is reachable (e.g. `make up` wasn't run).
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest
from common.config import AccountSettings, DatabaseSettings, RiskConfig
from common.db.models import Order, Position, RiskDecision, Signal
from common.enums import Action, OrderStatus, PositionStatus, SignalStatus
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from risk_engine.app.freqtrade_client import FreqtradeClient
from risk_engine.app.main import process_signal


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    async def delete(self, key):
        self.store.pop(key, None)


@pytest.fixture
async def db_session_factory():
    # Function-scoped engine, matching pytest-asyncio's function-scoped
    # event loop — common.db.session.get_session_factory()'s lru_cache'd
    # engine is bound to whichever loop first touches it and breaks when
    # reused across tests running in separate loops.
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
        await session.execute(text("UPDATE system_state SET killswitch_enabled = false"))
        await session.commit()

    yield session_factory
    await engine.dispose()


def _mock_freqtrade(handler) -> FreqtradeClient:
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://freqtrade-test"
    )
    return FreqtradeClient(http_client=http_client)


async def _seed_signal(session_factory, *, symbol: str, action: Action) -> uuid.UUID:
    signal_id = uuid.uuid4()
    async with session_factory() as session:
        session.add(
            Signal(
                id=signal_id,
                trace_id=uuid.uuid4(),
                symbol=symbol,
                timeframe="1h",
                candle_ts=datetime.now(timezone.utc),
                action=action.value,
                confidence=Decimal("0.80"),
                reasoning="test",
                model_name="test:model",
                price=Decimal("60000"),
                atr_14=Decimal("500"),
                status=SignalStatus.PENDING.value,
            )
        )
        await session.commit()
    return signal_id


async def _seed_open_position(session_factory, *, symbol: str, freqtrade_trade_id: int) -> None:
    async with session_factory() as session:
        trace_id = uuid.uuid4()
        entry_signal_id = uuid.uuid4()
        session.add(
            Signal(
                id=entry_signal_id,
                trace_id=trace_id,
                symbol=symbol,
                timeframe="1h",
                candle_ts=datetime.now(timezone.utc),
                action=Action.BUY.value,
                confidence=Decimal("0.80"),
                reasoning="seed entry",
                model_name="test:model",
                price=Decimal("60000"),
                atr_14=Decimal("500"),
                status=SignalStatus.CONSUMED.value,
            )
        )
        await session.flush()
        decision = RiskDecision(
            trace_id=trace_id,
            signal_id=entry_signal_id,
            approved=True,
            position_size_usdt=Decimal("500"),
            position_size_base=Decimal("0.0083"),
            stop_loss_price=Decimal("59000"),
            equity_snapshot_usdt=Decimal("10000"),
            risk_pct_applied=Decimal("0.01"),
        )
        session.add(decision)
        await session.flush()
        entry_order = Order(
            trace_id=trace_id,
            risk_decision_id=decision.id,
            freqtrade_trade_id=freqtrade_trade_id,
            symbol=symbol,
            side="BUY",
            status=OrderStatus.FILLED.value,
            requested_amount=Decimal("0.0083"),
            filled_amount=Decimal("0.0083"),
            avg_price=Decimal("60000"),
            dry_run=True,
        )
        session.add(entry_order)
        await session.flush()
        session.add(
            Position(
                symbol=symbol,
                status=PositionStatus.OPEN.value,
                entry_order_id=entry_order.id,
                entry_price=Decimal("60000"),
                amount=Decimal("0.0083"),
            )
        )
        await session.commit()


async def test_entry_signal_approved_submits_forceenter_and_persists_order(db_session_factory):
    signal_id = await _seed_signal(db_session_factory, symbol="BTC/USDT", action=Action.BUY)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"trade_id": 99, "status": "ok"})

    async with db_session_factory() as session:
        await process_signal(
            session,
            FakeRedis(),
            str(signal_id),
            RiskConfig(),
            AccountSettings(),
            _mock_freqtrade(handler),
        )

    async with db_session_factory() as session:
        decision = (
            await session.execute(select(RiskDecision).where(RiskDecision.signal_id == signal_id))
        ).scalar_one()
        assert decision.approved is True
        order = (
            await session.execute(select(Order).where(Order.risk_decision_id == decision.id))
        ).scalar_one()
        assert order.status == OrderStatus.SUBMITTED.value
        assert order.freqtrade_trade_id == 99
        assert order.side == "BUY"


async def test_entry_signal_approved_but_freqtrade_unreachable_marks_order_failed(
    db_session_factory,
):
    signal_id = await _seed_signal(db_session_factory, symbol="BTC/USDT", action=Action.BUY)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with db_session_factory() as session:
        await process_signal(
            session,
            FakeRedis(),
            str(signal_id),
            RiskConfig(),
            AccountSettings(),
            _mock_freqtrade(handler),
        )

    async with db_session_factory() as session:
        decision = (
            await session.execute(select(RiskDecision).where(RiskDecision.signal_id == signal_id))
        ).scalar_one()
        # RiskDecision approval stands even though order submission failed.
        assert decision.approved is True
        order = (
            await session.execute(select(Order).where(Order.risk_decision_id == decision.id))
        ).scalar_one()
        assert order.status == OrderStatus.FAILED.value
        assert order.freqtrade_trade_id is None


async def test_sell_signal_with_open_position_submits_forceexit(db_session_factory):
    await _seed_open_position(db_session_factory, symbol="BTC/USDT", freqtrade_trade_id=7)
    signal_id = await _seed_signal(db_session_factory, symbol="BTC/USDT", action=Action.SELL)

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"trade_id": 7, "pair": "BTC/USDT", "is_open": True},
            )
        captured["body"] = request.read()
        return httpx.Response(200, json={"result": "Created exit order"})

    async with db_session_factory() as session:
        await process_signal(
            session,
            FakeRedis(),
            str(signal_id),
            RiskConfig(),
            AccountSettings(),
            _mock_freqtrade(handler),
        )

    assert b'"tradeid":"7"' in captured["body"]
    async with db_session_factory() as session:
        decision = (
            await session.execute(select(RiskDecision).where(RiskDecision.signal_id == signal_id))
        ).scalar_one()
        assert decision.approved is True
        exit_order = (
            await session.execute(select(Order).where(Order.risk_decision_id == decision.id))
        ).scalar_one()
        assert exit_order.side == "SELL"
        assert exit_order.status == OrderStatus.SUBMITTED.value
        assert exit_order.freqtrade_trade_id == 7

        position = (
            await session.execute(select(Position).where(Position.symbol == "BTC/USDT"))
        ).scalar_one()
        assert position.exit_order_id == exit_order.id
        assert position.status == PositionStatus.OPEN.value  # webhook confirms the close later


async def test_sell_signal_fails_closed_when_trade_id_belongs_to_another_pair(
    db_session_factory,
):
    await _seed_open_position(db_session_factory, symbol="BTC/USDT", freqtrade_trade_id=1)
    signal_id = await _seed_signal(db_session_factory, symbol="BTC/USDT", action=Action.SELL)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(
            200,
            json={"trade_id": 1, "pair": "SOL/USDT", "is_open": True},
        )

    async with db_session_factory() as session:
        await process_signal(
            session,
            FakeRedis(),
            str(signal_id),
            RiskConfig(),
            AccountSettings(),
            _mock_freqtrade(handler),
        )

    async with db_session_factory() as session:
        decision = (
            await session.execute(select(RiskDecision).where(RiskDecision.signal_id == signal_id))
        ).scalar_one()
        exit_order = (
            await session.execute(select(Order).where(Order.risk_decision_id == decision.id))
        ).scalar_one()
        position = (
            await session.execute(select(Position).where(Position.symbol == "BTC/USDT"))
        ).scalar_one()
        assert decision.approved is True
        assert exit_order.status == OrderStatus.FAILED.value
        assert position.exit_order_id is None


async def test_sell_signal_with_no_open_position_is_rejected_without_calling_freqtrade(
    db_session_factory,
):
    signal_id = await _seed_signal(db_session_factory, symbol="BTC/USDT", action=Action.SELL)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Freqtrade should not be called when there is nothing to exit")

    async with db_session_factory() as session:
        await process_signal(
            session,
            FakeRedis(),
            str(signal_id),
            RiskConfig(),
            AccountSettings(),
            _mock_freqtrade(handler),
        )

    async with db_session_factory() as session:
        decision = (
            await session.execute(select(RiskDecision).where(RiskDecision.signal_id == signal_id))
        ).scalar_one()
        assert decision.approved is False
        assert decision.rejection_reason == "NO_POSITION_TO_EXIT"
        orders = (
            await session.execute(select(Order).where(Order.risk_decision_id == decision.id))
        ).scalars().all()
        assert orders == []
