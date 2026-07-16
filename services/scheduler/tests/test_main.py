from datetime import datetime, timezone

import pytest
from common.config import SchedulerSettings
from scheduler.app.main import _cron_minute_field, build_scheduler


def test_build_scheduler_registers_one_hourly_job_per_symbol() -> None:
    settings = SchedulerSettings(
        symbols=["BTC/USDT", "ETH/USDT"],
        timeframe="1h",
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


def test_build_scheduler_defaults_to_5m_cadence() -> None:
    settings = SchedulerSettings(symbols=["BTC/USDT", "ETH/USDT"], candle_settle_second=15)

    scheduler = build_scheduler(settings)

    trigger = scheduler.get_job("closed-candle:BTC/USDT").trigger
    next_fire = trigger.get_next_fire_time(
        None, datetime(2026, 7, 15, 13, 32, tzinfo=timezone.utc)
    )
    assert next_fire == datetime(2026, 7, 15, 13, 35, 15, tzinfo=timezone.utc)


def test_build_scheduler_staggers_symbols_to_avoid_concurrent_llm_calls() -> None:
    settings = SchedulerSettings(
        symbols=["BTC/USDT", "ETH/USDT"],
        timeframe="5m",
        candle_settle_second=15,
        symbol_stagger_seconds=40,
    )

    scheduler = build_scheduler(settings)

    btc_trigger = scheduler.get_job("closed-candle:BTC/USDT").trigger
    eth_trigger = scheduler.get_job("closed-candle:ETH/USDT").trigger
    after = datetime(2026, 7, 15, 13, 32, tzinfo=timezone.utc)
    btc_fire = btc_trigger.get_next_fire_time(None, after)
    eth_fire = eth_trigger.get_next_fire_time(None, after)

    assert btc_fire == datetime(2026, 7, 15, 13, 35, 15, tzinfo=timezone.utc)
    assert eth_fire == datetime(2026, 7, 15, 13, 35, 55, tzinfo=timezone.utc)
    assert (eth_fire - btc_fire).total_seconds() == 40


@pytest.mark.parametrize(
    ("timeframe_seconds", "expected"),
    [(300, "*/5"), (600, "*/10"), (900, "*/15"), (1800, "*/30"), (3600, 0)],
)
def test_cron_minute_field_divides_the_hour(timeframe_seconds: int, expected: str | int) -> None:
    assert _cron_minute_field(timeframe_seconds) == expected


def test_cron_minute_field_rejects_timeframes_that_dont_divide_the_hour() -> None:
    with pytest.raises(ValueError):
        _cron_minute_field(7 * 60)


def test_build_scheduler_spreads_five_symbols_across_the_candle_period() -> None:
    settings = SchedulerSettings(
        symbols=["BTC/USDT", "ETH/USDT", "BNB/USDT", "USDC/USDT", "SOL/USDT"],
        timeframe="5m",
        candle_settle_second=15,
        symbol_stagger_seconds=40,
    )

    scheduler = build_scheduler(settings)

    # Reference point sits exactly on a candle-close boundary so each
    # symbol's next fire is unambiguously candle_close + its own offset
    # (candle_settle_second + index * symbol_stagger_seconds).
    after = datetime(2026, 7, 15, 13, 30, 0, tzinfo=timezone.utc)
    fires = [
        scheduler.get_job(f"closed-candle:{symbol}").trigger.get_next_fire_time(None, after)
        for symbol in settings.symbols
    ]

    assert fires == [
        datetime(2026, 7, 15, 13, 30, 15, tzinfo=timezone.utc),
        datetime(2026, 7, 15, 13, 30, 55, tzinfo=timezone.utc),
        datetime(2026, 7, 15, 13, 31, 35, tzinfo=timezone.utc),
        datetime(2026, 7, 15, 13, 32, 15, tzinfo=timezone.utc),
        datetime(2026, 7, 15, 13, 32, 55, tzinfo=timezone.utc),
    ]
    assert all(
        (later - earlier).total_seconds() == settings.symbol_stagger_seconds
        for earlier, later in zip(fires, fires[1:])
    )
    # Every symbol still fires again on the next 5m candle close, not just once.
    next_btc_fire = scheduler.get_job("closed-candle:BTC/USDT").trigger.get_next_fire_time(
        None, fires[-1]
    )
    assert next_btc_fire == datetime(2026, 7, 15, 13, 35, 15, tzinfo=timezone.utc)


def test_build_scheduler_rejects_stagger_overrunning_the_candle_period() -> None:
    settings = SchedulerSettings(
        symbols=["BTC/USDT", "ETH/USDT"],
        timeframe="5m",
        candle_settle_second=15,
        symbol_stagger_seconds=290,
    )

    with pytest.raises(ValueError):
        build_scheduler(settings)
