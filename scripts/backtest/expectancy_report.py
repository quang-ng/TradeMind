"""Expectancy report — regime / volatility / score-bucket breakdowns over a
closed-trade set, via `services/common/performance.py` (positive-expectancy
plan M4).

Two input modes, same output:

    # over a mechanical_replay run
    .venv/bin/python scripts/backtest/expectancy_report.py \\
        --trades-csv reports/mechanical/trades.csv

    # over the live journal (reads Postgres — DATABASE__POSTGRES_DSN / the
    # DatabaseSettings default, same as the services)
    .venv/bin/python scripts/backtest/expectancy_report.py --live \\
        --since 2026-07-27 --symbol BTC/USDT

Because every number goes through the exact functions
`GET /performance` calls, a backtest expectancy table and the live one are
the same computation on different rows (implementation plan Section 7).
`--starting-equity` anchors the drawdown curve; omit it and the two
drawdown figures come back blank, every other metric is unaffected.
"""

import argparse
import asyncio
import csv
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import _bootstrap  # noqa: F401,I001 -- must patch sys.path before the imports below
import report as perf_report  # noqa: E402
from common.db.session import get_session_factory  # noqa: E402
from common.performance import ClosedTradeMetrics, summarize, summarize_breakdowns  # noqa: E402
from common.performance_query import load_closed_trade_metrics  # noqa: E402

_UNKNOWN_REGIME = "unknown"


def _decimal_or_none(value: str | None) -> Decimal | None:
    value = (value or "").strip()
    return Decimal(value) if value else None


def _int_or_none(value: str | None) -> int | None:
    value = (value or "").strip()
    return int(value) if value else None


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def metrics_from_csv(path: Path) -> list[ClosedTradeMetrics]:
    """Read a `mechanical_replay.py --trades-out` CSV into Performance
    Engine rows. Tolerates pre-M4 CSVs that lack the
    `r_multiple`/`volatility_regime`/`score` columns (those trades just
    carry `None` on the missing dimensions)."""
    rows: list[ClosedTradeMetrics] = []
    with open(path, newline="") as handle:
        for raw in csv.DictReader(handle):
            regime = (raw.get("entry_regime") or "").strip()
            volatility = (raw.get("volatility_regime") or "").strip()
            rows.append(
                ClosedTradeMetrics(
                    pnl_usdt=Decimal(raw["pnl_usdt"]),
                    r_multiple=_decimal_or_none(raw.get("r_multiple")),
                    fees_usdt=None,  # netted into pnl_usdt by the ledger
                    closed_at=_parse_dt(raw["exit_time"]),
                    market_regime=None if regime in ("", _UNKNOWN_REGIME) else regime,
                    volatility_regime=volatility or None,
                    trade_score=_int_or_none(raw.get("score")),
                )
            )
    return rows


async def metrics_from_live(
    *, symbol: str | None, since: datetime | None, until: datetime | None
) -> list[ClosedTradeMetrics]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        return await load_closed_trade_metrics(
            session, symbol=symbol, since=since, until=until
        )


def render(metrics: list[ClosedTradeMetrics], *, starting_equity: Decimal | None) -> str:
    report = summarize(metrics, starting_equity_usdt=starting_equity)
    breakdowns = summarize_breakdowns(metrics, starting_equity_usdt=starting_equity)
    return "\n".join(
        [
            perf_report.render_summary(report),
            perf_report.render_breakdowns(breakdowns),
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--trades-csv", type=Path, help="a mechanical_replay --trades-out file")
    source.add_argument("--live", action="store_true", help="read the live positions journal")
    parser.add_argument("--symbol", default=None, help="live mode: restrict to one pair")
    parser.add_argument("--since", default=None, help="live mode: closed-at lower bound (ISO)")
    parser.add_argument("--until", default=None, help="live mode: closed-at upper bound (ISO)")
    parser.add_argument(
        "--starting-equity",
        default=None,
        help="anchors the drawdown curve; omit to leave both drawdown figures blank",
    )
    args = parser.parse_args()

    starting_equity = _decimal_or_none(args.starting_equity)
    if args.live:
        metrics = asyncio.run(
            metrics_from_live(
                symbol=args.symbol,
                since=_parse_dt(args.since) if args.since else None,
                until=_parse_dt(args.until) if args.until else None,
            )
        )
    else:
        metrics = metrics_from_csv(args.trades_csv)

    print(f"\n=== Expectancy report ({len(metrics)} closed trades) ===")
    print(render(metrics, starting_equity=starting_equity))


if __name__ == "__main__":
    main()
