from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from common.config import NotifierSettings
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from notifier.app.public_report import (
    OperationsStats,
    PublicStats,
    PublicWeeklyReport,
    build_public_report,
    latest_weekly_end,
    publish_weekly_report,
    render_public_report,
)

END = datetime(2026, 10, 5, 8, tzinfo=timezone.utc)


def stats(**overrides):
    return PublicStats(**{
        "trades": 4, "recorded_pnl": 3, "wins": 1, "losses": 1,
        "return_samples": 2, "mean_trade_return": Decimal("0.0125"),
        "total_pnl_usdt": Decimal("-1.2345"), **overrides,
    })


def report():
    return PublicWeeklyReport(
        window_end=END, current=stats(), previous=stats(),
        operations=OperationsStats(
            signals=8, order_failures=1, reconciliation_alerts=2, llm_stall_alerts=3,
            llm_recoveries=1, killswitch_activations=1, config_changes=2,
        ),
    )


def test_public_model_rejects_private_fields():
    for key in (
        "equity_usdt", "pnl_usdt", "dry_run_trades", "live_trades",
        "symbol", "payload", "reasoning", "token",
    ):
        with pytest.raises(ValidationError):
            PublicWeeklyReport.model_validate({**report().model_dump(), key: "SECRET"})
        with pytest.raises(ValidationError):
            stats(**{key: "SECRET"})
    with pytest.raises(ValidationError):
        stats(mean_trade_return=Decimal("NaN"))


def test_rendering_labels_missing_data_denominators_modes_and_window():
    text = render_public_report(report())
    assert "2026-09-28 08:00 → 2026-10-05 08:00 UTC" in text
    assert "33.33%" in text  # 1 win / 3 recorded outcomes, not all 4 positions
    assert "1.25%" in text
    assert "không phải lợi nhuận % của tài khoản" in text
    assert "Giao dịch thiếu P&L | 1 | 1" in text
    assert "Lãi/lỗ đã chốt (USDT) | -1.2345 | -1.2345" in text
    assert "Thắng / thua / hòa | 1 / 1 / 1" in text
    assert "dry-run" not in text and "live" not in text
    assert f"({report().page_name}-charts.png)" in text


def test_empty_data_is_not_zero_return():
    empty = stats(trades=0, recorded_pnl=0, wins=0, losses=0, return_samples=0,
                  mean_trade_return=None, total_pnl_usdt=None)
    text = render_public_report(report().model_copy(update={"current": empty}))
    assert "Lãi/lỗ % trung bình mỗi giao dịch | Không đủ dữ liệu" in text
    assert "Tỷ lệ thắng (trên giao dịch có P&L) | Không đủ dữ liệu" in text


def test_schedule_includes_boundary_and_normalizes_timezone():
    settings = NotifierSettings()
    assert latest_weekly_end(END, settings) == END
    assert latest_weekly_end(END - timedelta(seconds=1), settings) == END - timedelta(days=7)
    local = END.astimezone(timezone(timedelta(hours=7)))
    assert latest_weekly_end(local, settings) == END
    assert report().model_copy().page_name == "Weekly-2026-10-05-080000"
    with pytest.raises(ValueError):
        latest_weekly_end(datetime(2026, 10, 5), settings)
    with pytest.raises(ValidationError):
        NotifierSettings(weekly_pnl_report_weekday_utc=7)


async def test_queries_use_only_aggregates_and_half_open_windows():
    session = AsyncMock()
    result = MagicMock()
    result.mappings.return_value.one.side_effect = [
        stats().model_dump(), stats().model_dump(),
        report().operations.model_dump(exclude={"signals"}),
    ]
    session.execute.return_value = result
    session.scalar.return_value = 8
    factory = MagicMock()
    factory.return_value.__aenter__.return_value = session
    built = await build_public_report(factory, END)
    assert built == report()
    queries = [call.args[0] for call in session.execute.call_args_list]
    sql = [str(q.compile(dialect=postgresql.dialect())) for q in queries]
    assert "positions.closed_at >=" in sql[0] and "positions.closed_at <" in sql[0]
    params = queries[0].compile().params
    assert END in params.values() and END-timedelta(days=7) in params.values()
    assert END-timedelta(days=14) in queries[1].compile().params.values()
    for statement in sql:
        for forbidden in ("symbol", "payload", "raw_response", "entry_price", "amount"):
            assert forbidden not in statement
    assert "avg(positions.pnl_pct)" in sql[0]
    assert "sum(positions.pnl_usdt) AS total_pnl_usdt" in sql[0]
    assert "orders" not in sql[0]


async def test_db_failure_does_not_publish_or_log_secrets(monkeypatch, caplog):
    from notifier.app import public_report
    from notifier.app.wiki_client import WikiClient

    monkeypatch.setattr(public_report, "build_public_report",
                        AsyncMock(side_effect=RuntimeError("SECRET postgres://password")))
    publish = AsyncMock()
    monkeypatch.setattr(WikiClient, "publish", publish)
    assert not await publish_weekly_report(
        MagicMock(), NotifierSettings(wiki_report_repository="dqflow/TradeMind"), END,
    )
    publish.assert_not_called()
    assert "SECRET" not in caplog.text and "password" not in caplog.text


async def test_unconfigured_archive_does_not_query_db():
    factory = MagicMock()
    assert not await publish_weekly_report(factory, NotifierSettings(), END)
    factory.assert_not_called()
