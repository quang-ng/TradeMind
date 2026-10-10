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

`weekly-private` instead defaults to the last completed scheduled weekly window;
`--end` backfills an explicit window, and `--preview` prints only private Markdown
without sending email, Telegram, or pushing Git.
"""

import argparse
import asyncio
from datetime import datetime, timezone

from common.config import DatabaseSettings, NotifierSettings
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from .archive_worker import publish_private_week
from .email_client import EmailClient
from .main import _send_daily_pnl_summary, _send_weekly_pnl_summary
from .private_report import build_private_report, render_private_report
from .public_report import (
    latest_weekly_end,
)
from .telegram_client import TelegramClient


async def _run(which: str, *, end: datetime | None = None, preview: bool = False) -> None:
    settings = NotifierSettings()
    engine = create_async_engine(DatabaseSettings().postgres_dsn)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    try:
        if which == "weekly-private":
            window_end = end or latest_weekly_end(now, settings)
            if preview:
                report = await build_private_report(session_factory, settings, window_end)
                print(render_private_report(report))
            elif not await publish_private_week(session_factory, settings, window_end):
                raise SystemExit("Private report failed; check configuration and logs")
        elif which == "weekly":
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
    parser.add_argument("which", choices=["daily", "weekly", "weekly-private"])
    parser.add_argument(
        "--end",
        type=datetime.fromisoformat,
        help="Private report window end (aware ISO 8601)",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Print private Markdown without posting",
    )
    args = parser.parse_args()
    if (args.end or args.preview) and args.which not in {"weekly-private"}:
        parser.error("--end and --preview are only available for weekly-private")
    if args.end and (args.end.tzinfo is None or args.end > datetime.now(timezone.utc)):
        parser.error("--end must include a timezone and must not be in the future")
    asyncio.run(_run(args.which, end=args.end, preview=args.preview))
