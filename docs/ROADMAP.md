# TradeMind — Project Roadmap

> Working document. Sequencing and effort estimates assume part-time work and are
> planning aids, not commitments. Milestones are tracked as GitHub issues #3–#11.

Last updated: 2026-08-27

---

## 1. Where we are today

TradeMind is **live with real money** on Binance Spot (since 2026-07-27 — despite
`PROJECT.md` still saying "dry-run"; that doc is stale on this point). Long-only,
single LLM provider, one timeframe.

| Dimension | State |
|---|---|
| Equity | ~$624 (operator tops up periodically; +$300 on 2026-08-26) |
| Track record | 42 trades / 40 closed · win rate 57.5% · profit factor 3.80 · expectancy +$0.17/trade · max drawdown ~0.16% |
| Open positions | BTC/USDT, XRP/USDT |
| Pipeline | scheduler → llm_service (signal) → risk_engine (approve / size / stop) → freqtrade (execute) → Postgres audit + Telegram + admin_api + frontend |
| LLM | Ollama `qwen2.5:7b`, CPU-only VPS (~0.6 tok/s tail). `ANALYZE_TIMEOUT_SECONDS=450`, `num_predict=220` |
| Exit logic | `minimal_roi` decay table + per-trade ATR stop + peak-anchored trailing (`activation 2%` / `distance 1.5%`, grid-tuned 2026-08-11) |
| Regime | `StrategySelector`; a `TREND_FOLLOWING` classification suppresses BUY (since 2026-08-13) — currently ~57% of would-be entries |
| Positive-Expectancy build | M1 (Risk & R) + M2 (Trade Journal) implemented but stuck in **unmerged PR #2** |

### Known problems

1. **LLM reliability.** 15–30% of signals fail (timeout / malformed JSON) on a bad
   VPS hour, 0% on a good one. A failed signal becomes a forced HOLD with no retry
   against another backend — a silently missed trade. No fallback provider wired.
2. **The regime filter is unvalidated.** It blocks the majority of entry signals and
   nobody has measured, on live data, whether the blocked trades would have won or lost.
3. **No R-normalized performance visibility.** Expectancy, R-multiples, and
   regime/score breakdowns aren't computed over the live journal yet (M3).
4. **Small sample.** ~40 closed trades. Exit params and the regime filter were tuned
   on thin data; anything tuned now is at high risk of fitting noise.

---

## 2. Guiding principles

Carried from `docs/trademind_positive_expectancy_plan.md`:

- **Optimize positive expectancy, not win rate.**
- **The LLM proposes; the Risk Engine disposes.** No strategy path bypasses risk limits.
- **No strategy change ships from a backtest alone.** Backtest → out-of-sample →
  walk-forward → paper → small capital.
- **Additive-only until proven.** M1–M4 change no trading behaviour. M5 is the only
  milestone that can change what gets traded, and only after a manual config flip.
- **Don't increase risk because the bot recently won.**

---

## 3. The roadmap

```
Phase 0        Phase 1            Phase 2         Phase 3          Phase 4              Phase 5
Unblock &      Measurement        Regime &        Edge filter      Validation &         Activation
harden         foundation         scoring         (shadow)         tuning               & scale
─────────      ───────────        ───────────     ───────────      ───────────          ───────────
#3 land PR#2   #4 M3 Perf Engine  #5 M4 regime    #6 M5 expectancy #7 M6 validation     flip M5 on
#8 LLM         #9 validate        + trade score   filter (OFF,     harness              (operator)
   fallback       regime filter   + breakdowns    shadow log)      #10 trailing re-eval capital ↑
housekeeping
```

Dependency order: **#3 → #4 → #5 → #6 → (#7 with #10)**. #9 runs parallel to Phase 1.
#7 unblocks #10. Phase 5 is operator-gated with no fixed date.

---

### Phase 0 — Unblock & harden

**Goal:** get M1/M2 onto `main` and into production, and stop losing signals to LLM flakiness.

| Issue | Item | Size |
|---|---|---|
| #3 | Land PR #2 — rebase, test, merge, run both migrations on the VPS, redeploy | M |
| #8 | LLM provider fallback chain — Ollama primary, Anthropic (Haiku) on timeout / malformed / provider_error | M |
| — | Housekeeping: update `PROJECT.md` (live, not dry-run); tick M1/M2 in the plan doc; verify weekly-email deploy | S |

**Exit criteria:** a position opened in production writes a complete journal row
including `R_multiple`; a forced Ollama timeout still yields a valid signal from the
fallback; `signals.model_name` records which provider answered.

---

### Phase 1 — Measurement foundation

**Goal:** be able to see expectancy in R, and settle the regime-filter question with data.

| Issue | Item | Size |
|---|---|---|
| #4 | **M3 Performance Engine** — Win Rate, Avg Win/Loss R, Expectancy R/trade, Total R, Profit Factor, Max/Avg Drawdown, fees, slippage — over the live journal, scheduled recompute, snapshot history. Frontend Performance view ships in the same PR. | L |
| #9 | Validate the `TREND_FOLLOWING` regime filter — reconstruct forward outcomes for every suppressed signal since 2026-08-13, compare hypothetical expectancy vs the executed cohort. **Report only, no code change.** | M |

**Exit criteria:** operator can read Win Rate / Expectancy(R) / Total R / PF / Max DD,
filterable by symbol / regime / score bucket; a written verdict on whether the regime
filter is net-positive, net-negative, or noise.

---

### Phase 2 — Regime & scoring

**Goal:** label every setup by regime and quality so expectancy can be sliced.

| Issue | Item | Size |
|---|---|---|
| #5 | **M4** — extend `StrategySelector` regimes (`TREND_FOLLOWING` / `TREND_PULLBACK` / `MOMENTUM_CONTINUATION` / `MEAN_REVERSION`) + a new orthogonal volatility bucket (decision D2 — *not* the vision doc's BULL/BEAR/… labels); land the 0–100 Trade Score; persist regime + volatility + component sub-scores on every journal row; Performance Engine breakdowns by regime / volatility / score bucket / setup. | L |

**Exit criteria:** every new signal carries a regime label, a volatility bucket and a
0–100 score, all persisted; expectancy-by-regime and expectancy-by-score tables render.

---

### Phase 3 — Edge filter in shadow

**Goal:** build the historical-expectancy gate, but let it only *watch*.

| Issue | Item | Size |
|---|---|---|
| #6 | **M5 Historical Expectancy Filter** — per-(regime, volatility, score, setup) expectancy store with a minimum-sample-size guard; a pipeline stage between Trade Scoring and the Risk Engine. Ships with `expectancy_filter_enabled=False`: it logs an `expectancy_check` on every decision and **never rejects anything** (decision D4). | L |
| — | Soak: accumulate `expectancy_check` shadow data for several weeks before Phase 5 considers activation. | — |

**Exit criteria:** every `risk_decision` carries an `expectancy_check` line showing
what the filter *would* have done; zero change to any trade outcome.

---

### Phase 4 — Validation & tuning

**Goal:** a repeatable gauntlet every future strategy change must pass.

| Issue | Item | Size |
|---|---|---|
| #7 | **M6 Validation** — backtest → out-of-sample → walk-forward → paper-trading harness; automated backtest-vs-paper expectancy comparison; continuous-feedback loop that recomputes expectancy after every N trades and surfaces proposed changes for **human approval** (never auto-deploy). | L |
| #10 | Re-evaluate the peak-anchored trailing-stop params (currently `2%` / `1.5%`, tuned once on thin data 2026-08-11) using the M6 walk-forward harness. | M |

**Exit criteria:** a candidate change runs through all four stages and produces one
report with expectancy per stage and a pass/fail against a documented promotion checklist.

---

### Phase 5 — Activation & scale

**Operator-gated. No fixed date.**

- Review the accumulated `expectancy_check` shadow data. If it shows the filter would
  have improved expectancy without starving the system of trades, flip
  `expectancy_filter_enabled=True` via the audited `PATCH /config` path.
- Reconcile the M5 data-driven gate with the hardcoded `TREND_FOLLOWING` suppression —
  end state is **one** expectancy gate, not two overlapping heuristics.
- Increase capital gradually, and only after expectancy has held positive across at
  least two walk-forward windows plus the paper-trading run.

---

## 4. Rough sequencing

Assumes part-time work, one milestone mostly at a time. Ranges are for ordering
intuition, not deadlines.

| Phase | Calendar (rough) | Gate to next phase |
|---|---|---|
| 0 | ~1 week | PR #2 live; fallback verified |
| 1 | ~2–3 weeks | Performance view usable; regime-filter verdict written |
| 2 | ~2–4 weeks | Signals carry regime + score; breakdowns render |
| 3 | ~2–3 weeks build, then weeks of soak | `expectancy_check` logging live, data accumulating |
| 4 | ~3–4 weeks | Validation harness runs end-to-end |
| 5 | operator-gated | — |

---

## 5. Risk register

| Risk | Mitigation |
|---|---|
| Overfitting exit / filter params to ~40 trades | Walk-forward across multiple windows required before any live change; M5 ships disabled |
| PR #2 rebase conflicts after weeks of drift on `main` | Prioritised as the first Phase 0 task; keep the branch rebased |
| Anthropic fallback cost creep | Fallback only fires on the failure tail; default to Haiku; log/meter every invocation |
| Regime filter is actually load-bearing and removing it hurts | #9 is measurement-only; no change until the data says so |
| Operator flips M5 on too early | D4 hard rule: enabling is a separate, manual, audited config change — never bundled with the code that ships the rule |
| VPS LLM latency degrades further (Contabo steal-time) | Phase 0 fallback removes the dependency on local inference succeeding |

---

## 6. Explicitly not on this roadmap

- Short positions (plan exists, deferred by operator — complexity).
- Multi-exchange, multi-timeframe, or a larger symbol set (each symbol adds CPU-bound
  LLM load, already the bottleneck).
- Auto-deploying strategy changes from backtest results.
- Margin / futures / leverage.

---

## 7. Issue index

| # | Title | Phase | Labels |
|---|---|---|---|
| [#11](https://github.com/quang-ng/TradeMind/issues/11) | [Epic] Positive Expectancy roadmap M1–M6 | — | positive-expectancy |
| [#3](https://github.com/quang-ng/TradeMind/issues/3) | Land PR #2 (M1 + M2 merge / migrate / deploy) | 0 | positive-expectancy |
| [#8](https://github.com/quang-ng/TradeMind/issues/8) | LLM provider fallback chain | 0 | reliability, enhancement |
| [#4](https://github.com/quang-ng/TradeMind/issues/4) | M3 — Performance Engine | 1 | positive-expectancy |
| [#9](https://github.com/quang-ng/TradeMind/issues/9) | Validate the `TREND_FOLLOWING` regime filter | 1 | strategy-research |
| [#5](https://github.com/quang-ng/TradeMind/issues/5) | M4 — Regime + Trade Score + breakdowns | 2 | positive-expectancy |
| [#6](https://github.com/quang-ng/TradeMind/issues/6) | M5 — Historical Expectancy Filter (shadow) | 3 | positive-expectancy |
| [#7](https://github.com/quang-ng/TradeMind/issues/7) | M6 — Validation pipeline | 4 | positive-expectancy |
| [#10](https://github.com/quang-ng/TradeMind/issues/10) | Re-evaluate trailing-stop params | 4 | strategy-research |
