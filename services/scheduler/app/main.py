import asyncio
import logging
from datetime import timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from common.config import SchedulerSettings
from common.logging import configure_json_logging

from .jobs import run_cycle

configure_json_logging()
logger = logging.getLogger(__name__)


async def _run_scheduled_cycle(symbol: str) -> None:
    try:
        await run_cycle(symbol)
    except Exception:
        # A failed cycle must not stop future schedules. The underlying
        # dependency failure remains fail-closed: no signal means no order.
        logger.exception("scheduled_cycle_failed", extra={"symbol": symbol})


def build_scheduler(settings: SchedulerSettings | None = None) -> AsyncIOScheduler:
    settings = settings or SchedulerSettings()
    scheduler = AsyncIOScheduler(
        timezone=timezone.utc,
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 120},
    )
    for symbol in settings.symbols:
        scheduler.add_job(
            _run_scheduled_cycle,
            trigger=CronTrigger(
                minute=0,
                second=settings.candle_settle_second,
                timezone=timezone.utc,
            ),
            args=[symbol],
            id=f"closed-candle:{symbol}",
            name=f"TradeMind closed 1h candle cycle for {symbol}",
            replace_existing=True,
        )
    return scheduler


async def _health_server(port: int) -> asyncio.Server:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=2.0)
            path = request_line.decode(errors="replace").split(" ")[1]
            status = "200 OK" if path == "/health" else "404 Not Found"
            body = b'{"status":"ok"}' if path == "/health" else b'{"detail":"not found"}'
            writer.write(
                f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        except (IndexError, TimeoutError):
            logger.warning("scheduler_health_request_invalid")
        finally:
            writer.close()
            await writer.wait_closed()

    return await asyncio.start_server(handle, host="0.0.0.0", port=port)


async def run_scheduler() -> None:
    settings = SchedulerSettings()
    scheduler = build_scheduler(settings)
    health_server = await _health_server(settings.scheduler_health_port)
    scheduler.start()
    logger.info(
        "scheduler_started",
        extra={
            "symbols": settings.symbols,
            "timeframe": settings.timeframe,
            "settle_second": settings.candle_settle_second,
        },
    )
    try:
        await asyncio.Event().wait()
    finally:
        scheduler.shutdown(wait=False)
        health_server.close()
        await health_server.wait_closed()


if __name__ == "__main__":
    asyncio.run(run_scheduler())
