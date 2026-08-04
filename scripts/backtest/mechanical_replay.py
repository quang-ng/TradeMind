"""Stage A (mechanical variant): deterministic-rubric-only historical replay.

Like `replay.py`, but skips the live LLM call entirely. Instead of asking a
model what to do, it always proposes the "natural" action for the current
position state (BUY when flat, SELL when in a position) and lets the exact
same deterministic validator that gates every real trade
(`llm_service/app/validators/semantic.py::validate_signal_semantics`) decide
whether that action survives. This isolates the entry/exit confirmation
rubric's own edge — including `hard_loss_cut_pct` and the Strategy
Selector's regime classification — from whatever a real model would or
wouldn't have proposed on top of it. No Docker/Ollama/API key needed.
Naming mirrors `tools/historical_replay`'s "mechanical" replay for the same
reason: this validates the rubric's mechanics, not the LLM's judgment.

Every signal is tagged with the Strategy Selector's regime label
(`strategy_selected`) — the same field `SignalGenerator` attaches to
`Signal.raw_response` in production — so `--trades-out` can be sliced by
regime to test whether one (e.g. `trend_following`) systematically
under/over-performs.

Usage:
    .venv/bin/python scripts/backtest/mechanical_replay.py \\
        --symbols BTC/USDT,ETH/USDT,SOL/USDT,XRP/USDT,BNB/USDT --timeframe 1h \\
        --start 2026-02-01 --end 2026-08-04 \\
        --trades-out reports/mechanical/trades.csv

RiskConfig and LLMServiceSettings knobs (RISK_PER_TRADE_PCT,
MAX_OPEN_POSITIONS, HARD_LOSS_CUT_PCT, ...) load from the environment
exactly like the live services — set them the same way before running to
test a specific configuration.
"""

import argparse
import asyncio
import csv
import logging
from datetime import datetime, timezone
from decimal import Decimal
from itertools import groupby
from pathlib import Path

import _bootstrap  # noqa: F401,I001 -- must patch sys.path before the imports below
import pandas as pd
from cache import CandleCache  # noqa: E402
from common.config import LLMServiceSettings, RiskConfig, SchedulerSettings  # noqa: E402
from common.enums import Action  # noqa: E402
from history import fetch_history, timeframe_to_ms  # noqa: E402
from ledger import Ledger  # noqa: E402
from llm_service.app.context.builder import ContextBuilder  # noqa: E402
from llm_service.app.models.llm import LLMOutput  # noqa: E402
from llm_service.app.models.wire import AnalyzeRequest  # noqa: E402
from llm_service.app.strategies.selector import StrategySelector  # noqa: E402
from llm_service.app.validators.semantic import validate_signal_semantics  # noqa: E402
from replay import decision_indices, iso, max_drawdown_pct, parse_date  # noqa: E402
from risk_engine.app.schemas import SignalView  # noqa: E402
from scheduler.app.indicators import compute_indicators  # noqa: E402

logger = logging.getLogger("backtest.mechanical_replay")

DEFAULT_CACHE_DIR = Path(__file__).resolve().parent / ".data"
_STUB_REASONING = "mechanical-replay stub: always proposes the natural action for the rubric to gate"


def build_context(
    *,
    symbol: str,
    timeframe: str,
    candle_close_ts_ms: int,
    window: list[dict],
    indicators: dict,
    has_open_position: bool,
    unrealized_pnl_pct: float | None,
    llm_ohlcv_window: int,
):
    request = AnalyzeRequest(
        symbol=symbol,
        timeframe=timeframe,
        candle_close_time=iso(candle_close_ts_ms),
        ohlcv=[
            {"t": iso(c["t"]), "o": c["o"], "h": c["h"], "l": c["l"], "c": c["c"], "v": c["v"]}
            for c in window[-llm_ohlcv_window:]
        ],
        indicators=indicators,
        position_context={
            "has_open_position": has_open_position,
            "unrealized_pnl_pct": unrealized_pnl_pct,
        },
    )
    return ContextBuilder().build(request)


def mechanical_decision(context, settings: LLMServiceSettings):
    """Returns (final_action, regime, exit_confirmations)."""
    stub_action = Action.SELL if context.position.has_open_position else Action.BUY
    stub_output = LLMOutput(
        action=stub_action,
        confidence=0.99,
        reasoning=_STUB_REASONING,
        key_indicators=[],
        invalidation_condition="n/a",
    )
    strategy = StrategySelector().select(context)
    result = validate_signal_semantics(
        context,
        stub_output,
        min_exit_profit_pct=settings.min_exit_profit_pct,
        min_exit_loss_pct=settings.min_exit_loss_pct,
        hard_loss_cut_pct=settings.hard_loss_cut_pct,
    )
    return result.output.action, strategy.strategy.value, result.exit_confirmations


def _reason_text(result) -> str:
    return result.rejection_reason.value if result.rejection_reason else ""


async def run(args: argparse.Namespace) -> None:
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    scheduler_settings = SchedulerSettings()
    llm_settings = LLMServiceSettings()
    lookback = scheduler_settings.candle_lookback
    llm_ohlcv_window = scheduler_settings.llm_ohlcv_window
    timeframe_ms = timeframe_to_ms(args.timeframe)

    start_ms = int(parse_date(args.start).timestamp() * 1000)
    end_ms = int(parse_date(args.end).timestamp() * 1000)
    fetch_since_ms = start_ms - lookback * timeframe_ms
    fetch_until_ms = end_ms + timeframe_ms

    cache_dir = Path(args.cache_dir)
    candle_cache = CandleCache(cache_dir / "candles.sqlite")

    candles_by_symbol: dict[str, list[dict]] = {}
    for symbol in symbols:
        logger.info("fetching_history symbol=%s", symbol)
        candles_by_symbol[symbol] = await fetch_history(
            symbol, args.timeframe, fetch_since_ms, fetch_until_ms, candle_cache
        )

    events: list[tuple[int, str, int]] = []
    for symbol, candles in candles_by_symbol.items():
        for i in decision_indices(len(candles), lookback):
            close_ts = candles[i]["t"] + timeframe_ms
            if start_ms <= close_ts < end_ms:
                events.append((close_ts, symbol, i))
    events.sort(key=lambda e: (e[0], e[1]))

    if not events:
        logger.warning("no_decision_candles — check --start/--end against available history")
        candle_cache.close()
        return

    ledger = Ledger(
        starting_equity_usdt=Decimal(args.starting_equity),
        fee_pct=Decimal(args.fee_pct),
        slippage_pct=Decimal(args.slippage_pct),
        compounding=args.compounding,
    )
    risk_config = RiskConfig()
    entry_regimes: dict[str, str] = {}
    trade_regimes: dict[tuple[str, str], str] = {}  # (symbol, entry_time_iso) -> regime

    if args.trades_out:
        Path(args.trades_out).parent.mkdir(parents=True, exist_ok=True)
    if args.signals_out:
        Path(args.signals_out).parent.mkdir(parents=True, exist_ok=True)
    signals_file = open(args.signals_out, "w", newline="") if args.signals_out else None
    signals_writer = None
    if signals_file:
        signals_writer = csv.writer(signals_file)
        signals_writer.writerow(
            ["timestamp", "symbol", "action", "regime", "outcome", "reason"]
        )

    processed = 0
    for close_ts, group_iter in groupby(events, key=lambda e: e[0]):
        group = list(group_iter)
        now = datetime.fromtimestamp(close_ts / 1000, tz=timezone.utc)

        for _, symbol, i in group:
            closed = ledger.check_static_exit(symbol, candles_by_symbol[symbol][i], now)
            if closed is not None:
                trade_regimes[(symbol, closed.entry_time.isoformat())] = entry_regimes.pop(
                    symbol, "unknown"
                )

        for _, symbol, i in group:
            candles = candles_by_symbol[symbol]
            window = candles[i - lookback + 1 : i + 1]
            indicators = compute_indicators(pd.DataFrame(window))
            latest = candles[i]
            position = ledger.positions.get(symbol)
            has_open_position = position is not None
            unrealized_pnl_pct = (
                float((Decimal(str(latest["c"])) - position.entry_price) / position.entry_price)
                if position is not None
                else None
            )
            context = build_context(
                symbol=symbol,
                timeframe=args.timeframe,
                candle_close_ts_ms=close_ts,
                window=window,
                indicators=indicators,
                has_open_position=has_open_position,
                unrealized_pnl_pct=unrealized_pnl_pct,
                llm_ohlcv_window=llm_ohlcv_window,
            )
            action, regime, _ = mechanical_decision(context, llm_settings)

            signal_view = SignalView(
                id=f"{symbol}-{close_ts}",
                symbol=symbol,
                action=action,
                confidence=Decimal("0.99"),
                candle_ts=now,
                price=Decimal(str(latest["c"])),
                atr_14=Decimal(str(indicators["atr_14"])),
            )

            outcome, reason = "no_action", ""
            if action == Action.BUY and symbol not in ledger.positions:
                fill_price = Decimal(str(candles[i + 1]["o"]))
                result, new_position = ledger.apply_entry(
                    symbol, signal_view, risk_config, now, fill_price
                )
                outcome = "entered" if new_position else "rejected"
                reason = "" if new_position else _reason_text(result)
                if new_position:
                    entry_regimes[symbol] = regime
            elif action == Action.SELL and symbol in ledger.positions:
                fill_price = Decimal(str(candles[i + 1]["o"]))
                entry_time_iso = ledger.positions[symbol].entry_time.isoformat()
                result, trade = ledger.apply_exit_signal(
                    symbol, signal_view, risk_config, now, fill_price
                )
                outcome = "exited" if trade else "rejected"
                reason = "" if trade else _reason_text(result)
                if trade:
                    trade_regimes[(symbol, entry_time_iso)] = entry_regimes.pop(symbol, "unknown")

            if signals_writer:
                signals_writer.writerow(
                    [now.isoformat(), symbol, action.value, regime, outcome, reason]
                )

        processed += len(group)
        if processed % 500 < len(group):
            logger.info(
                "progress processed=%d/%d trades=%d realized_pnl_usdt=%s",
                processed, len(events), len(ledger.closed_trades), ledger.realized_pnl_usdt,
            )

    if signals_file:
        signals_file.close()

    if args.trades_out:
        with open(args.trades_out, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                ["symbol", "entry_time", "exit_time", "entry_price", "exit_price",
                 "size_usdt", "pnl_usdt", "pnl_pct", "exit_reason", "entry_regime"]
            )
            for t in ledger.closed_trades:
                regime = trade_regimes.get((t.symbol, t.entry_time.isoformat()), "unknown")
                writer.writerow(
                    [
                        t.symbol, t.entry_time.isoformat(), t.exit_time.isoformat(),
                        t.entry_price, t.exit_price, t.size_usdt,
                        t.pnl_usdt.quantize(Decimal("0.01")),
                        t.pnl_pct.quantize(Decimal("0.0001")),
                        t.exit_reason, regime,
                    ]
                )

    candle_cache.close()
    print_summary(ledger, candles_by_symbol, trade_regimes)


def print_summary(ledger: Ledger, candles_by_symbol: dict[str, list[dict]], trade_regimes: dict) -> None:
    trades = ledger.closed_trades
    wins = [t for t in trades if t.pnl_usdt > 0]
    losses = [t for t in trades if t.pnl_usdt <= 0]
    win_rate = (len(wins) / len(trades) * 100) if trades else 0.0
    drawdown = max_drawdown_pct(ledger.starting_equity_usdt, trades)

    pnl_of_equity = ledger.realized_pnl_usdt / ledger.starting_equity_usdt if ledger.starting_equity_usdt else Decimal("0")
    print("\n=== Mechanical backtest summary ===")
    print(f"Starting equity: {ledger.starting_equity_usdt} USDT")
    print(
        f"Closed trades:   {len(trades)}  "
        f"(wins={len(wins)} losses={len(losses)} win_rate={win_rate:.1f}%)"
    )
    print(
        f"Realized P&L:    {ledger.realized_pnl_usdt:.2f} USDT "
        f"({pnl_of_equity:.2%} of starting equity)"
    )
    print(f"Max drawdown:    {drawdown:.2%}")
    print(f"Killswitch tripped by end of run: {ledger.killswitch_tripped}")

    if ledger.positions:
        print(f"\nStill open ({len(ledger.positions)} positions, marked at last known close):")
        for symbol, position in ledger.positions.items():
            last_close = Decimal(str(candles_by_symbol[symbol][-1]["c"]))
            unrealized = (last_close - position.entry_price) / position.entry_price
            print(
                f"  {symbol}: entered {position.entry_time.isoformat()} @ {position.entry_price}, "
                f"unrealized {unrealized:.2%}"
            )

    by_reason: dict[str, int] = {}
    for t in trades:
        by_reason[t.exit_reason] = by_reason.get(t.exit_reason, 0) + 1
    if by_reason:
        print("\nExit reasons:", ", ".join(f"{k}={v}" for k, v in by_reason.items()))

    by_regime: dict[str, list] = {}
    for t in trades:
        regime = trade_regimes.get((t.symbol, t.entry_time.isoformat()), "unknown")
        by_regime.setdefault(regime, []).append(t)
    if by_regime:
        print("\n=== By entry regime (Strategy Selector classification) ===")
        for regime, regime_trades in sorted(by_regime.items(), key=lambda kv: -len(kv[1])):
            r_wins = [t for t in regime_trades if t.pnl_usdt > 0]
            r_win_rate = len(r_wins) / len(regime_trades) * 100
            r_pnl = sum((t.pnl_usdt for t in regime_trades), start=Decimal("0"))
            avg_pct = sum((t.pnl_pct for t in regime_trades), start=Decimal("0")) / len(regime_trades)
            print(
                f"  {regime:<22} n={len(regime_trades):<4} win_rate={r_win_rate:5.1f}%  "
                f"pnl={r_pnl:>8.2f} USDT  avg_pnl_pct={avg_pct:.2%}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--symbols", required=True, help="Comma-separated, e.g. BTC/USDT,ETH/USDT")
    parser.add_argument("--timeframe", default="1h", choices=["5m", "30m", "1h"])
    parser.add_argument("--start", required=True, help="UTC date/time, e.g. 2026-02-01")
    parser.add_argument("--end", required=True, help="UTC date/time, e.g. 2026-08-04")
    parser.add_argument("--starting-equity", default="10000")
    parser.add_argument("--fee-pct", default="0.00075", help="Per-leg taker fee (BNB-burn discounted)")
    parser.add_argument("--slippage-pct", default="0.0")
    parser.add_argument(
        "--compounding",
        action="store_true",
        help="Size off starting_equity + realized P&L instead of a fixed equity",
    )
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--trades-out", default=None, help="CSV path for the closed-trade log")
    parser.add_argument("--signals-out", default=None, help="CSV path for every signal + decision")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
