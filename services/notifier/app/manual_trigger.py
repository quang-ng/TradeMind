"""One-off CLI to send a summary immediately, without waiting for its
scheduled UTC weekday/hour — e.g. to verify SMTP/Telegram config right after
a deploy. Not part of the running service (`app.main`); run inside the
already-deployed notifier container:

    docker compose exec notifier python -m app.manual_trigger weekly
    docker compose exec notifier python -m app.manual_trigger daily

Uses real production settings (POSTGRES_DSN, SMTP_*, TELEGRAM_*) from the
container's own environment, so this sends a real email/Telegram message,
not a dry run. The window is always "the 7 (or 1) days ending right now" —
same trailing-window semantics the scheduled loops use, just triggered on
demand instead of at the configured time.
"""

import argparse
import asyncio
from datetime import datetime, timezone

from common.config import DatabaseSettings, NotifierSettings
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from .email_client import EmailClient
from .main import _send_daily_pnl_summary, _send_weekly_pnl_summary
from .telegram_client import TelegramClient


async def _run(which: str) -> None:
    settings = NotifierSettings()
    engine = create_async_engine(DatabaseSettings().postgres_dsn)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    try:
        if which == "weekly":
            await _send_weekly_pnl_summary(session_factory, EmailClient(settings), settings, now)
        else:
            telegram = TelegramClient(settings)
            try:
                await _send_daily_pnl_summary(session_factory, telegram, settings, now)
            finally:
                await telegram.aclose()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("which", choices=["daily", "weekly"])
    asyncio.run(_run(parser.parse_args().which))
