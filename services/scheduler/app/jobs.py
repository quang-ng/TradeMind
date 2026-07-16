import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx
import pandas as pd
import redis.asyncio as redis
from common import redis_keys
from common.config import RedisSettings, SchedulerSettings
from common.db.models import Position, Signal
from common.db.session import get_session_factory
from common.enums import Action, PositionStatus, SignalStatus
from sqlalchemy import select

from .indicators import compute_indicators
from .market_data import fetch_closed_candles

logger = logging.getLogger(__name__)

_TIMEFRAME_SECONDS = {"1h": 3600}


async def run_cycle(
    symbol: str,
    *,
    redis_client: Any = None,
    session_factory: Any = None,
    http_client: Any = None,
    settings: SchedulerSettings | None = None,
) -> uuid.UUID | None:
    """PROJECT.md Section 5.1 steps 1-4: acquire the per-symbol lock and
    check the candle idempotency key, fetch closed candles and compute
    indicators, call the LLM service, persist `Signal` to Postgres, and
    publish to the Redis signals stream. Returns the minted `trace_id`, or
    `None` if the cycle was skipped (lock already held / candle already
    processed — Section 5.1's documented safe default)."""
    settings = settings or SchedulerSettings()
    owns_redis = redis_client is None
    redis_client = redis_client or redis.from_url(RedisSettings().redis_url, decode_responses=True)
    session_factory = session_factory or get_session_factory()

    lock_key = redis_keys.cycle_lock(symbol)
    acquired = await redis_client.set(lock_key, "1", nx=True, ex=redis_keys.CYCLE_LOCK_TTL_SECONDS)
    if not acquired:
        logger.info("cycle_skipped_lock_held", extra={"symbol": symbol})
        if owns_redis:
            await redis_client.aclose()
        return None

    try:
        return await _run_locked_cycle(
            symbol, redis_client=redis_client, session_factory=session_factory,
            http_client=http_client, settings=settings,
        )
    finally:
        await redis_client.delete(lock_key)
        if owns_redis:
            await redis_client.aclose()


async def _run_locked_cycle(
    symbol: str,
    *,
    redis_client: Any,
    session_factory: Any,
    http_client: Any,
    settings: SchedulerSettings,
) -> uuid.UUID | None:
    candles = await fetch_closed_candles(
        symbol, timeframe=settings.timeframe, limit=settings.candle_lookback
    )
    if len(candles) < 2:
        logger.warning("insufficient_candles", extra={"symbol": symbol})
        return None

    candle_ts_ms = candles[-1]["t"]
    idempotency_key = redis_keys.candle_idempotency(symbol, settings.timeframe, str(candle_ts_ms))
    timeframe_seconds = _TIMEFRAME_SECONDS[settings.timeframe]
    newly_claimed = await redis_client.set(idempotency_key, "1", nx=True, ex=2 * timeframe_seconds)
    if not newly_claimed:
        logger.info(
            "cycle_skipped_already_processed",
            extra={"symbol": symbol, "candle_ts": candle_ts_ms},
        )
        return None

    indicators = compute_indicators(pd.DataFrame(candles))
    latest = candles[-1]
    trace_id = uuid.uuid4()

    async with session_factory() as session:
        has_open_position = (
            await session.execute(
                select(Position.id).where(
                    Position.symbol == symbol, Position.status == PositionStatus.OPEN.value
                )
            )
        ).first() is not None

        llm_result = await _request_signal(
            http_client,
            settings,
            symbol=symbol,
            candle_ts_ms=candle_ts_ms,
            candles=candles,
            indicators=indicators,
            has_open_position=has_open_position,
        )

        signal_row = Signal(
            trace_id=trace_id,
            symbol=symbol,
            timeframe=settings.timeframe,
            candle_ts=datetime.fromtimestamp(candle_ts_ms / 1000, tz=timezone.utc),
            action=llm_result["action"],
            confidence=Decimal(str(llm_result["confidence"])),
            reasoning=llm_result["reasoning"],
            model_name=llm_result["model_name"],
            raw_response=llm_result.get("raw_response"),
            price=Decimal(str(latest["c"])),
            atr_14=Decimal(str(indicators["atr_14"])),
            status=SignalStatus.PENDING.value,
        )
        session.add(signal_row)
        await session.flush()
        await redis_client.xadd(
            redis_keys.SIGNALS_PENDING_STREAM, {"signal_id": str(signal_row.id)}
        )
        await session.commit()

    logger.info("cycle_completed", extra={"symbol": symbol, "trace_id": str(trace_id)})
    return trace_id


async def _request_signal(
    http_client: httpx.AsyncClient | None,
    settings: SchedulerSettings,
    *,
    symbol: str,
    candle_ts_ms: int,
    candles: list[dict],
    indicators: dict,
    has_open_position: bool,
) -> dict:
    """Calls the LLM Analysis Service's `/analyze` (PROJECT.md Section 8).
    The service itself always resolves model-level failures to a `HOLD`
    Signal (200 OK) — the only failure this needs to handle is the HTTP
    call itself failing (service down, timeout, malformed body), matching
    the sequence diagram's "LLM timeout / malformed JSON / provider error"
    branch from the Scheduler's point of view."""
    owns_client = http_client is None
    http_client = http_client or httpx.AsyncClient(timeout=settings.llm_request_timeout_seconds)
    payload = {
        "symbol": symbol,
        "timeframe": settings.timeframe,
        "candle_close_time": (
            datetime.fromtimestamp(candle_ts_ms / 1000, tz=timezone.utc).isoformat()
        ),
        "ohlcv": [
            {
                "t": datetime.fromtimestamp(c["t"] / 1000, tz=timezone.utc).isoformat(),
                "o": c["o"],
                "h": c["h"],
                "l": c["l"],
                "c": c["c"],
                "v": c["v"],
            }
            for c in candles[-settings.llm_ohlcv_window :]
        ],
        "indicators": indicators,
        "position_context": {"has_open_position": has_open_position, "unrealized_pnl_pct": None},
    }
    try:
        response = await http_client.post(settings.llm_service_url, json=payload)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("llm_service_unreachable", extra={"symbol": symbol, "error": str(exc)})
        return {
            "action": Action.HOLD.value,
            "confidence": 0.0,
            "reasoning": "llm_service_unreachable",
            "model_name": "n/a",
            "raw_response": None,
        }
    finally:
        if owns_client:
            await http_client.aclose()
