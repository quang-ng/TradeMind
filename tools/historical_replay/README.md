# Mechanical historical replay

This repository-local tool replays synthetic `BUY`/`SELL`/`HOLD` signals
against chronological candles. It reuses the production Risk Engine and
simulates next-candle fills, fees, slippage, Freqtrade's ROI table, the static
stop-loss, cooldowns, exposure limits, and portfolio accounting.

It is deliberately offline: it contains no Redis, PostgreSQL, Freqtrade,
exchange-credential, or order-execution client.

## Quick smoke test

```bash
uv run python -m tools.historical_replay.cli demo \
  --output reports/mechanical-demo
```

The built-in scenario creates one ROI winner, one stop-loss loser, and
intentional risk rejections. It also writes the generated input files so they
can be edited and replayed:

```bash
uv run python -m tools.historical_replay.cli replay \
  --candles reports/mechanical-demo/inputs/candles.jsonl \
  --signals reports/mechanical-demo/inputs/signals.jsonl \
  --output reports/my-replay \
  --fee 0.001 \
  --slippage 0.0005
```

Outputs are `summary.json`, `config.json`, `trades.csv`, `decisions.csv`, and
`equity_curve.csv`. Monetary results include entry and exit fees. Open
positions are marked at the last available candle close, net of an estimated
exit fee.

This validates mechanics, not the profitability of the LLM approach. LLM
signal generation and public historical-data download are intentionally a
later stage.
