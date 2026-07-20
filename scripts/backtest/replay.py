"""Stage A: historical signal-generation replay.

Walks historical OHLCV candle-by-candle exactly like the live scheduler
cycle, calling a running `llm_service` for each closed candle and caching
every response. Because the LLM's exit behavior is gated by open-position
state (`services/llm_service/app/semantic_validator.py`), this also runs a
minimal position ledger inline — reusing the real, pure
`risk_engine.app.evaluate()` / `evaluate_exit()` functions plus Freqtrade's
static stoploss/`minimal_roi` safety net — so the position context sent to
the LLM matches what production would actually have had open.

Usage:
    .venv/bin/python scripts/backtest/replay.py \\
        --symbols BTC/USDT,ETH/USDT --timeframe 5m \\
        --start 2026-05-01 --end 2026-06-01 \\
        --llm-url http://localhost:8001/analyze

RiskConfig knobs (RISK_PER_TRADE_PCT, MAX_OPEN_POSITIONS, ...) load from the
environment exactly like the live risk_engine — set them the same way
before running to test a specific configuration.
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
import httpx
import pandas as pd
from cache import CandleCache, ResponseCache  # noqa: E402
from common.config import RiskConfig, SchedulerSettings  # noqa: E402
from common.enums import Action  # noqa: E402
from common.sentiment import MarketIndicatorSnapshot  # noqa: E402
from history import fetch_history, timeframe_to_ms  # noqa: E402
from ledger import Ledger  # noqa: E402
from risk_engine.app.schemas import SignalView  # noqa: E402
from scheduler.app.indicators import compute_indicators  # noqa: E402
from scheduler.app.sentiment import (  # noqa: E402
    MarketSentimentService,
    default_sentiment_providers,
)

logger = logging.getLogger("backtest.replay")

DEFAULT_CACHE_DIR = Path(__file__).resolve().parent / ".data"


def parse_date(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()


def decision_indices(n_candles: int, lookback: int) -> range:
    """Indices with `lookback` trailing candles for indicators and a
    following candle available to use as the entry/exit fill price."""
    return range(lookback - 1, n_candles - 1)


def build_payload(
    *,
    symbol: str,
    timeframe: str,
    candle_close_ts_ms: int,
    window: list[dict],
    indicators: dict,
    sentiment,
    has_open_position: bool,
    unrealized_pnl_pct: float | None,
    llm_ohlcv_window: int,
) -> dict:
    """Mirrors `services/scheduler/app/jobs.py::_build_analyze_payload`
    minus `provider_override` — omitted so `llm_service` uses its own
    env-configured provider, matching how you'd run it locally."""
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "candle_close_time": iso(candle_close_ts_ms),
        "ohlcv": [
            {"t": iso(c["t"]), "o": c["o"], "h": c["h"], "l": c["l"], "c": c["c"], "v": c["v"]}
            for c in window[-llm_ohlcv_window:]
        ],
        "indicators": indicators,
        "sentiment": sentiment.model_dump(mode="json"),
        "position_context": {
            "has_open_position": has_open_position,
            "unrealized_pnl_pct": unrealized_pnl_pct,
        },
    }


async def call_analyze(
    http_client: httpx.AsyncClient,
    llm_url: str,
    payload: dict,
    cache: ResponseCache,
    request_timeout: float,
) -> tuple[dict, bool]:
    cached = cache.get(payload)
    if cached is not None:
        return cached, True
    try:
        response = await http_client.post(llm_url, json=payload, timeout=request_timeout)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        # Deliberately not cached: a transport/validation failure here
        # (llm_service down, or — as seen in practice — rejecting a symbol
        # its currently-deployed schema doesn't recognize) is an artifact of
        # this run's environment, not a real model decision. Caching it
        # would permanently poison future runs for this candle even after
        # the underlying issue is fixed.
        logger.warning("llm_call_failed symbol=%s error=%s", payload["symbol"], exc)
        return {
            "action": "HOLD",
            "confidence": 0.0,
            "reasoning": "llm_service_unreachable",
            "model_name": "n/a",
            "raw_response": None,
        }, False
    cache.put(payload, data)
    return data, False


def _reason_text(result) -> str:
    return result.rejection_reason.value if result.rejection_reason else ""


def max_drawdown_pct(starting_equity: Decimal, trades: list) -> Decimal:
    equity = starting_equity
    peak = starting_equity
    worst = Decimal("0")
    for trade in sorted(trades, key=lambda t: t.exit_time):
        equity += trade.pnl_usdt
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, (peak - equity) / peak)
    return worst


async def run(args: argparse.Namespace) -> None:
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    scheduler_settings = SchedulerSettings()
    lookback = scheduler_settings.candle_lookback
    llm_ohlcv_window = scheduler_settings.llm_ohlcv_window
    timeframe_ms = timeframe_to_ms(args.timeframe)

    start_ms = int(parse_date(args.start).timestamp() * 1000)
    end_ms = int(parse_date(args.end).timestamp() * 1000)
    fetch_since_ms = start_ms - lookback * timeframe_ms
    fetch_until_ms = end_ms + timeframe_ms  # one extra candle for the final fill price

    cache_dir = Path(args.cache_dir)
    candle_cache = CandleCache(cache_dir / "candles.sqlite")
    response_cache = ResponseCache(cache_dir / "analyze_cache.sqlite")

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
        response_cache.close()
        return

    ledger = Ledger(
        starting_equity_usdt=Decimal(args.starting_equity),
        fee_pct=Decimal(args.fee_pct),
        slippage_pct=Decimal(args.slippage_pct),
        compounding=args.compounding,
    )
    risk_config = RiskConfig()
    sentiment_service = MarketSentimentService(default_sentiment_providers())
    semaphore = asyncio.Semaphore(args.concurrency)

    if args.trades_out:
        Path(args.trades_out).parent.mkdir(parents=True, exist_ok=True)
    if args.signals_out:
        Path(args.signals_out).parent.mkdir(parents=True, exist_ok=True)
    signals_file = open(args.signals_out, "w", newline="") if args.signals_out else None
    signals_writer = None
    if signals_file:
        signals_writer = csv.writer(signals_file)
        signals_writer.writerow(
            ["timestamp", "symbol", "action", "confidence", "cache_hit", "outcome", "reason"]
        )

    processed = 0
    async with httpx.AsyncClient(timeout=args.request_timeout) as http_client:
        for close_ts, group_iter in groupby(events, key=lambda e: e[0]):
            group = list(group_iter)
            now = datetime.fromtimestamp(close_ts / 1000, tz=timezone.utc)

            for _, symbol, i in group:
                ledger.check_static_exit(symbol, candles_by_symbol[symbol][i], now)

            async def build_and_call(symbol: str, i: int):
                candles = candles_by_symbol[symbol]
                window = candles[i - lookback + 1 : i + 1]
                indicators = compute_indicators(pd.DataFrame(window))
                latest = candles[i]
                previous_close = candles[i - 1]["c"]
                sentiment = sentiment_service.classify(
                    MarketIndicatorSnapshot(
                        price=latest["c"],
                        price_change_pct=(latest["c"] - previous_close) / previous_close,
                        rsi_14=indicators["rsi_14"],
                        ema_50=indicators["ema_50"],
                        ema_200=indicators["ema_200"],
                        macd=indicators["macd"],
                        atr_14=indicators["atr_14"],
                        volume=latest["v"],
                        volume_sma_20=indicators["volume_sma_20"],
                    )
                )
                position = ledger.positions.get(symbol)
                has_open_position = position is not None
                unrealized_pnl_pct = (
                    float((Decimal(str(latest["c"])) - position.entry_price) / position.entry_price)
                    if position is not None
                    else None
                )
                payload = build_payload(
                    symbol=symbol,
                    timeframe=args.timeframe,
                    candle_close_ts_ms=close_ts,
                    window=window,
                    indicators=indicators,
                    sentiment=sentiment,
                    has_open_position=has_open_position,
                    unrealized_pnl_pct=unrealized_pnl_pct,
                    llm_ohlcv_window=llm_ohlcv_window,
                )
                async with semaphore:
                    data, cache_hit = await call_analyze(
                        http_client, args.llm_url, payload, response_cache, args.request_timeout
                    )
                return symbol, i, indicators, latest, data, cache_hit

            results = await asyncio.gather(*(build_and_call(s, i) for _, s, i in group))

            for symbol, i, indicators, latest, data, cache_hit in results:
                candles = candles_by_symbol[symbol]
                action = Action(data["action"])
                signal_view = SignalView(
                    id=f"{symbol}-{close_ts}",
                    symbol=symbol,
                    action=action,
                    confidence=Decimal(str(data["confidence"])),
                    candle_ts=now,
                    price=Decimal(str(latest["c"])),
                    atr_14=Decimal(str(indicators["atr_14"])),
                )

                outcome, reason = "no_action", ""
                if action == Action.BUY and symbol not in ledger.positions:
                    fill_price = Decimal(str(candles[i + 1]["o"]))
                    result, position = ledger.apply_entry(
                        symbol, signal_view, risk_config, now, fill_price
                    )
                    outcome = "entered" if position else "rejected"
                    reason = "" if position else _reason_text(result)
                elif action == Action.SELL and symbol in ledger.positions:
                    fill_price = Decimal(str(candles[i + 1]["o"]))
                    result, trade = ledger.apply_exit_signal(
                        symbol, signal_view, risk_config, now, fill_price
                    )
                    outcome = "exited" if trade else "rejected"
                    reason = "" if trade else _reason_text(result)

                if signals_writer:
                    signals_writer.writerow(
                        [now.isoformat(), symbol, action.value, str(signal_view.confidence),
                         cache_hit, outcome, reason]
                    )

            processed += len(results)
            if processed % 200 < len(results):
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
                 "size_usdt", "pnl_usdt", "pnl_pct", "exit_reason"]
            )
            for t in ledger.closed_trades:
                writer.writerow(
                    [
                        t.symbol, t.entry_time.isoformat(), t.exit_time.isoformat(),
                        t.entry_price, t.exit_price, t.size_usdt,
                        t.pnl_usdt.quantize(Decimal("0.01")),
                        t.pnl_pct.quantize(Decimal("0.0001")),
                        t.exit_reason,
                    ]
                )

    candle_cache.close()
    response_cache.close()
    print_summary(ledger, candles_by_symbol)


def print_summary(ledger: Ledger, candles_by_symbol: dict[str, list[dict]]) -> None:
    trades = ledger.closed_trades
    wins = [t for t in trades if t.pnl_usdt > 0]
    losses = [t for t in trades if t.pnl_usdt <= 0]
    win_rate = (len(wins) / len(trades) * 100) if trades else 0.0
    drawdown = max_drawdown_pct(ledger.starting_equity_usdt, trades)

    pnl_of_equity = ledger.realized_pnl_usdt / ledger.starting_equity_usdt
    print("\n=== Backtest summary ===")
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--symbols", required=True, help="Comma-separated, e.g. BTC/USDT,ETH/USDT")
    parser.add_argument("--timeframe", default="5m", choices=["5m", "30m", "1h"])
    parser.add_argument("--start", required=True, help="UTC date/time, e.g. 2026-05-01")
    parser.add_argument("--end", required=True, help="UTC date/time, e.g. 2026-06-01")
    parser.add_argument("--llm-url", default="http://localhost:8001/analyze")
    parser.add_argument("--starting-equity", default="10000")
    parser.add_argument("--fee-pct", default="0.001", help="Round-trip taker fee, applied per leg")
    parser.add_argument("--slippage-pct", default="0.0")
    parser.add_argument(
        "--compounding",
        action="store_true",
        help="Size off starting_equity + realized P&L instead of a fixed equity "
        "(production currently sizes off a fixed placeholder — see account_state.py)",
    )
    parser.add_argument(
        "--concurrency", type=int, default=1, help="Parallel /analyze calls in flight"
    )
    parser.add_argument("--request-timeout", type=float, default=300.0)
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--trades-out", default=None, help="CSV path for the closed-trade log")
    parser.add_argument("--signals-out", default=None, help="CSV path for every signal + decision")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
