"""Issue #9: measure what the `TREND_FOLLOWING` BUY filter has cost or saved.

Measurement only — this script changes no trading code path. It answers:
"for every flat-position signal the filter blocked since its 2026-08-13
deploy, what would the trade have done?", and compares that with the
signals the same rubric let through over the same period.

Input is a JSONL export of live `signals` rows (one `row_to_json` object per
line, see `--help` epilog for the exact psql command). For each row the
script rebuilds the exact `MarketContext` the live pipeline saw from the
stored `model_input` (the full `/analyze` payload) and re-runs the live
rubric pieces — `StrategySelector` and `_entry_is_sufficient` — to put it
in one cohort:

* `blocked_trend_following` — no open position, the numeric entry bar
  passed, and the regime was `TREND_FOLLOWING`: the filter alone stopped it.
* `eligible` — no open position, entry bar passed, any other regime: the
  rubric would have let a model BUY through (control group).
* anything else (bar failed, position open) is counted but not simulated.

Re-deriving from `model_input` rather than trusting the stored reason text
matters: the pre-filter's HOLD reason names `trend_following` for every
TREND_FOLLOWING setup, including downtrends the rubric would have rejected
anyway (issue #27). Agreement between the two is reported.

Each blocked/eligible signal is then simulated as an isolated trade on
Binance history (`history.py`, cached): fill at the next candle's open,
`risk_engine` sizing/ATR stop via `Ledger.apply_entry`, the freqtrade exit
profile that was deployed at the signal's time (`EXIT_PROFILES`), and the
deterministic exit rubric every later candle (`mechanical_replay`'s
always-propose-SELL stub, incl. `hard_loss_cut_pct`). Results are reported
two ways:

* per-signal — every signal is its own trade (inflated n: consecutive
  hourly signals in one trend are the same bet);
* non-overlapping — per symbol and cohort, a signal is skipped while the
  previous simulated trade is still open. This is the honest n and the one
  the conclusion should rest on.

Usage:
    .venv/bin/python scripts/backtest/trend_filter_study.py \\
        --signals-jsonl reports/issue9/signals.jsonl \\
        --positions-jsonl reports/issue9/positions.jsonl \\
        --report-out reports/issue9/report.md \\
        --outcomes-out reports/issue9/outcomes.csv
"""

import argparse
import asyncio
import csv
import json
import logging
import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import _bootstrap  # noqa: F401,I001 -- must patch sys.path before the imports below
import pandas as pd
from cache import CandleCache  # noqa: E402
from common.config import LLMServiceSettings, RiskConfig, SchedulerSettings  # noqa: E402
from common.enums import PREFILTER_MODEL_NAME, Action  # noqa: E402
from history import fetch_history, timeframe_to_ms  # noqa: E402
from ledger import ExitProfile, Ledger  # noqa: E402
from llm_service.app.context.builder import ContextBuilder  # noqa: E402
from llm_service.app.models.market import MarketContext  # noqa: E402
from llm_service.app.models.strategy import StrategyName  # noqa: E402
from llm_service.app.models.wire import AnalyzeRequest  # noqa: E402
from llm_service.app.strategies.selector import StrategySelector  # noqa: E402
from llm_service.app.strategies.volatility_classifier import VolatilityClassifier  # noqa: E402
from llm_service.app.validators.semantic import _entry_is_sufficient  # noqa: E402
from mechanical_replay import build_context, mechanical_decision  # noqa: E402
from risk_engine.app.schemas import SignalView  # noqa: E402
from scheduler.app.indicators import compute_indicators  # noqa: E402

logger = logging.getLogger("backtest.trend_filter_study")

DEFAULT_CACHE_DIR = Path(__file__).resolve().parent / ".data"

BLOCKED = "blocked_trend_following"
ELIGIBLE = "eligible"
INSUFFICIENT = "entry_bar_failed"
IN_POSITION = "position_open"
SIMULATED_COHORTS = (BLOCKED, ELIGIBLE)


def _utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# Freqtrade exit profile live at each point in time, newest last. Boundaries
# are deploy times (UTC), approximated from commit + deploy notes:
# PR #18 "let winners run" (e1c98e1) deployed ~2026-08-31 03:30, its "0"
# tier walked back 18% -> 10% (9218fc5) ~2026-09-05 03:00, full revert
# (97c96d8) deployed 2026-09-08 ~19:33. ATR stop / hard_loss_cut were never
# part of these changes.
_PRE_PR18 = ExitProfile()
EXIT_PROFILES: tuple[tuple[datetime, str, ExitProfile], ...] = (
    (_utc("2026-01-01T00:00:00"), "pre_pr18", _PRE_PR18),
    (
        _utc("2026-08-31T03:30:00"),
        "pr18_x3",
        ExitProfile(
            minimal_roi={
                0: Decimal("0.18"), 240: Decimal("0.09"), 720: Decimal("0.06"),
                1440: Decimal("0.045"), 2880: Decimal("0.03"), 5760: Decimal("0.015"),
            },
            trailing_activation_pct=Decimal("0.045"),
            trailing_distance_pct=Decimal("0.027"),
        ),
    ),
    (
        _utc("2026-09-05T03:00:00"),
        "pr18_x3_roi0_10pct",
        ExitProfile(
            minimal_roi={
                0: Decimal("0.10"), 240: Decimal("0.09"), 720: Decimal("0.06"),
                1440: Decimal("0.045"), 2880: Decimal("0.03"), 5760: Decimal("0.015"),
            },
            trailing_activation_pct=Decimal("0.045"),
            trailing_distance_pct=Decimal("0.027"),
        ),
    ),
    (_utc("2026-09-08T19:33:00"), "reverted", _PRE_PR18),
)

# Data periods from issue #9's 2026-10-09 update. The outage window is
# excluded from every comparison (≈100% llm_timeout, then the switch).
QWEN_END = _utc("2026-09-07T00:00:00")
SONNET_START = _utc("2026-09-09T00:00:00")
# Sonnet window where min_confidence (0.8) sat above Sonnet's max (~0.78)
# and then Anthropic credit ran out: almost nothing executed, so the
# "executed" comparison must skip it — but it holds most blocked signals.
NO_EXECUTION_START = _utc("2026-09-11T00:00:00")
NO_EXECUTION_END = _utc("2026-09-26T00:00:00")


def exit_profile_at(ts: datetime) -> tuple[str, ExitProfile]:
    label, profile = EXIT_PROFILES[0][1], EXIT_PROFILES[0][2]
    for start, name, candidate in EXIT_PROFILES:
        if ts >= start:
            label, profile = name, candidate
    return label, profile


def period_of(ts: datetime) -> str:
    if ts < QWEN_END:
        return "qwen"
    if ts < SONNET_START:
        return "outage"
    return "sonnet"


@dataclass(frozen=True)
class StudySignal:
    """One live `signals` row, reduced to what the study needs."""

    id: str
    symbol: str
    timeframe: str
    candle_ts: datetime  # close time of the decision candle
    stored_action: str
    stored_reasoning: str
    model_name: str
    model_action: str | None  # what the LLM proposed, before semantic.py
    model_confidence: float | None  # the LLM's own confidence, pre-normalization
    price: Decimal
    atr_14: Decimal
    context: MarketContext


def _model_output(raw_response: dict | None) -> tuple[str | None, float | None]:
    """The model's own action/confidence. `semantic.py` caps the stored
    `confidence` at 0.64 when it suppresses a BUY, so the column can't be
    used to bucket blocked signals by confidence — the raw text can."""
    if not isinstance(raw_response, dict):
        return None, None
    action = raw_response.get("model_action")
    confidence = None
    raw_text = raw_response.get("raw")
    if isinstance(raw_text, str):
        try:
            parsed = json.loads(raw_text)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            value = parsed.get("confidence")
            if isinstance(value, int | float):
                confidence = float(value)
            action = action or parsed.get("action")
    return action, confidence


def parse_signal(row: dict) -> StudySignal | None:
    """None for rows that can't be rebuilt (no/invalid `model_input`)."""
    model_input = row.get("model_input")
    if not isinstance(model_input, dict):
        return None
    try:
        request = AnalyzeRequest.model_validate(
            {k: v for k, v in model_input.items() if k != "provider_override"}
        )
    except ValueError:
        return None
    model_action, model_confidence = _model_output(row.get("raw_response"))
    return StudySignal(
        id=str(row["id"]),
        symbol=row["symbol"],
        timeframe=row.get("timeframe") or request.timeframe,
        candle_ts=_utc(row["candle_ts"]),
        stored_action=row["action"],
        stored_reasoning=row.get("reasoning") or "",
        model_name=row.get("model_name") or "",
        model_action=model_action,
        model_confidence=model_confidence,
        price=Decimal(str(row["price"])),
        atr_14=Decimal(str(row["atr_14"])),
        context=ContextBuilder().build(request),
    )


def load_signals(path: Path) -> tuple[list[StudySignal], int]:
    signals: list[StudySignal] = []
    skipped = 0
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parsed = parse_signal(json.loads(line))
            if parsed is None:
                skipped += 1
            else:
                signals.append(parsed)
    signals.sort(key=lambda s: (s.candle_ts, s.symbol))
    return signals, skipped


@dataclass(frozen=True)
class Classification:
    cohort: str
    regime: str
    alternatives: tuple[str, ...]
    entry_confirmations: tuple[str, ...]
    ema_gap_pct: float
    volatility_regime: str
    source: str  # prefilter | llm | llm_failed
    stored_says_trend_following: bool


def classify(signal: StudySignal) -> Classification:
    context = signal.context
    selected = StrategySelector().select(context)
    confirmations = context.entry_confirmations
    if context.position.has_open_position:
        cohort = IN_POSITION
    elif not _entry_is_sufficient(confirmations):
        cohort = INSUFFICIENT
    elif selected.strategy == StrategyName.TREND_FOLLOWING:
        cohort = BLOCKED
    else:
        cohort = ELIGIBLE

    if signal.model_name == PREFILTER_MODEL_NAME:
        source = "prefilter"
    elif signal.model_action is not None:
        source = "llm"
    else:
        source = "llm_failed"  # timeout / provider error / invalid response
    return Classification(
        cohort=cohort,
        regime=selected.strategy.value,
        alternatives=tuple(a.value for a in selected.possible_alternatives),
        entry_confirmations=confirmations,
        ema_gap_pct=context.trend.ema_gap_pct,
        volatility_regime=VolatilityClassifier().classify(context).value,
        source=source,
        stored_says_trend_following="trend_following" in signal.stored_reasoning,
    )


@dataclass
class Outcome:
    signal: StudySignal
    classification: Classification
    exit_profile: str
    status: str  # closed | horizon | data_end | rejected | no_candles
    exit_reason: str = ""
    r_multiple: Decimal | None = None
    pnl_pct: Decimal | None = None
    hold_hours: float | None = None
    exit_time: datetime | None = None
    rejection: str = ""


class IndicatorCache:
    """`compute_indicators` over the `lookback` window ending at candle
    `i` — memoized because overlapping simulated trades revisit the same
    candles hundreds of times."""

    def __init__(self, candles_by_symbol: dict[str, list[dict]], lookback: int):
        self._candles = candles_by_symbol
        self._lookback = lookback
        self._cache: dict[tuple[str, int], dict] = {}

    def get(self, symbol: str, i: int) -> dict | None:
        if i - self._lookback + 1 < 0:
            return None
        key = (symbol, i)
        if key not in self._cache:
            window = self._candles[symbol][i - self._lookback + 1 : i + 1]
            self._cache[key] = compute_indicators(pd.DataFrame(window))
        return self._cache[key]


ExitDecider = Callable[[str, int, Decimal], bool]


def rubric_exit_decider(
    candles_by_symbol: dict[str, list[dict]],
    indicators: IndicatorCache,
    *,
    timeframe: str,
    llm_ohlcv_window: int,
    lookback: int,
    settings: LLMServiceSettings,
) -> ExitDecider:
    """True when the live exit rubric would SELL at the close of candle `i`,
    assuming the model proposes SELL whenever a position is open (the same
    assumption as `mechanical_replay`)."""
    tf_ms = timeframe_to_ms(timeframe)

    def decide(symbol: str, i: int, entry_price: Decimal) -> bool:
        values = indicators.get(symbol, i)
        if values is None:
            return False
        candles = candles_by_symbol[symbol]
        close = Decimal(str(candles[i]["c"]))
        context = build_context(
            symbol=symbol,
            timeframe=timeframe,
            candle_close_ts_ms=candles[i]["t"] + tf_ms,
            window=candles[i - lookback + 1 : i + 1],
            indicators=values,
            has_open_position=True,
            unrealized_pnl_pct=float((close - entry_price) / entry_price),
            llm_ohlcv_window=llm_ohlcv_window,
        )
        return mechanical_decision(context, settings).action == Action.SELL

    return decide


def simulate_trade(
    signal: StudySignal,
    classification: Classification,
    candles: Sequence[dict],
    *,
    timeframe: str,
    risk_config: RiskConfig,
    exit_decider: ExitDecider,
    max_hold_hours: int,
    fee_pct: Decimal,
) -> Outcome:
    """One isolated trade from `signal`, on a fresh `Ledger` (no portfolio
    state: the question is the setup's edge, not how sizing/exposure caps
    would have interacted with the real book)."""
    tf_ms = timeframe_to_ms(timeframe)
    label, profile = exit_profile_at(signal.candle_ts)
    outcome = Outcome(signal, classification, label, status="no_candles")
    entry_ms = int(signal.candle_ts.timestamp() * 1000)
    # Candle opening at the decision candle's close time = the fill candle.
    j = next((k for k, c in enumerate(candles) if c["t"] == entry_ms), None)
    if j is None:
        return outcome

    ledger = Ledger(
        starting_equity_usdt=Decimal("10000"), fee_pct=fee_pct, exit_profile=profile
    )
    view = SignalView(
        id=signal.id,
        symbol=signal.symbol,
        action=Action.BUY,
        confidence=Decimal("0.99"),
        candle_ts=signal.candle_ts,
        price=signal.price,
        atr_14=signal.atr_14,
    )
    result, position = ledger.apply_entry(
        signal.symbol, view, risk_config, signal.candle_ts, Decimal(str(candles[j]["o"]))
    )
    if position is None:
        outcome.status = "rejected"
        outcome.rejection = result.rejection_reason.value if result.rejection_reason else ""
        return outcome

    horizon = signal.candle_ts + timedelta(hours=max_hold_hours)
    trade = None
    status = "data_end"
    last_close_time = signal.candle_ts
    for k in range(j, len(candles)):
        close_time = datetime.fromtimestamp((candles[k]["t"] + tf_ms) / 1000, tz=timezone.utc)
        last_close_time = close_time
        trade = ledger.check_static_exit(signal.symbol, candles[k], close_time)
        if trade is not None:
            status = "closed"
            break
        if k + 1 < len(candles) and exit_decider(signal.symbol, k, position.entry_price):
            trade = ledger.apply_exit_signal(
                signal.symbol, replace(view, action=Action.SELL),
                risk_config, close_time, Decimal(str(candles[k + 1]["o"])),
            )[1]
            if trade is not None:
                status = "closed"
                break
        if close_time >= horizon:
            status = "horizon"
            break
    if trade is None:
        # Still open: mark to market at the last examined close.
        trade = ledger._record_close(
            position, last_close_time, Decimal(str(candles[k]["c"])), status
        )

    outcome.status = status
    outcome.exit_reason = trade.exit_reason
    outcome.r_multiple = trade.r_multiple
    outcome.pnl_pct = trade.pnl_pct
    outcome.exit_time = trade.exit_time
    outcome.hold_hours = (trade.exit_time - trade.entry_time).total_seconds() / 3600
    return outcome


def non_overlapping(outcomes: Iterable[Outcome]) -> list[Outcome]:
    """Per (cohort, symbol), keep a signal only if the previously kept
    simulated trade had already exited — one position per symbol, the way
    the live system itself trades."""
    kept: list[Outcome] = []
    busy_until: dict[tuple[str, str], datetime] = {}
    for o in sorted(outcomes, key=lambda o: o.signal.candle_ts):
        if o.r_multiple is None or o.exit_time is None:
            continue
        key = (o.classification.cohort, o.signal.symbol)
        if key in busy_until and o.signal.candle_ts < busy_until[key]:
            continue
        busy_until[key] = o.exit_time
        kept.append(o)
    return kept


@dataclass(frozen=True)
class RStats:
    n: int
    win_rate: float | None
    mean_r: float | None
    total_r: float
    sd_r: float | None
    ci95: tuple[float, float] | None


def r_stats(values: Sequence[float]) -> RStats:
    n = len(values)
    if n == 0:
        return RStats(0, None, None, 0.0, None, None)
    mean = statistics.fmean(values)
    sd = statistics.stdev(values) if n > 1 else None
    ci = None
    if sd is not None:
        half = 1.96 * sd / math.sqrt(n)
        ci = (mean - half, mean + half)
    return RStats(n, sum(v > 0 for v in values) / n, mean, sum(values), sd, ci)


def welch_t(a: Sequence[float], b: Sequence[float]) -> float | None:
    """Welch's t for mean(a) - mean(b); |t| < ~2 means indistinguishable
    from noise at n this small."""
    if len(a) < 2 or len(b) < 2:
        return None
    va, vb = statistics.variance(a) / len(a), statistics.variance(b) / len(b)
    if va + vb == 0:
        return None
    return (statistics.fmean(a) - statistics.fmean(b)) / math.sqrt(va + vb)


def _rs(outcomes: Iterable[Outcome]) -> list[float]:
    return [float(o.r_multiple) for o in outcomes if o.r_multiple is not None]


def _fmt(value: float | None, spec: str) -> str:
    return "—" if value is None else format(value, spec)


def _row(label: str, stats: RStats) -> str:
    ci = "—" if stats.ci95 is None else f"[{stats.ci95[0]:+.2f}, {stats.ci95[1]:+.2f}]"
    win = "—" if stats.win_rate is None else f"{stats.win_rate:.0%}"
    return (
        f"| {label} | {stats.n} | {win} | {_fmt(stats.mean_r, '+.3f')} | "
        f"{ci} | {stats.total_r:+.2f} |"
    )


_TABLE_HEAD = "| cohort | n | win | mean R | 95% CI | total R |\n|---|---|---|---|---|---|"


def _gap_bucket(gap: float) -> str:
    gap = abs(gap)
    if gap < 0.025:
        return "1.5–2.5%"
    if gap < 0.04:
        return "2.5–4%"
    return "≥4%"


def _confidence_bucket(o: Outcome) -> str:
    c = o.signal.model_confidence
    if o.classification.source != "llm" or c is None:
        return "(no LLM confidence)"
    if c < 0.6:
        return "<0.60"
    if c < 0.7:
        return "0.60–0.69"
    if c < 0.8:
        return "0.70–0.79"
    return "≥0.80"


def _model_proposal(o: Outcome) -> str:
    if o.classification.source != "llm":
        return f"({o.classification.source})"
    return f"model said {o.signal.model_action}"


BREAKDOWNS: tuple[tuple[str, Callable[[Outcome], str]], ...] = (
    ("period", lambda o: period_of(o.signal.candle_ts)),
    ("exit profile", lambda o: o.exit_profile),
    ("symbol", lambda o: o.signal.symbol),
    ("regime alternatives", lambda o: "+".join(o.classification.alternatives) or "(none)"),
    ("volatility regime", lambda o: o.classification.volatility_regime),
    ("EMA50/EMA200 gap", lambda o: _gap_bucket(o.classification.ema_gap_pct)),
    ("entry confirmations", lambda o: str(len(o.classification.entry_confirmations))),
    ("model proposal", _model_proposal),
    ("model confidence", _confidence_bucket),
)


def _breakdown_table(title: str, outcomes: list[Outcome], key: Callable[[Outcome], str]) -> str:
    groups: dict[str, list[Outcome]] = defaultdict(list)
    for o in outcomes:
        groups[f"{o.classification.cohort} · {key(o)}"].append(o)
    lines = [f"\n**{title}**\n", _TABLE_HEAD]
    for label in sorted(groups):
        lines.append(_row(label, r_stats(_rs(groups[label]))))
    return "\n".join(lines)


def _comparison(title: str, outcomes: list[Outcome]) -> str:
    blocked = _rs(o for o in outcomes if o.classification.cohort == BLOCKED)
    eligible = _rs(o for o in outcomes if o.classification.cohort == ELIGIBLE)
    t = welch_t(blocked, eligible)
    return "\n".join([
        f"\n### {title}\n",
        _TABLE_HEAD,
        _row(BLOCKED, r_stats(blocked)),
        _row(ELIGIBLE, r_stats(eligible)),
        f"\nWelch t (blocked − eligible mean R): {_fmt(t, '+.2f')}",
    ])


def _live_positions_section(path: Path | None) -> str:
    if path is None:
        return ""
    by_period: dict[str, list[float]] = defaultdict(list)
    by_regime: dict[str, list[float]] = defaultdict(list)
    with path.open() as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("r_multiple") is None or row.get("closed_at") is None:
                continue
            opened = _utc(row["opened_at"])
            label = period_of(opened)
            if label == "sonnet" and NO_EXECUTION_START <= opened < NO_EXECUTION_END:
                label = "sonnet (09-11→09-26, near-empty)"
            r = float(row["r_multiple"])
            by_period[label].append(r)
            by_regime[row.get("market_regime") or "(unclassified)"].append(r)
    lines = ["\n## Live executed positions (actual R, for reference)\n", _TABLE_HEAD]
    for label in sorted(by_period):
        lines.append(_row(f"live · {label}", r_stats(by_period[label])))
    lines.append("\n" + _TABLE_HEAD)
    for label in sorted(by_regime):
        lines.append(_row(f"live · {label}", r_stats(by_regime[label])))
    return "\n".join(lines)


def render_report(
    signals: list[StudySignal],
    classifications: dict[str, Classification],
    outcomes: list[Outcome],
    *,
    skipped_rows: int,
    positions_path: Path | None,
    max_hold_hours: int,
) -> str:
    cohort_counts = Counter(c.cohort for c in classifications.values())
    agreement = Counter(
        (c.cohort == BLOCKED, c.stored_says_trend_following, c.source)
        for c in classifications.values()
        if c.cohort != IN_POSITION
    )
    status_counts = Counter(o.status for o in outcomes)
    usable = [o for o in outcomes if o.r_multiple is not None]
    in_scope = [o for o in usable if period_of(o.signal.candle_ts) != "outage"]
    flat = non_overlapping(in_scope)
    sonnet_flat = [o for o in flat if period_of(o.signal.candle_ts) == "sonnet"]
    qwen_flat = [o for o in flat if period_of(o.signal.candle_ts) == "qwen"]

    first = signals[0].candle_ts.date() if signals else "—"
    last = signals[-1].candle_ts.date() if signals else "—"
    parts = [
        "# Issue #9 — TREND_FOLLOWING filter study\n",
        f"Signals: {len(signals)} rebuilt ({skipped_rows} skipped: no/invalid "
        "model_input or other timeframe), "
        f"{first} → {last}. Max simulated hold {max_hold_hours}h; the outage "
        "window (2026-09-07 → 09-09) is excluded from every R table.\n",
        "## Cohorts (re-derived from model_input)\n",
        "| cohort | signals |\n|---|---|",
        *(f"| {k} | {v} |" for k, v in sorted(cohort_counts.items())),
        "\n**Recomputed vs stored reason** (flat signals; key = recomputed blocked, "
        "stored reason mentions trend_following, source)\n",
        "| recomputed blocked | stored says TF | source | signals |\n|---|---|---|---|",
        *(f"| {a} | {b} | {c} | {n} |" for (a, b, c), n in sorted(agreement.items())),
        "\n**Simulation status** "
        + ", ".join(f"{k}={v}" for k, v in sorted(status_counts.items())),
        "\n## Headline — non-overlapping (honest n)",
        _comparison("Sonnet period (2026-09-09 →)", sonnet_flat),
        _comparison("qwen period (2026-08-13 → 09-07), reference only", qwen_flat),
        _comparison("All periods", flat),
        "\n## Per-signal (inflated n — every hourly signal its own trade)",
        _comparison("All periods, per-signal", in_scope),
        "\n## Breakdowns (non-overlapping)",
    ]
    parts.extend(_breakdown_table(title, flat, key) for title, key in BREAKDOWNS)
    exit_reasons = Counter((o.classification.cohort, o.exit_reason) for o in flat)
    parts.append("\n**Exit reasons (non-overlapping)**\n\n| cohort | exit | n |\n|---|---|---|")
    parts.extend(f"| {c} | {e} | {n} |" for (c, e), n in sorted(exit_reasons.items()))
    parts.append(_live_positions_section(positions_path))
    return "\n".join(parts) + "\n"


def write_outcomes_csv(path: Path, outcomes: list[Outcome]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "signal_id", "symbol", "candle_ts", "period", "cohort", "regime", "alternatives",
            "volatility_regime", "ema_gap_pct", "entry_confirmations", "source",
            "model_action", "model_confidence", "exit_profile", "status", "exit_reason",
            "exit_time", "hold_hours", "pnl_pct", "r_multiple", "rejection",
        ])
        for o in outcomes:
            c = o.classification
            writer.writerow([
                o.signal.id, o.signal.symbol, o.signal.candle_ts.isoformat(),
                period_of(o.signal.candle_ts), c.cohort, c.regime, "|".join(c.alternatives),
                c.volatility_regime, f"{c.ema_gap_pct:.4f}", "|".join(c.entry_confirmations),
                c.source, o.signal.model_action or "", o.signal.model_confidence or "",
                o.exit_profile, o.status, o.exit_reason,
                o.exit_time.isoformat() if o.exit_time else "",
                "" if o.hold_hours is None else f"{o.hold_hours:.1f}",
                "" if o.pnl_pct is None else f"{o.pnl_pct:.4f}",
                "" if o.r_multiple is None else f"{o.r_multiple:.4f}",
                o.rejection,
            ])


async def run(args: argparse.Namespace) -> None:
    signals, skipped = load_signals(Path(args.signals_jsonl))
    if args.since:
        since = _utc(args.since)
        signals = [s for s in signals if s.candle_ts >= since]
    # Off-cadence rows (e.g. a handful of 5m signals from a 2026-09-09
    # bring-up test) can't share one candle series with the live 1h ones.
    timeframe = args.timeframe
    other_timeframe = sum(s.timeframe != timeframe for s in signals)
    skipped += other_timeframe
    signals = [s for s in signals if s.timeframe == timeframe]
    if not signals:
        logger.warning("no_signals")
        return
    classifications = {s.id: classify(s) for s in signals}
    to_simulate = [s for s in signals if classifications[s.id].cohort in SIMULATED_COHORTS]
    tf_ms = timeframe_to_ms(timeframe)

    scheduler_settings = SchedulerSettings()
    lookback = scheduler_settings.candle_lookback
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    since_ms = int(signals[0].candle_ts.timestamp() * 1000) - (lookback + 2) * tf_ms
    until_ms = min(
        int(signals[-1].candle_ts.timestamp() * 1000) + (args.max_hold_hours + 2) * 3_600_000,
        now_ms - now_ms % tf_ms,
    )
    cache = CandleCache(Path(args.cache_dir) / "candles.sqlite")
    candles_by_symbol: dict[str, list[dict]] = {}
    for symbol in sorted({s.symbol for s in to_simulate}):
        logger.info("fetching_history symbol=%s", symbol)
        candles_by_symbol[symbol] = await fetch_history(
            symbol, timeframe, since_ms, until_ms, cache
        )
    cache.close()

    indicators = IndicatorCache(candles_by_symbol, lookback)
    decider = rubric_exit_decider(
        candles_by_symbol, indicators,
        timeframe=timeframe, llm_ohlcv_window=scheduler_settings.llm_ohlcv_window,
        lookback=lookback, settings=LLMServiceSettings(),
    )
    risk_config = RiskConfig()
    outcomes: list[Outcome] = []
    for n, signal in enumerate(to_simulate, start=1):
        outcomes.append(simulate_trade(
            signal, classifications[signal.id], candles_by_symbol[signal.symbol],
            timeframe=timeframe, risk_config=risk_config, exit_decider=decider,
            max_hold_hours=args.max_hold_hours, fee_pct=Decimal(args.fee_pct),
        ))
        if n % 100 == 0:
            logger.info("progress simulated=%d/%d", n, len(to_simulate))

    report = render_report(
        signals, classifications, outcomes, skipped_rows=skipped,
        positions_path=Path(args.positions_jsonl) if args.positions_jsonl else None,
        max_hold_hours=args.max_hold_hours,
    )
    if args.report_out:
        Path(args.report_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report_out).write_text(report)
    if args.outcomes_out:
        write_outcomes_csv(Path(args.outcomes_out), outcomes)
    print(report)


_EPILOG = """\
Export on the VPS (one JSON object per line; psql -At prints row_to_json
verbatim, unlike COPY which escapes backslashes):

  docker exec trademind-postgres-1 psql -U trademind -d trademind -At -c \\
    "SELECT row_to_json(s) FROM (SELECT id, symbol, timeframe, candle_ts, action,
       confidence, reasoning, model_name, raw_response, model_input, price, atr_14,
       setup_regime, volatility_regime, trade_score
     FROM signals WHERE candle_ts >= '2026-08-13' ORDER BY candle_ts) s" \\
    | gzip > /root/issue9_signals.jsonl.gz

  docker exec trademind-postgres-1 psql -U trademind -d trademind -At -c \\
    "SELECT row_to_json(p) FROM (SELECT symbol, opened_at, closed_at, r_multiple,
       pnl_usdt, exit_reason, market_regime, trade_score
     FROM positions WHERE opened_at >= '2026-08-13' ORDER BY opened_at) p" \\
    > /root/issue9_positions.jsonl
"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, epilog=_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--signals-jsonl", required=True)
    parser.add_argument("--positions-jsonl", default=None)
    parser.add_argument("--since", default=None, help="UTC date; drop earlier signals")
    parser.add_argument(
        "--timeframe", default="1h", help="Only signals on this timeframe are studied"
    )
    parser.add_argument("--max-hold-hours", type=int, default=336)
    parser.add_argument(
        "--fee-pct", default="0.001", help="Per-leg fee; matches RiskConfig.estimated_fee_pct"
    )
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--report-out", default=None)
    parser.add_argument("--outcomes-out", default=None)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
