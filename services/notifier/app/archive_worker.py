"""Archive-only notifier entrypoint; no email, Telegram commands or trading writes."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from common.config import DatabaseSettings, NotifierSettings
from common.logging import configure_json_logging
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .private_report import build_private_report
from .private_report_client import PrivateReportClient
from .public_report import latest_weekly_end

logger = logging.getLogger(__name__)


async def publish_private_week(
    sessions: async_sessionmaker[AsyncSession],
    settings: NotifierSettings,
    end: datetime,
) -> bool:
    try:
        report = await build_private_report(sessions, settings, end)
        return await PrivateReportClient(settings).publish(report)
    except Exception:
        logger.warning("private_report_build_failed", extra={"window_end": end.isoformat()})
        return False


async def archive_loop(
    sessions: async_sessionmaker[AsyncSession],
    settings: NotifierSettings,
) -> None:
    if not settings.private_report_repository:
        return
    private_done: datetime | None = None
    while True:
        now = datetime.now(timezone.utc)
        end = latest_weekly_end(now, settings)
        if settings.private_report_repository and private_done != end:
            if await publish_private_week(sessions, settings, end):
                private_done = end
        # Retry private archive failures hourly; public Wiki publication is retired.
        delay = min(3600.0, max(1.0, (end + timedelta(days=7) - now).total_seconds()))
        await asyncio.sleep(delay)


async def run() -> None:
    configure_json_logging()
    settings = NotifierSettings()
    engine = create_async_engine(DatabaseSettings().postgres_dsn)
    try:
        await archive_loop(async_sessionmaker(engine, expire_on_commit=False), settings)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
