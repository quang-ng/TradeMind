from datetime import datetime, timezone

from common.config import SchedulerSettings
from scheduler.app.main import build_scheduler


def test_build_scheduler_registers_one_hourly_job_per_symbol() -> None:
    settings = SchedulerSettings(
        symbols=["BTC/USDT", "ETH/USDT"],
        candle_settle_second=15,
    )

    scheduler = build_scheduler(settings)

    jobs = {job.id: job for job in scheduler.get_jobs()}
    assert set(jobs) == {"closed-candle:BTC/USDT", "closed-candle:ETH/USDT"}
    trigger = jobs["closed-candle:BTC/USDT"].trigger
    next_fire = trigger.get_next_fire_time(
        None, datetime(2026, 7, 15, 13, 30, tzinfo=timezone.utc)
    )
    assert next_fire == datetime(2026, 7, 15, 14, 0, 15, tzinfo=timezone.utc)
