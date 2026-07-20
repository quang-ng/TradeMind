import argparse
from decimal import Decimal
from pathlib import Path

from .demo import build_demo
from .io import load_candles, load_signals, write_inputs, write_report
from .schemas import ReplayConfig
from .simulator import ReplaySimulator


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Offline mechanical replay of TradeMind risk and execution controls"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    demo = subparsers.add_parser("demo", help="run the built-in synthetic smoke replay")
    demo.add_argument("--output", type=Path, default=Path("reports/mechanical-demo"))

    replay = subparsers.add_parser("replay", help="replay candle and synthetic-signal JSONL")
    replay.add_argument("--candles", type=Path, required=True)
    replay.add_argument("--signals", type=Path, required=True)
    replay.add_argument("--output", type=Path, required=True)
    replay.add_argument("--starting-equity", type=Decimal, default=Decimal("10000"))
    replay.add_argument("--fee", type=Decimal, default=Decimal("0.001"))
    replay.add_argument("--slippage", type=Decimal, default=Decimal("0.0005"))
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "demo":
        candles, signals = build_demo()
        config = ReplayConfig()
        write_inputs(args.output / "inputs", candles, signals)
    else:
        candles = load_candles(args.candles)
        signals = load_signals(args.signals)
        config = ReplayConfig(
            starting_equity_usdt=args.starting_equity,
            fee_rate=args.fee,
            slippage_rate=args.slippage,
        )

    result = ReplaySimulator(config).run(candles, signals)
    write_report(args.output, result)
    summary = result.summary
    print(f"Report: {args.output}")
    print(
        f"Trades={summary.trades} Net={summary.net_pnl_usdt:.4f} USDT "
        f"WinRate={summary.win_rate:.2%} Drawdown={summary.max_drawdown_pct:.2%}"
    )
    print(f"Rejections={summary.rejection_counts}")


if __name__ == "__main__":
    main()
