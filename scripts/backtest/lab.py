"""Strategy lab: does any pre-registered strategy have a real entry edge?

Runs every strategy in `lab_strategies.registry()` over cached Binance
history and compares it with a random-entry control that uses the same
stop/target/management (`lab_sim.random_control`). Two phases:

* `design` (default) — the period to explore and iterate on.
* `holdout` / `presample` — periods kept aside to judge a strategy *once*. Each
  holdout run is appended to `docs/research/strategy_lab_holdout_log.jsonl`
  (committed, so the record is auditable), and re-running a strategy that is
  already in the log is refused unless `--allow-rerun`, which is recorded
  too. Pass/fail is only printed for the holdout phase.

Pass criteria (pre-registered in
`docs/research/2026-10-10_strategy_lab_preregistration.md`), on holdout:
n >= 100 trades; week-clustered 95% CI of mean R entirely above 0; mean R
beats the random control's mean R by >= 0.15R; permutation p < 0.05
against the random runs; mean R > 0 in >= 75% of the period's quarters
(3 of 4 on the one-year holdout). `presample` (2020-01 -> 2024-10,
Appendix B) is a second one-look phase with the same criteria.

Usage:
    .venv/bin/python scripts/backtest/lab.py --phase design \\
        --report-out reports/lab/design.md
"""

import argparse
import json
import logging
import math
import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import _bootstrap  # noqa: F401,I001 -- must patch sys.path before the imports below
import numpy as np
import pandas as pd
from lab_data import add_indicators, load_candles, open_cache, warmup_start  # noqa: E402
from lab_sim import Trade, columns, period_mask, random_control, simulate  # noqa: E402
from lab_strategies import design_variants, label, registry  # noqa: E402

logger = logging.getLogger("backtest.lab")

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_DIR = Path(__file__).resolve().parent / ".data"
HOLDOUT_LOG = REPO_ROOT / "docs" / "research" / "strategy_lab_holdout_log.jsonl"
DEFAULT_SYMBOLS = "BTC,ETH,SOL,XRP,BNB,ADA,DOGE,LINK,AVAX,LTC"
PERIODS = {
    "design": ("2024-10-01", "2025-10-01"),
    "holdout": ("2025-10-01", "2026-10-01"),
    # Pre-registration Appendix B: untouched history before the design set.
    "presample": ("2020-01-01", "2024-10-01"),
}
# Phases judged once: logged, rerun refused, verdict printed.
ONE_LOOK_PHASES = frozenset({"holdout", "presample"})

MIN_TRADES = 100
MIN_EDGE_OVER_RANDOM_R = 0.15
MAX_P_VALUE = 0.05
MIN_POSITIVE_QUARTER_SHARE = 0.75  # 3 of 4 on a one-year period


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Evaluation:
    label: str
    n: int
    win_rate: float | None
    mean_r: float | None
    ci_low: float | None  # week-clustered
    ci_high: float | None
    total_r: float
    avg_hold_bars: float | None
    random_mean_r: float | None
    p_value: float | None
    quarters_positive: int
    quarters_total: int
    exits: dict[str, int]
    by_year: dict[int, tuple[int, float]]  # year -> (n, mean R), descriptive only

    @property
    def edge_r(self) -> float | None:
        if self.mean_r is None or self.random_mean_r is None:
            return None
        return self.mean_r - self.random_mean_r

    def verdict(self) -> tuple[bool, list[str]]:
        failures = []
        if self.n < MIN_TRADES:
            failures.append(f"n {self.n} < {MIN_TRADES}")
        if self.ci_low is None or self.ci_low <= 0:
            failures.append("CI of mean R not above 0")
        if self.edge_r is None or self.edge_r < MIN_EDGE_OVER_RANDOM_R:
            failures.append(f"edge over random < {MIN_EDGE_OVER_RANDOM_R}R")
        if self.p_value is None or self.p_value >= MAX_P_VALUE:
            failures.append(f"p >= {MAX_P_VALUE}")
        needed = math.ceil(MIN_POSITIVE_QUARTER_SHARE * self.quarters_total)
        if self.quarters_total == 0 or self.quarters_positive < needed:
            failures.append(f"positive quarters {self.quarters_positive} < {needed}")
        return not failures, failures


def clustered_ci(trades: Sequence[Trade]) -> tuple[float | None, float | None]:
    """95% CI of mean R with entry-week clusters: trades on many coins in
    the same week ride the same market move, so they are not independent."""
    if len(trades) < 2:
        return None, None
    weeks: dict[pd.Period, list[float]] = defaultdict(list)
    for t in trades:
        weeks[t.entry_time.tz_localize(None).to_period("W")].append(t.r)
    n = len(trades)
    mean = sum(t.r for t in trades) / n
    if len(weeks) < 2:
        return None, None
    g = len(weeks)
    resid = sum((sum(rs) - mean * len(rs)) ** 2 for rs in weeks.values())
    se = math.sqrt(resid * g / (g - 1)) / n
    return mean - 1.96 * se, mean + 1.96 * se


def evaluate(
    lab_label: str,
    trades: list[Trade],
    random_runs: list[list[Trade]],
) -> Evaluation:
    rs = [t.r for t in trades]
    mean = statistics.fmean(rs) if rs else None
    ci_low, ci_high = clustered_ci(trades)
    seed_means = [statistics.fmean(t.r for t in run) for run in random_runs if run]
    random_mean = statistics.fmean(seed_means) if seed_means else None
    p_value = None
    if mean is not None and seed_means:
        p_value = (1 + sum(m >= mean for m in seed_means)) / (1 + len(seed_means))
    quarters: dict[pd.Period, list[float]] = defaultdict(list)
    for t in trades:
        quarters[t.entry_time.tz_localize(None).to_period("Q")].append(t.r)
    exits: dict[str, int] = defaultdict(int)
    years: dict[int, list[float]] = defaultdict(list)
    for t in trades:
        exits[t.reason] += 1
        years[t.entry_time.year].append(t.r)
    return Evaluation(
        label=lab_label,
        n=len(rs),
        win_rate=sum(r > 0 for r in rs) / len(rs) if rs else None,
        mean_r=mean,
        ci_low=ci_low,
        ci_high=ci_high,
        total_r=sum(rs),
        avg_hold_bars=statistics.fmean(t.bars_held for t in trades) if trades else None,
        random_mean_r=random_mean,
        p_value=p_value,
        quarters_positive=sum(statistics.fmean(v) > 0 for v in quarters.values()),
        quarters_total=len(quarters),
        exits=dict(sorted(exits.items())),
        by_year={y: (len(v), statistics.fmean(v)) for y, v in sorted(years.items())},
    )


def run_strategy(
    strategy,
    frames: dict[str, pd.DataFrame],
    start: datetime,
    end: datetime,
    *,
    seeds: int,
    fee_pct: float,
) -> tuple[list[Trade], list[list[Trade]]]:
    trades: list[Trade] = []
    random_runs: list[list[Trade]] = [[] for _ in range(seeds)]
    for k, (symbol, df) in enumerate(sorted(frames.items())):
        cols = columns(df)
        in_period = period_mask(df, pd.Timestamp(start), pd.Timestamp(end))
        mask = np.asarray(strategy.entry_mask(cols), dtype=bool) & in_period
        trades += simulate(symbol, df, cols, strategy, mask, fee_pct=fee_pct)
        density = mask.sum() / max(in_period.sum(), 1)
        runs = random_control(
            symbol, df, cols, strategy, in_period, density,
            seeds=seeds, fee_pct=fee_pct, base_seed=1000 * k,
        )
        for s, run in enumerate(runs):
            random_runs[s] += run
    return trades, random_runs


def _f(value: float | None, spec: str) -> str:
    return "—" if value is None else format(value, spec)


def render(evals: list[Evaluation], *, phase: str, start: str, end: str, args) -> str:
    lines = [
        f"# Strategy lab — {phase} ({start} → {end})\n",
        f"Symbols: {args.symbols}. Fee {args.fee_pct} per leg. Random control: "
        f"{args.seeds} seeds per strategy (same stops/targets, random entry candles). "
        "CI is week-clustered.\n",
        "| strategy | n | win | mean R | 95% CI | total R | random mean R | edge | p | "
        "+quarters | avg hold (bars) |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for e in evals:
        ci = "—" if e.ci_low is None else f"[{e.ci_low:+.2f}, {e.ci_high:+.2f}]"
        lines.append(
            f"| {e.label} | {e.n} | {_f(e.win_rate, '.0%')} | {_f(e.mean_r, '+.3f')} | {ci} | "
            f"{e.total_r:+.1f} | {_f(e.random_mean_r, '+.3f')} | {_f(e.edge_r, '+.3f')} | "
            f"{_f(e.p_value, '.3f')} | {e.quarters_positive}/{e.quarters_total} | "
            f"{_f(e.avg_hold_bars, '.1f')} |"
        )
    lines.append("\n**Exit reasons**\n")
    for e in evals:
        lines.append(f"- {e.label}: " + ", ".join(f"{k}={v}" for k, v in e.exits.items()))
    lines.append("\n**Mean R by entry year** (descriptive)\n")
    for e in evals:
        lines.append(f"- {e.label}: " + ", ".join(
            f"{y}: {m:+.2f} (n={n})" for y, (n, m) in e.by_year.items()))
    if phase in ONE_LOOK_PHASES:
        lines.append("\n## Verdict (pre-registered criteria)\n")
        for e in evals:
            passed, failures = e.verdict()
            lines.append(f"- **{e.label}: {'PASS' if passed else 'FAIL'}**"
                         + ("" if passed else f" — {'; '.join(failures)}"))
    return "\n".join(lines) + "\n"


def _check_holdout_log(phase: str, labels: list[str], allow_rerun: bool) -> None:
    seen = set()
    if HOLDOUT_LOG.exists():
        for line in HOLDOUT_LOG.read_text().splitlines():
            if line.strip():
                entry = json.loads(line)
                if entry.get("phase", "holdout") == phase:
                    seen.update(entry["strategies"])
    already = sorted(set(labels) & seen)
    if already and not allow_rerun:
        raise SystemExit(
            f"{phase} already evaluated for {already}; it is a one-look test. "
            "Use a new strategy name, or --allow-rerun (it is logged)."
        )


def _append_holdout_log(evals: list[Evaluation], args, rerun: bool) -> None:
    HOLDOUT_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "phase": args.phase,
        "strategies": [e.label for e in evals],
        "symbols": args.symbols,
        "fee_pct": args.fee_pct,
        "rerun": rerun,
        "results": [
            {**{k: v for k, v in asdict(e).items() if k not in ("exits", "by_year")},
             "passed": e.verdict()[0]}
            for e in evals
        ],
    }
    with HOLDOUT_LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--phase", choices=sorted(PERIODS), default="design")
    parser.add_argument("--symbols", default=DEFAULT_SYMBOLS, help="Base assets, quoted in USDT")
    parser.add_argument("--strategies", default=None, help="Comma-separated labels, default all")
    parser.add_argument(
        "--variants", action="store_true", help="Include the Appendix A design variants"
    )
    parser.add_argument("--fee-pct", type=float, default=0.001)
    parser.add_argument("--seeds", type=int, default=100)
    parser.add_argument("--allow-rerun", action="store_true")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--report-out", default=None)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(message)s")

    strategies = registry() + (design_variants() if args.variants else [])
    if args.strategies:
        wanted = set(args.strategies.split(","))
        strategies = [s for s in strategies if label(s) in wanted]
    if not strategies:
        raise SystemExit("no strategies selected")
    if args.phase in ONE_LOOK_PHASES:
        _check_holdout_log(args.phase, [label(s) for s in strategies], args.allow_rerun)

    start_s, end_s = PERIODS[args.phase]
    start, end = _utc(start_s), _utc(end_s)
    data_end = datetime.now(timezone.utc)
    cache = open_cache(Path(args.cache_dir))
    frames: dict[str, dict[str, pd.DataFrame]] = defaultdict(dict)
    for tf in sorted({s.timeframe for s in strategies}):
        for base in args.symbols.split(","):
            symbol = f"{base}/USDT"
            # Data runs past `end` so trades opened near it can close.
            df = load_candles(cache, symbol, tf, warmup_start(start, tf), data_end)
            if df.empty:
                raise SystemExit(f"no cached candles for {symbol}; fetch them first")
            frames[tf][symbol] = add_indicators(df)
    cache.close()

    evals = []
    for strategy in strategies:
        logger.info("running %s", label(strategy))
        trades, random_runs = run_strategy(
            strategy, frames[strategy.timeframe], start, end,
            seeds=args.seeds, fee_pct=args.fee_pct,
        )
        evals.append(evaluate(label(strategy), trades, random_runs))

    report = render(evals, phase=args.phase, start=start_s, end=end_s, args=args)
    if args.phase in ONE_LOOK_PHASES:
        _append_holdout_log(evals, args, rerun=args.allow_rerun)
    if args.report_out:
        Path(args.report_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report_out).write_text(report)
    print(report)


if __name__ == "__main__":
    main()
