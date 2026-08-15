# TradeMind — Positive Expectancy Trading System Plan

## Objective

Build TradeMind around **Positive Expectancy**, not Win Rate alone.

The goal is to identify trading setups that have a statistically positive edge, control risk with `R`, allocate position size based on risk, and validate every strategy through out-of-sample testing before real-money deployment.

---

## Core Principles

1. **Optimize Positive Expectancy, not Win Rate alone.**
2. **1R = the maximum amount of money intentionally risked on one trade.**
3. The Risk Engine controls position sizing and risk limits.
4. Historical performance must be analyzed by market regime and setup.
5. No strategy change should be deployed directly from backtest results.
6. LLM proposes; Risk Engine disposes.
7. Backtest → Walk-forward → Paper Trading → Small Capital.

---

# Phase 1 — Risk Engine & R

### Goal

Standardize risk measurement across every trade.

### Requirements

Implement:

- Account equity
- Risk percentage per trade
- Risk amount
- Entry price
- Stop Loss
- Take Profit
- Position size
- PnL
- Fees
- Slippage
- `R_multiple`

### Formula

```text
risk_amount = account_equity × risk_percent

R_multiple = net_pnl / risk_amount
```

Example:

```text
Account = $10,000
Risk = 1%
1R = $100

Trade PnL = +$250
R = +2.5R
```

### Risk Controls

Implement hard limits:

- `MAX_RISK_PER_TRADE`
- `MAX_DAILY_LOSS`
- `MAX_DRAWDOWN`
- `MAX_OPEN_POSITIONS`
- Maximum exposure per symbol
- Maximum portfolio exposure

The strategy must not bypass these limits.

---

# Phase 2 — Trade Journal

### Goal

Record enough information to determine **which conditions actually make money**.

Every trade should store:

```text
trade_id
symbol
timestamp

market_regime

strategy
signal_score

trend_score
momentum_score
volume_score
volatility_score

entry_price
stop_loss
take_profit

risk_amount
position_size

gross_pnl
fees
slippage
net_pnl

R_multiple
outcome
```

Also store a snapshot of relevant market conditions at entry.

Example:

```text
BTC
Regime = Bull
RSI = 64
EMA20 > EMA50
Volume = 1.4x average
ATR = 2.1%
Signal Score = 84
RR = 2.5
```

---

# Phase 3 — Performance Engine

### Goal

Calculate performance using `R` as the primary normalized unit.

### Required Metrics

- Win Rate
- Average Win (`R`)
- Average Loss (`R`)
- Expectancy (`R/trade`)
- Total R
- Total PnL
- Profit Factor
- Maximum Drawdown
- Average Drawdown
- Number of trades
- Fees
- Slippage

### Expectancy Formula

```text
Expectancy =
    Win Rate × Average Win (R)
    + Loss Rate × Average Loss (R)
```

`Average Loss` is negative.

Example:

```text
Win Rate = 45%
Average Win = +2.1R
Average Loss = -1.0R

Expectancy =
0.45 × 2.1 + 0.55 × -1.0

= +0.395R/trade
```

Positive expectancy means the strategy has positive expected return per trade over a sufficiently large sample.

---

# Phase 4 — Market Regime Analysis

### Goal

Determine when the strategy works and when it does not.

Start with:

```text
BULL
BEAR
SIDEWAYS
HIGH_VOLATILITY
LOW_VOLATILITY
```

Calculate performance separately for each regime.

Example:

```text
                 Win Rate    Expectancy

Bull               58%       +0.42R
Bear               39%       -0.18R
Sideways           44%       -0.07R
High Volatility    41%       -0.21R
```

If a setup consistently has negative expectancy in a regime, the system should reduce or reject trades in that regime instead of blindly trading every signal.

---

# Phase 5 — Trade Score

### Goal

Create a deterministic score representing setup quality.

Example:

```text
Trend             0–25
Momentum          0–20
Volume            0–15
Market Regime     0–20
Risk/Reward       0–15
Volatility         0–5
----------------------
Total             0–100
```

Store the score for every trade.

After sufficient historical data, analyze expectancy by score range.

Example:

```text
Score       Expectancy

50–60       -0.15R
60–70       -0.03R
70–80       +0.18R
80–90       +0.42R
90–100      +0.47R
```

Do not assume these thresholds in advance. Derive them from data and validate them out-of-sample.

---

# Phase 6 — Historical Expectancy Filter

### Goal

Before executing a trade, check whether the current setup historically has positive expectancy.

Pipeline:

```text
Signal
  ↓
Market Regime
  ↓
Trade Score
  ↓
Historical Setup Performance
  ↓
Expectancy
  ↓
Risk Engine
  ↓
Execute / Reject
```

Example:

```text
BTC
Bull
Score = 84
Historical Expectancy = +0.42R
→ ALLOW
```

Example:

```text
BTC
Sideways
Score = 65
Historical Expectancy = -0.12R
→ HOLD / REJECT
```

The system should answer:

> "Does this type of setup historically have an edge?"

rather than only:

> "Is the current signal BUY?"

---

# Phase 7 — Position Sizing

### Goal

Allocate capital according to controlled risk, not a fixed dollar amount.

Basic principle:

```text
Risk Amount = Equity × Risk %

Position Size =
    Risk Amount / Stop Loss Distance
```

Example:

```text
Account = $10,000
Risk = 1%
Risk Amount = $100
```

A wider Stop Loss should result in a smaller position.

A tighter Stop Loss should result in a larger position, while the actual maximum loss remains approximately 1R.

Never allow position sizing to bypass the Risk Engine.

---

# Phase 8 — Backtesting & Anti-Overfitting

### Goal

Prevent the system from optimizing itself against historical noise.

Do NOT do:

```text
Full Historical Dataset
        ↓
Optimize
        ↓
Deploy
```

Use:

```text
Training
   ↓
Validation
   ↓
Out-of-Sample Test
   ↓
Walk-Forward Test
```

Evaluate stability across multiple time windows.

A strategy should not be accepted because it performs well on one historical period.

---

# Phase 9 — Paper Trading

### Goal

Verify that backtest performance survives real-time market conditions.

Run the strategy using live market data without real money.

Track at least:

- 100–300 trades where practical
- Expectancy
- Total R
- Drawdown
- Fees
- Slippage
- Execution latency
- Signal quality

Compare:

```text
Backtest Expectancy
vs
Paper Trading Expectancy
```

If backtest is strongly positive but paper trading becomes negative, stop and investigate before using real capital.

---

# Phase 10 — Small Capital Deployment

Only after successful:

```text
Backtest
   ↓
Walk-Forward
   ↓
Paper Trading
```

Deploy with small capital first.

Example progression:

```text
Theoretical $10,000
        ↓
Small real allocation
        ↓
Increase gradually only after validation
```

Do not increase risk simply because the bot recently won several trades.

---

# Phase 11 — Continuous Performance Feedback

After every N trades:

```text
New Trades
    ↓
Performance Engine
    ↓
Recalculate Expectancy
    ↓
Detect Performance Degradation
    ↓
Analyze Regime / Setup
    ↓
Propose Strategy Change
    ↓
Backtest
    ↓
Out-of-Sample Validation
    ↓
Risk Validation
    ↓
Human Approval
    ↓
Deploy
```

The system must **not** automatically modify and deploy a strategy based only on recent performance.

---

# Target Architecture

```text
                    ┌─────────────────┐
                    │   Market Data   │
                    └────────┬────────┘
                             ↓
                    ┌─────────────────┐
                    │ Feature Engine  │
                    └────────┬────────┘
                             ↓
                 ┌──────────────────────┐
                 │ Strategy / LLM Signal│
                 └──────────┬───────────┘
                            ↓
                 ┌──────────────────────┐
                 │   Market Regime      │
                 └──────────┬───────────┘
                            ↓
                 ┌──────────────────────┐
                 │    Trade Scoring     │
                 └──────────┬───────────┘
                            ↓
                 ┌──────────────────────┐
                 │ Expectancy Filter    │
                 └──────────┬───────────┘
                            ↓
                 ┌──────────────────────┐
                 │     Risk Engine      │
                 │   1R / Position Size │
                 └──────────┬───────────┘
                            ↓
                      ┌───────────┐
                      │ Execution │
                      └─────┬─────┘
                            ↓
                     ┌─────────────┐
                     │ Trade Journal│
                     └──────┬──────┘
                            ↓
                 ┌──────────────────────┐
                 │ Performance Engine   │
                 │ Expectancy / R / DD  │
                 └──────────┬───────────┘
                            │
                            └──────────────→ Feedback
```

---

# Implementation Milestones

## M1 — Risk & R

- [ ] Implement `1R` calculation
- [ ] Implement risk-per-trade configuration
- [ ] Implement position sizing
- [ ] Implement SL/TP validation
- [ ] Implement hard risk limits
- [ ] Add `R_multiple` to trade results

## M2 — Trade Journal

- [ ] Store complete trade lifecycle
- [ ] Store market regime
- [ ] Store signal score
- [ ] Store technical features
- [ ] Store entry/SL/TP
- [ ] Store PnL, fees, slippage
- [ ] Store R multiple

## M3 — Performance Engine

- [ ] Win Rate
- [ ] Average Win R
- [ ] Average Loss R
- [ ] Expectancy R/trade
- [ ] Total R
- [ ] Profit Factor
- [ ] Maximum Drawdown
- [ ] Fee/slippage analysis

## M4 — Regime & Trade Score

- [ ] Implement market regime classification
- [ ] Implement trade scoring
- [ ] Analyze expectancy by regime
- [ ] Analyze expectancy by score
- [ ] Analyze expectancy by setup

## M5 — Expectancy Filter

- [ ] Build historical setup statistics
- [ ] Add minimum sample-size requirements
- [ ] Reject statistically weak setups
- [ ] Add confidence/quality thresholds
- [ ] Keep Risk Engine as final authority

## M6 — Validation

- [ ] Backtest
- [ ] Out-of-sample testing
- [ ] Walk-forward testing
- [ ] Paper trading
- [ ] Compare backtest vs paper performance
- [ ] Small-capital deployment only after validation

---

# Success Criteria

The system should NOT define success as:

```text
Win Rate > X%
```

Instead evaluate:

```text
Positive Expectancy
Stable Expectancy across time
Controlled Maximum Drawdown
Positive Out-of-Sample performance
Positive Paper Trading performance
Acceptable fees/slippage
Stable performance across relevant market regimes
```

The ultimate objective is:

> **Identify setups with a durable statistical edge, express performance in R, control downside through the Risk Engine, and allocate capital only after the edge has survived out-of-sample validation.**

---

# Core Principle

> **The LLM proposes. The Risk Engine disposes.**
>
> The system should not try to predict every market move. It should identify situations where historical evidence shows positive expectancy, then take controlled risk when those situations occur.
