"""One-off experiment (NOT a production code path): compare exit-parameter
configurations for the "let winners run" / "Option B" question.

The live system's downside is currently pinned near -1.5% by three
overlapping mechanisms (ATR stop floored at `min_stop_loss_pct`, the SELL
rubric's `hard_loss_cut_pct`, and — rarely — the static -8% net) while its
upside is capped near +2% by the `minimal_roi` decay table and the
2%/1.5% trailing stop. Realized reward:risk is therefore ~1.2:1. This
script sweeps a handful of coherent alternatives over cached 1h history and
reports every metric through `common.performance` (same math as
`GET /performance`), on a full window plus a held-out recent window, so a
config that only looks good in-sample is visible.

    .venv/bin/python scripts/backtest/exit_tuning_matrix.py

Each config sets `RiskConfig` stop knobs via the environment (exactly how
`mechanical_replay` already reads them) and monkey-patches `ledger`'s
`MINIMAL_ROI` / trailing / static-stop module constants (which
`check_static_exit` reads as globals). Nothing here is imported by any
service.
"""

import argparse
import asyncio
import contextlib
import io
import os
from dataclasses import dataclass, field
from decimal import Decimal

import _bootstrap  # noqa: F401,I001 -- must patch sys.path before the imports below
import ledger as ledger_mod  # noqa: E402
import mechanical_replay as mr  # noqa: E402
import report as perf_report  # noqa: E402
from common.performance import summarize, summarize_breakdowns  # noqa: E402

SYMBOLS = "BTC/USDT,ETH/USDT,SOL/USDT,XRP/USDT"
TIMEFRAME = "1h"
STARTING_EQUITY = "625"
FEE_PCT = "0.001"  # per leg — matches live `estimated_fee_pct` round-trip of ~0.2%

WINDOWS = [
    ("FULL  2026-01-16..2026-08-31", "2026-01-16", "2026-08-31"),
    ("OOS   2026-05-15..2026-08-31", "2026-05-15", "2026-08-31"),
]


def _roi(scale: float, *, first: float | None = None) -> dict[int, Decimal]:
    """Scale the live `minimal_roi` decay table by `scale`; `first` overrides
    the 0-minute backstop tier explicitly when given."""
    base = {0: 0.06, 240: 0.03, 720: 0.02, 1440: 0.015, 2880: 0.01, 5760: 0.005}
    out = {k: Decimal(str(round(v * scale, 4))) for k, v in base.items()}
    if first is not None:
        out[0] = Decimal(str(first))
    return out


@dataclass(frozen=True)
class ExitConfig:
    name: str
    blurb: str
    # RiskConfig env knobs (stop geometry + the SELL-path hard loss cut)
    min_stop_loss_pct: str
    atr_stop_multiplier: str
    max_stop_loss_pct: str
    hard_loss_cut_pct: str
    # ledger module constants (static exits)
    minimal_roi: dict[int, Decimal]
    trailing_activation_pct: Decimal
    trailing_distance_pct: Decimal
    static_stoploss_pct: Decimal = Decimal("-0.08")


CONFIGS = [
    ExitConfig(
        name="C0-baseline",
        blurb="current live: stop~1.5%, ROI +2% @12h, trail 2%/1.5%, hard-cut 1.5%",
        min_stop_loss_pct="0.015",
        atr_stop_multiplier="2.0",
        max_stop_loss_pct="0.025",
        hard_loss_cut_pct="0.015",
        minimal_roi=_roi(1.0),
        trailing_activation_pct=Decimal("0.02"),
        trailing_distance_pct=Decimal("0.015"),
    ),
    ExitConfig(
        name="C1-run-winners",
        blurb="stop unchanged 1.5%; ROI x2 (+4% @12h), trail 3.5%/2%, hard-cut 1.5%",
        min_stop_loss_pct="0.015",
        atr_stop_multiplier="2.0",
        max_stop_loss_pct="0.025",
        hard_loss_cut_pct="0.015",
        minimal_roi=_roi(2.0),
        trailing_activation_pct=Decimal("0.035"),
        trailing_distance_pct=Decimal("0.02"),
    ),
    ExitConfig(
        name="C2-optionB-full",
        blurb="stop 3.5% (mult 2.5, cap 6%); ROI x2.3 (+4.6% @12h); trail 4.5%/2.8%; hard-cut 3.5%",
        min_stop_loss_pct="0.035",
        atr_stop_multiplier="2.5",
        max_stop_loss_pct="0.06",
        hard_loss_cut_pct="0.035",
        minimal_roi=_roi(2.3),
        trailing_activation_pct=Decimal("0.045"),
        trailing_distance_pct=Decimal("0.028"),
    ),
    ExitConfig(
        name="C3-optionB-mid",
        blurb="stop 2.5% (cap 4.5%); ROI x1.6 (+3.2% @12h); trail 3%/2%; hard-cut 2.5%",
        min_stop_loss_pct="0.025",
        atr_stop_multiplier="2.0",
        max_stop_loss_pct="0.045",
        hard_loss_cut_pct="0.025",
        minimal_roi=_roi(1.6),
        trailing_activation_pct=Decimal("0.03"),
        trailing_distance_pct=Decimal("0.02"),
    ),
    ExitConfig(
        name="C4-wide-stop-only",
        blurb="stop 3.5%, ROI/trail/hard-cut LEFT at live values (naive 'just widen the stop')",
        min_stop_loss_pct="0.035",
        atr_stop_multiplier="2.5",
        max_stop_loss_pct="0.06",
        hard_loss_cut_pct="0.015",
        minimal_roi=_roi(1.0),
        trailing_activation_pct=Decimal("0.02"),
        trailing_distance_pct=Decimal("0.015"),
    ),
]

_ENV_KEYS = (
    "MIN_STOP_LOSS_PCT",
    "ATR_STOP_MULTIPLIER",
    "MAX_STOP_LOSS_PCT",
    "HARD_LOSS_CUT_PCT",
    "RISK_PER_TRADE_PCT",
    "MAX_POSITION_PCT",
    "MAX_STOP_LOSS_PCT",
    "MAX_OPEN_POSITIONS",
    "MAX_TOTAL_EXPOSURE_PCT",
    "MIN_CONFIDENCE",
)


def _apply_env(cfg: ExitConfig) -> None:
    # live risk_config_state overrides that matter to sizing / gating
    os.environ["RISK_PER_TRADE_PCT"] = "0.01"
    os.environ["MAX_POSITION_PCT"] = "0.09"
    os.environ["MAX_OPEN_POSITIONS"] = "6"
    os.environ["MAX_TOTAL_EXPOSURE_PCT"] = "0.9"
    os.environ["MIN_CONFIDENCE"] = "0.5"
    os.environ["MIN_STOP_LOSS_PCT"] = cfg.min_stop_loss_pct
    os.environ["ATR_STOP_MULTIPLIER"] = cfg.atr_stop_multiplier
    os.environ["MAX_STOP_LOSS_PCT"] = cfg.max_stop_loss_pct
    os.environ["HARD_LOSS_CUT_PCT"] = cfg.hard_loss_cut_pct


def _apply_ledger(cfg: ExitConfig) -> None:
    ledger_mod.MINIMAL_ROI = dict(cfg.minimal_roi)
    ledger_mod.TRAILING_ACTIVATION_PCT = cfg.trailing_activation_pct
    ledger_mod.TRAILING_DISTANCE_PCT = cfg.trailing_distance_pct
    ledger_mod.STATIC_STOPLOSS_PCT = cfg.static_stoploss_pct


def _ns(start: str, end: str) -> argparse.Namespace:
    return argparse.Namespace(
        symbols=SYMBOLS,
        timeframe=TIMEFRAME,
        start=start,
        end=end,
        starting_equity=STARTING_EQUITY,
        fee_pct=FEE_PCT,
        slippage_pct="0.0005",
        compounding=False,
        suppress_buy_regimes=None,
        ignore_killswitch=True,
        cache_dir=mr.DEFAULT_CACHE_DIR,
        trades_out=None,
        signals_out=None,
        log_level="WARNING",
    )


@dataclass
class RunResult:
    config: str
    window: str
    n: int
    win_rate: float | None
    expectancy_r: float | None
    avg_win_r: float | None
    avg_loss_r: float | None
    total_r: float | None
    profit_factor: float | None
    total_pnl_usdt: float
    pnl_pct_equity: float
    max_dd_pct: float | None
    exit_reasons: dict[str, int] = field(default_factory=dict)
    by_regime: str = ""


async def _run_one(cfg: ExitConfig, window: str, start: str, end: str) -> RunResult:
    _apply_env(cfg)
    _apply_ledger(cfg)
    # mechanical_replay.run() still calls print_summary internally; silence it.
    with contextlib.redirect_stdout(io.StringIO()):
        ledger, candles_by_symbol, regimes, scores, vols = await mr.run(_ns(start, end))

    trades = ledger.closed_trades
    metrics = perf_report.metrics_from_closed_trades(
        trades, regimes=regimes, scores=scores, volatilities=vols
    )
    anchor = Decimal(STARTING_EQUITY)
    rep = summarize(metrics, starting_equity_usdt=anchor)
    breakdowns = summarize_breakdowns(metrics, starting_equity_usdt=anchor)

    reasons: dict[str, int] = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1

    def f(x):
        return None if x is None else float(x)

    return RunResult(
        config=cfg.name,
        window=window,
        n=rep.trades,
        win_rate=f(rep.win_rate),
        expectancy_r=f(rep.expectancy_r),
        avg_win_r=f(rep.avg_win_r),
        avg_loss_r=f(rep.avg_loss_r),
        total_r=f(rep.total_r),
        profit_factor=f(rep.profit_factor),
        total_pnl_usdt=float(rep.total_pnl_usdt),
        pnl_pct_equity=float(rep.total_pnl_usdt / anchor),
        max_dd_pct=f(rep.max_drawdown_pct),
        exit_reasons=reasons,
        by_regime=perf_report.render_breakdowns(breakdowns),
    )


def _fmt_pct(x, digits=1):
    return "  —  " if x is None else f"{x * 100:+.{digits}f}%"


def _fmt_r(x):
    return "  —  " if x is None else f"{x:+.2f}"


def _print_table(results: list[RunResult]) -> None:
    hdr = (
        f"{'config':<20} {'window':<8} {'n':>4} {'win%':>7} {'expR':>7} "
        f"{'avgW':>7} {'avgL':>7} {'totR':>8} {'PF':>6} {'pnl$':>9} {'pnl%eq':>8} {'maxDD':>8}"
    )
    print(hdr)
    print("-" * len(hdr))
    for r in results:
        win = None if r.win_rate is None else r.win_rate
        print(
            f"{r.config:<20} {r.window[:8]:<8} {r.n:>4} "
            f"{_fmt_pct(win):>7} {_fmt_r(r.expectancy_r):>7} "
            f"{_fmt_r(r.avg_win_r):>7} {_fmt_r(r.avg_loss_r):>7} "
            f"{_fmt_r(r.total_r):>8} "
            f"{'—' if r.profit_factor is None else format(r.profit_factor, '.2f'):>6} "
            f"{r.total_pnl_usdt:>9.2f} {_fmt_pct(r.pnl_pct_equity):>8} "
            f"{_fmt_pct(r.max_dd_pct, 2):>8}"
        )


def main() -> None:
    results: list[RunResult] = []
    for cfg in CONFIGS:
        print(f"\n### {cfg.name} — {cfg.blurb}", flush=True)
        for window, start, end in WINDOWS:
            res = asyncio.run(_run_one(cfg, window, start, end))
            results.append(res)
            reasons = ", ".join(f"{k}={v}" for k, v in sorted(res.exit_reasons.items()))
            print(
                f"  [{window}] n={res.n} exp={_fmt_r(res.expectancy_r)}R "
                f"totR={_fmt_r(res.total_r)} pnl=${res.total_pnl_usdt:.2f} "
                f"({_fmt_pct(res.pnl_pct_equity)} eq) | exits: {reasons}",
                flush=True,
            )

    print("\n\n================ SUMMARY ================\n")
    _print_table(results)

    print("\n\n================ PER-REGIME (FULL window) ================")
    for r in results:
        if r.window.startswith("FULL"):
            print(f"\n--- {r.config} ---")
            print(r.by_regime)


if __name__ == "__main__":
    main()
