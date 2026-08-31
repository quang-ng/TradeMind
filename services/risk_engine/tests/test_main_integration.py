"""Integration tests for the order-submission wiring in main.py.

Unlike the rest of the risk_engine suite (pure functions over in-memory
fakes), this exercises real Postgres reads/writes — faking SQLAlchemy Core
`select().where(...)` statements convincingly isn't worth it. CI provides a
`postgres` service container (.github/workflows/ci.yml); locally this
skips gracefully if no Postgres is reachable (e.g. `make up` wasn't run).
"""

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest
from common.config import DatabaseSettings, RiskConfig
from common.db.models import AuditEvent, Order, Position, RiskDecision, Signal
from common.enums import Action, AuditEventType, OrderStatus, PositionStatus, SignalStatus
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
        await session.execute(
            text(
                "UPDATE system_state "
                "SET killswitch_enabled = false, consecutive_loss_reset_at = NULL"
            )
        )
        await session.commit()

    yield session_factory
    await engine.dispose()


def _mock_freqtrade(handler) -> FreqtradeClient:
    def with_balance(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/balance":
            return httpx.Response(
                200,
                json={
                    "currencies": [
                        {
                            "currency": "USDT",
                            "free": 9000,
                            "balance": 10000,
                            "used": 1000,
                            "stake": "USDT",
                        }
                    ],
                    "total": 10000,
                    "stake": "USDT",
                },
            )
        return handler(request)

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(with_balance), base_url="http://freqtrade-test"
    )
    return FreqtradeClient(http_client=http_client)


async def _seed_signal(
    session_factory,
    *,
    symbol: str,
    action: Action,
    setup_regime: str | None = None,
    trade_score: int | None = None,
) -> uuid.UUID:
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
                setup_regime=setup_regime,
                trade_score=trade_score,
            )
        )
        await session.commit()
    return signal_id


async def _seed_closed_position(
    session_factory,
    *,
    symbol: str,
    market_regime: str,
    trade_score: int,
    pnl_usdt: str,
    r_multiple: str,
) -> None:
    """A fully-linked closed position (signal → decision → order → position)
    so `common.performance_query.load_closed_trade_metrics` — and therefore
    the M5 expectancy filter — sees it as realized history."""
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
                confidence=Decimal("0.80"),
                reasoning="seed history",
                model_name="test:model",
                price=Decimal("60000"),
                atr_14=Decimal("500"),
                status=SignalStatus.CONSUMED.value,
                setup_regime=market_regime,
                trade_score=trade_score,
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
        entry_order = Order(
            trace_id=trace_id,
            risk_decision_id=decision.id,
            freqtrade_trade_id=None,
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
                status=PositionStatus.CLOSED.value,
                entry_order_id=entry_order.id,
                entry_price=Decimal("60000"),
                amount=Decimal("0.0083"),
                pnl_usdt=Decimal(pnl_usdt),
                pnl_pct=Decimal("0"),
                closed_at=datetime.now(timezone.utc),
                market_regime=market_regime,
                trade_score=trade_score,
                r_multiple=Decimal(r_multiple),
            )
        )
        await session.commit()


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

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read()
        body = json.loads(captured["body"])
        return httpx.Response(
            200,
            json={"trade_id": 99, "status": "ok", "enter_tag": body["entry_tag"]},
        )

    async with db_session_factory() as session:
        await process_signal(
            session,
            FakeRedis(),
            str(signal_id),
            RiskConfig(),
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
        assert decision.stop_loss_price is not None
        assert b'"entry_tag":"slpct:' in captured["body"]
        # Positive-expectancy plan M1: nominal/actual risk + stop distance
        # are persisted on every approved decision, not just used transiently.
        assert decision.stop_distance_pct is not None
        assert decision.nominal_risk_amount_usdt is not None
        assert decision.actual_risk_usdt is not None
        assert decision.actual_risk_usdt <= decision.nominal_risk_amount_usdt

        audit_event = (
            await session.execute(
                select(AuditEvent).where(
                    AuditEvent.trace_id == decision.trace_id,
                    AuditEvent.event_type == AuditEventType.RISK_APPROVED.value,
                )
            )
        ).scalar_one()
        # Compared with a tight tolerance, not exact equality: the audit
        # payload is written from the full-precision in-memory Decimal
        # (e.g. a repeating 500/60000 ATR ratio), while the Numeric(20,8)/
        # Numeric(10,6) columns round to their declared scale on the round
        # trip through Postgres — a real, expected precision difference,
        # not a bug.
        assert Decimal(audit_event.payload["actual_risk_usdt"]) == pytest.approx(
            decision.actual_risk_usdt, abs=Decimal("0.00000001")
        )
        assert Decimal(audit_event.payload["nominal_risk_amount_usdt"]) == pytest.approx(
            decision.nominal_risk_amount_usdt, abs=Decimal("0.00000001")
        )
        assert Decimal(audit_event.payload["stop_distance_pct"]) == pytest.approx(
            decision.stop_distance_pct, abs=Decimal("0.000001")
        )
        # Positive-expectancy plan M5 (D4): every evaluated entry carries the
        # Historical Expectancy Filter's shadow verdict. This signal has no
        # regime/score and no closed-trade history, so the filter abstains —
        # but the check itself is recorded from day one.
        expectancy_check = audit_event.payload["expectancy_check"]
        assert expectancy_check["decision"] == "INSUFFICIENT_DATA"
        assert expectancy_check["enforced"] is False
        assert expectancy_check["sample_size"] == 0


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


async def test_signal_is_rejected_without_order_when_live_balance_is_unavailable(
    db_session_factory,
):
    signal_id = await _seed_signal(db_session_factory, symbol="BTC/USDT", action=Action.BUY)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="balance unavailable")

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://freqtrade-test"
    )
    client = FreqtradeClient(http_client=http_client)

    async with db_session_factory() as session:
        await process_signal(
            session,
            FakeRedis(),
            str(signal_id),
            RiskConfig(),
            client,
        )

    async with db_session_factory() as session:
        decision = (
            await session.execute(select(RiskDecision).where(RiskDecision.signal_id == signal_id))
        ).scalar_one()
        assert decision.approved is False
        assert decision.rejection_reason == "FREQTRADE_BALANCE_UNAVAILABLE"
        assert decision.equity_snapshot_usdt is None
        orders = (
            await session.execute(select(Order).where(Order.risk_decision_id == decision.id))
        ).scalars().all()
        assert orders == []


# --- M5: Historical Expectancy Filter, end to end ------------------------


async def _seed_losing_history(session_factory, *, regime: str, score: int, n: int) -> None:
    for _ in range(n):
        await _seed_closed_position(
            session_factory,
            symbol="ETH/USDT",  # a different pair, so no cooldown/dup on BTC
            market_regime=regime,
            trade_score=score,
            pnl_usdt="-40",
            r_multiple="-0.8",
        )


async def test_negative_expectancy_setup_is_only_shadow_logged_while_filter_disabled(
    db_session_factory,
):
    await _seed_losing_history(db_session_factory, regime="mean_reversion", score=55, n=4)
    signal_id = await _seed_signal(
        db_session_factory,
        symbol="BTC/USDT",
        action=Action.BUY,
        setup_regime="mean_reversion",
        trade_score=55,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        return httpx.Response(
            200, json={"trade_id": 1, "status": "ok", "enter_tag": body["entry_tag"]}
        )

    async with db_session_factory() as session:
        await process_signal(
            session,
            FakeRedis(),
            str(signal_id),
            # filter still disabled; consecutive_loss_limit raised so the
            # seeded losing history doesn't trip that breaker instead and
            # mask what this test is about.
            RiskConfig(expectancy_min_sample_size=3, consecutive_loss_limit=10),
            _mock_freqtrade(handler),
        )

    async with db_session_factory() as session:
        decision = (
            await session.execute(select(RiskDecision).where(RiskDecision.signal_id == signal_id))
        ).scalar_one()
        assert decision.approved is True  # shadow mode never rejects
        audit = (
            await session.execute(
                select(AuditEvent).where(
                    AuditEvent.trace_id == decision.trace_id,
                    AuditEvent.event_type == AuditEventType.RISK_APPROVED.value,
                )
            )
        ).scalar_one()
        check = audit.payload["expectancy_check"]
        assert check["decision"] == "NEGATIVE_EXPECTANCY"
        assert check["enforced"] is False
        assert check["sample_size"] == 4
        assert check["setup_key"] == "mean_reversion | 40–69"


async def test_negative_expectancy_setup_is_rejected_once_filter_is_enabled(
    db_session_factory,
):
    await _seed_losing_history(db_session_factory, regime="mean_reversion", score=55, n=4)
    signal_id = await _seed_signal(
        db_session_factory,
        symbol="BTC/USDT",
        action=Action.BUY,
        setup_regime="mean_reversion",
        trade_score=55,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no order should be submitted for a rejected entry")

    async with db_session_factory() as session:
        await process_signal(
            session,
            FakeRedis(),
            str(signal_id),
            RiskConfig(
                expectancy_filter_enabled=True,
                expectancy_min_sample_size=3,
                consecutive_loss_limit=10,
            ),
            _mock_freqtrade(handler),
        )

    async with db_session_factory() as session:
        decision = (
            await session.execute(select(RiskDecision).where(RiskDecision.signal_id == signal_id))
        ).scalar_one()
        assert decision.approved is False
        assert decision.rejection_reason == "NEGATIVE_EXPECTANCY_SETUP"
        orders = (
            await session.execute(select(Order).where(Order.risk_decision_id == decision.id))
        ).scalars().all()
        assert orders == []
        audit = (
            await session.execute(
                select(AuditEvent).where(
                    AuditEvent.trace_id == decision.trace_id,
                    AuditEvent.event_type == AuditEventType.RISK_REJECTED.value,
                )
            )
        ).scalar_one()
        check = audit.payload["expectancy_check"]
        assert check["decision"] == "NEGATIVE_EXPECTANCY"
        assert check["enforced"] is True
