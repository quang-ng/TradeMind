# TradeMind — Positive Expectancy: Implementation Plan

| | |
|---|---|
| Companion to | `docs/trademind_positive_expectancy_plan.md` (the "why"/vision doc — do not duplicate its principles here, only reference them) |
| Authority | `PROJECT.md` remains the single source of truth for contracts. This plan proposes changes to it; it does not supersede it. Each milestone below lists the exact `PROJECT.md` sections it must update in the same PR (AGENTS.md Section 6). |
| Status | M1 (Risk & R) and M2 (Trade Journal) implemented 2026-08-15 — not yet deployed/migrated on the VPS. M3–M6 still proposed, not built. |
| Audience | Whoever (human or agent) implements M1–M6 below, in order. |
| Context | System is **live**, trading real money on Binance since 2026-07-27, currently ~$115 equity. Every milestone below is written with that constraint first. |

---

## 0. How to read this document

`docs/trademind_positive_expectancy_plan.md` describes *what* a positive-expectancy system needs (R, journal, performance engine, regime analysis, trade score, expectancy filter, sizing, validation, paper trading, feedback loop). This document maps each of those onto **this specific codebase** — exact files, exact migrations, exact tests — and sequences the work as a series of small, independently shippable, additive-only PRs, per AGENTS.md's "implement one phase at a time" and "keep changes small and reviewable."

Section 1 is a gap analysis: for every concept in the vision doc, what already exists in TradeMind today and what's missing. Section 2 states the design decisions this plan makes where the vision doc is ambiguous relative to this codebase's actual architecture, with rationale — read this before implementing anything, since it changes file-level details in Section 4. Section 3 is the concrete schema/migration plan. Section 4 is the milestone-by-milestone build plan. Section 5 specifies exactly what must land in the audit trail for each new value this plan introduces. Section 6 covers rollout safety specific to a live-money system. Section 7 is testing strategy. Section 8 is sequencing. Section 9 records the operator's confirmed decisions (all open questions are resolved as of 2026-08-15).

---

## 1. Current-state gap analysis

| Vision-doc concept | Status in TradeMind today | Where |
|---|---|---|
| Account equity | ✅ Live, authenticated, no fallback | `risk_engine/app/account_state.py`, `common/account_balance.py` |
| Risk % per trade | ✅ | `RiskConfig.risk_per_trade_pct` (`common/config.py`) |
| **Risk amount (1R)** | ⚠️ Computed transiently inside `sizing.compute_sizing`, **never persisted** | `risk_engine/app/sizing.py:47` |
| Entry price / Stop Loss | ✅ Persisted per-trade (dynamic ATR stop, not static) | `Signal.price`, `RiskDecision.stop_loss_price` |
| Take Profit | ⚠️ No per-trade computed TP by design (MVP uses a decaying `minimal_roi` table + trailing stop as a shared safety net, not a per-trade figure) | `PROJECT.md` §9.2, `ExternalSignalStrategy.py`, `exit_evaluator.py` |
| Position size | ✅ | `sizing.py`, `RiskDecision.position_size_usdt/base` |
| PnL | ✅ realized (`Position.pnl_usdt/pnl_pct`), ✅ unrealized (mark-to-market) | `admin_api/app/position_service.py` |
| Fees | ❌ Not persisted per-trade in production (netted invisibly into Freqtrade's reported PnL). Only estimated in the **offline backtest** ledger | `scripts/backtest/ledger.py: fee_pct` |
| Slippage | ❌ Not tracked in production at all. Only estimated in the offline backtest ledger | `scripts/backtest/ledger.py: slippage_pct` |
| **R_multiple** | ❌ Does not exist anywhere in the codebase — no column, no computation, in production or backtest | — |
| Hard risk limits | ✅ mostly: per-trade cap (via sizing clamp), daily loss circuit breaker, consecutive-loss pause, max open positions, max total exposure, per-pair cooldown. `MAX_DRAWDOWN` as its own standalone limit does **not** exist (daily-loss + consecutive-loss are the closest proxies today) | `risk_engine/app/rules/*.py` |
| Trade Journal (regime, scores, market snapshot) | ⚠️ Regime label (`strategy_selected`) is already computed **every cycle** and written into `Signal.raw_response` JSONB — but unindexed, not queryable, not on `Position`. Scores don't exist | `llm_service/app/signals/generator.py: _enrich` |
| Performance Engine (win rate, expectancy-R, profit factor, drawdown) | ❌ None in production. A **partial, ad hoc** version exists only in the offline `scripts/backtest/mechanical_replay.py` (win rate, USDT PnL, max drawdown, win-rate-by-regime) — but no R units, no expectancy formula, no profit factor | `scripts/backtest/mechanical_replay.py:321 print_summary` |
| Market Regime Analysis | ⚠️ `StrategySelector` deterministically classifies every cycle into `TREND_FOLLOWING / TREND_PULLBACK / MOMENTUM_CONTINUATION / MEAN_REVERSION` (not the vision doc's `BULL/BEAR/SIDEWAYS/HIGH_VOL/LOW_VOL`) — real, unit-tested, but not persisted queryably and not fed by a volatility bucket | `llm_service/app/strategies/selector.py` |
| Trade Score (0–100 rubric) | ❌ Does not exist | — |
| Expectancy Filter (pre-trade statistical gate) | ❌ Does not exist. Nearest analog is the semantic validator's confirmation-count rubric — a **structural** rule, not a **statistical/historical** one | `llm_service/app/validators/semantic.py` |
| Position sizing by risk | ✅ Already exactly Phase 7's formula: fixed-fractional + ATR stop distance, clamped by `max_position_pct`/free balance, scaled by confidence | `risk_engine/app/sizing.py` |
| Backtesting / anti-overfitting infra | ⚠️ Real infrastructure already exists: `scripts/backtest/{mechanical_replay,replay,ledger,history,cache}.py` — a mechanical replay engine that reuses `evaluator.py`/`exit_evaluator.py` **unmodified** against historical candles (genuine out-of-sample-capable). Missing: automatic train/validation/walk-forward splitting, R/expectancy reporting | `scripts/backtest/*.py` |
| Paper trading | ⚠️ Conflated historically with `DRY_RUN=true`. The system has since flipped to live (2026-07-27) — there is no separate "paper" mode running in parallel with live today | `RiskConfig.dry_run` |
| Small capital deployment | ✅ Already the operating mode (~$115 equity) | — |
| Continuous performance feedback loop | ⚠️ The human-gated mechanism already exists structurally (`PATCH /config`, audited `CONFIG_CHANGED` events) — nothing yet *computes* "recalculate expectancy → detect degradation → propose change" | `admin_api/app/routers/config.py` |

**Bottom line:** Phases 1, 7, 10 of the vision doc are essentially done. Phase 2 (journal) and Phase 4 (regime) are half-done — the raw data is being computed already, it's just not persisted as first-class, queryable, cross-linked columns. Phases 3, 5, 6 (Performance Engine, Trade Score, Expectancy Filter) are genuinely greenfield. Phase 8/9 (backtesting/paper) have real infrastructure to build on rather than start from scratch.

---

## 2. Design decisions this plan makes

The vision doc is intentionally implementation-agnostic. These decisions resolve it against TradeMind's actual architecture and constraints. Each is a deliberate choice with a stated rationale — flag disagreement before implementation starts (see Section 8).

### D1 — R_multiple's denominator is *actual* per-trade risk, not the nominal account-level budget

`sizing.compute_sizing` already computes `risk_amount_usdt = equity_usdt * risk_per_trade_pct` (the account-level 1R *budget*) — but then clamps the resulting position by `max_position_pct`, free balance, and a confidence-based scale-down (2026-08-04 change) before arriving at `position_size_usdt`. Whenever any of those clamps bind, the money genuinely exposed to loss-if-stopped for *that specific trade* — `position_size_usdt * stop_distance_pct` — is smaller than the nominal budget.

The vision doc's own principle #2 says "1R = the maximum amount of money **intentionally risked on one trade**" — that's the clamped, actual figure, not the pre-clamp budget. This plan stores **both** (`nominal_risk_amount_usdt` for audit visibility of the budget that was targeted, `actual_risk_usdt` for what was truly at stake) but always uses `actual_risk_usdt` as the R-multiple denominator everywhere expectancy math happens.

### D2 — Extend the existing regime taxonomy; don't replace it with BULL/BEAR/SIDEWAYS

`StrategySelector`'s four labels are already deterministic, unit-tested, computed every cycle, and — since 2026-08-13 — load-bearing in `validators/semantic.py` (a `TREND_FOLLOWING` classification suppresses an otherwise-qualifying BUY, backed by walk-forward evidence). Replacing that taxonomy is a much larger, riskier change than the vision doc's actual requirement ("determine when the strategy works and when it does not," "calculate performance separately for each regime").

This plan persists the *existing* classifier's output as `setup_regime` and adds one genuinely missing, orthogonal dimension: **`volatility_regime`** (`HIGH_VOLATILITY` / `NORMAL` / `LOW_VOLATILITY`), since ATR today is only ever used for stop-sizing, never classified into its own bucket. `BULL`/`BEAR` is redundant with `TREND_FOLLOWING`'s direction; `SIDEWAYS` is `MEAN_REVERSION`.

### D3 — Trade Score lives in `llm_service`, as a new field on the wire contract (not buried in `raw_response`)

Scoring inputs (trend, momentum, volume, regime, volatility) are already fully computed in `llm_service`'s `MarketContext`/`StrategySelector` by the time `AnalysisPipeline` runs — same trust-zone boundary as `StrategySelector` (descriptive only, zero execution authority, no sizing). It's built as a new `scoring/` package parallel to `strategies/`.

Unlike `strategy_selected` today, this plan promotes the score to a **first-class field** on `TradingSignal` (`trade_score: int | None`) rather than only inside `raw_response` JSON — the regime gap in D2/Section 1 (computed every cycle, unindexed, unqueryable) is exactly the mistake this plan avoids repeating. This requires a `PROJECT.md` §8.2 wire-contract update in the same PR.

**Constraint to respect:** `llm_service` must never import from `risk_engine` (isolated-zone rule, PROJECT.md §3/§14 rule 1). The Risk/Reward sub-score cannot reach into `risk_engine/app/exit_evaluator.py`'s `MINIMAL_ROI`/`STATIC_STOPLOSS_PCT` constants — it must use a self-contained, `llm_service`-local approximation (e.g. a configured `assumed_reward_multiple`).

### D4 — Expectancy Filter ships inert (advisory-only) before it ever rejects a live trade

The vision doc itself says "Do not assume these thresholds in advance. Derive them from data and validate them out-of-sample" (Phase 5) and "The system must **not** automatically modify and deploy a strategy based only on recent performance" (Phase 11). Given the account is live with real money and only ~3 weeks of live history, this plan ships the filter fully built and tested but **disabled by default** (`RiskConfig.expectancy_filter_enabled = False`). It runs in shadow mode — computing and persisting its verdict on every signal without ever rejecting one — until the operator explicitly reviews the shadow data and flips it on via the existing audited `PATCH /config` path. This mirrors how this repo already treats `dry_run` flips (PROJECT.md §14 rule 13: "never a side effect of unrelated work").

### D5 — No retroactive backfill of R/score/regime onto already-closed trades

Backfilling would mean writing computed-after-the-fact values into what PROJECT.md treats as an immutable audit trail (`AuditEvent` is explicitly append-only), and `trade_score`'s inputs aren't guaranteed reconstructable for every historical row (`Signal.model_input` is nullable specifically because "rows created before this field was added have none" — §7.1). Historical estimates belong in an **offline, read-only report** (Section 4, M4), clearly labeled as estimated, never written back to Postgres. Everything new gets these fields natively going forward.

---

## 3. Data model changes

All new columns are **nullable and additive-only** — zero behavior change, safe to ship straight to the live system. One migration per milestone (M1–M2 need schema; M5 needs one more), matching AGENTS.md's "one logical change per PR" and this repo's existing `YYYYMMDD_NNNN_description.py` convention (latest on disk: `20260729_0001_consecutive_loss_reset.py`).

### `migrations/versions/20260815_0001_expectancy_journal.py` (M1)

`risk_decisions`:
| Column | Type | Notes |
|---|---|---|
| `nominal_risk_amount_usdt` | `Numeric(20,8)`, nullable | `equity_usdt * risk_per_trade_pct` at decision time — the pre-clamp budget |
| `actual_risk_usdt` | `Numeric(20,8)`, nullable | `position_size_usdt * stop_distance_pct` — the true 1R for this trade (D1); null when rejected |
| `stop_distance_pct` | `Numeric(10,6)`, nullable | Already computed in `SizingResult`, currently dropped before persistence |

`positions`:
| Column | Type | Notes |
|---|---|---|
| `exit_reason` | `String`, indexed, nullable | `atr_stoploss` \| `trailing_stop` \| `minimal_roi` \| `llm_sell_signal` \| `manual` — mirrors `scripts/backtest/ledger.py`'s `ClosedTrade.exit_reason`, not currently captured in production |
| `fees_usdt` | `Numeric(20,8)`, nullable | Best-effort from Freqtrade trade-detail if exposed, else estimated |
| `fees_estimated` | `Boolean`, default `false` | Distinguishes a real Freqtrade-reported fee from a config-estimated one |
| `r_multiple` | `Numeric(10,4)`, nullable | `pnl_usdt / risk_decisions.actual_risk_usdt` via `entry_order_id → risk_decision_id`; null for legacy rows (D5) |

### `migrations/versions/20260815_0002_trade_journal_fields.py` (M2)

`signals`:
| Column | Type | Notes |
|---|---|---|
| `trade_score` | `Integer`, indexed, nullable | 0–100, from the new `TradeScorer` |
| `score_breakdown` | `JSONB`, nullable | Per-component sub-scores, for audit/debugging |
| `setup_regime` | `String`, indexed, nullable | `StrategySelector`'s existing label, promoted from `raw_response` to a first-class column |
| `volatility_regime` | `String`, indexed, nullable | New: `HIGH_VOLATILITY` \| `NORMAL` \| `LOW_VOLATILITY` |

`positions` (denormalized from the entry `Signal`, same precedent as `entry_price`/`amount` already being copied rather than only living upstream):
| Column | Type | Notes |
|---|---|---|
| `market_regime` | `String`, indexed, nullable | Copy of entry `Signal.setup_regime` |
| `trade_score` | `Integer`, indexed, nullable | Copy of entry `Signal.trade_score` |

### Deferred: `setup_expectancy_stats` materialized table

Designed now, **not built** until trade volume actually makes on-read aggregation slow (YAGNI at current ~$115-account trade cadence). Schema sketch for when it's needed: `setup_key, sample_size, win_rate, avg_win_r, avg_loss_r, expectancy_r, total_r, profit_factor, window_start, window_end, computed_at`. M5's expectancy filter is written against a small loader interface (`expectancy_state.py`) so swapping the live-aggregation query for this table later is a one-file change.

---

## 4. Milestone-by-milestone plan

Numbered to match the vision doc's own M1–M6 milestone table for continuity. Each milestone is one PR. **Every milestone in M1–M4 has zero effect on trading behavior** — they are purely additive persistence and reporting. Only M5 can ever change what gets traded, and only after a manual config flip (D4).

### M1 — Risk & R ✅ implemented 2026-08-15 (not yet deployed to VPS)

**Goal:** Make "R" a first-class, persisted unit.

- `services/risk_engine/app/sizing.py` — extend `SizingResult` with `risk_amount_usdt` (nominal) and `actual_risk_usdt` (computed, D1). Pure function change.
- `services/common/db/models.py` — add the M1 columns above to `RiskDecision`, `Position`.
- `services/risk_engine/app/main.py` — wherever `RiskDecision(...)` is constructed (currently `main.py:159` and `:222`), persist `nominal_risk_amount_usdt`, `actual_risk_usdt`, `stop_distance_pct`.
- `services/admin_api/app/routers/webhooks.py` (`_handle_exit_fill`) — on exit fill: look up the linked `RiskDecision` (via `Order.risk_decision_id` → `entry order`), compute and persist `Position.r_multiple`, `exit_reason` (from the webhook payload / reconciliation), `fees_usdt`/`fees_estimated` (new `estimated_fee_pct` setting, since none exists in production config today — only in the offline `Ledger`).
- Tests: extend `services/risk_engine/tests/test_sizing.py` (actual_risk_usdt across unclamped / `max_position_pct`-clamped / free-balance-clamped / confidence-scaled scenarios — this is exactly the kind of property-based test PROJECT.md §12 Phase 2 already requires for sizing), extend `services/admin_api/tests/test_webhooks.py`.
- `PROJECT.md` updates: §7.2 (RiskDecision), §7.4 (Position), §9.2 (document `actual_risk_usdt` vs nominal, cite D1).

### M2 — Trade Journal (regime + score) ✅ implemented 2026-08-15 (not yet deployed to VPS)

**Goal:** Promote regime to a queryable column; add Trade Score; propagate both onto `Position` at close.

- `services/llm_service/app/models/wire.py` — add `trade_score`, `score_breakdown`, `setup_regime`, `volatility_regime` to `TradingSignal` (D3).
- `services/llm_service/app/scoring/trade_score.py` (new package) — `TradeScorer.score(context, strategy) -> TradeScoreResult`, pure function, doc's rubric (Trend 0–25 / Momentum 0–20 / Volume 0–15 / Regime 0–20 / R:R 0–15 / Volatility 0–5), each sub-score its own small pure function — same shape as `StrategySelector`, same unit-testability.
- `services/llm_service/app/strategies/volatility_classifier.py` (new) — fixed-threshold ATR/price bucketing into HIGH/NORMAL/LOW, consistent with `selector.py`'s existing fixed-threshold style (`_TREND_GAP_THRESHOLD_PCT`) rather than requiring new historical-percentile plumbing `ContextBuilder` doesn't currently have.
- `services/llm_service/app/services/pipeline.py` — wire both into `AnalysisPipeline` alongside the existing `StrategySelector` call.
- `services/scheduler/app/jobs.py` — persist the four new columns onto the `Signal` row from the `/analyze` response.
- `services/admin_api/app/routers/webhooks.py` (`_handle_entry_fill`) — denormalize `market_regime`/`trade_score` from the linked `Signal` onto the new `Position` row.
- `frontend/src/types.ts` — add the new optional fields to `Signal`/`Position`. UI surfacing (badges) is cosmetic, can land in the same PR or be deferred.
- Tests: `test_trade_score.py`, `test_volatility_classifier.py`, extend `test_signal_generator.py`, `test_pipeline.py`/`test_analyze_endpoint.py` fixtures, scheduler job tests, webhook tests.
- `PROJECT.md`: §8.2 (wire contract), §7.1 (Signal), §7.4 (Position), §6 (new `scoring/` package).

### M3 — Performance Engine

**Goal:** Win Rate, Avg Win/Loss R, Expectancy(R), Total R, Profit Factor, Max/Avg Drawdown, fees, slippage — queryable.

- `services/common/performance.py` (new) — pure functions (`compute_win_rate`, `compute_expectancy_r`, `compute_profit_factor`, `compute_max_drawdown`, …) over closed-position-shaped rows. Lives in `common` because PROJECT.md §6 already designates it as "the only code allowed to define domain models... so no service can drift from the shared contract" — the same reasoning applies to shared performance math, since `scripts/backtest` and `admin_api` both need it (see M4).
- `services/admin_api/app/routers/performance.py` (new) — `GET /performance?symbol=&regime=&score_min=&score_max=&since=&until=`, computed live from `positions` (current trade volume makes on-read aggregation fine — no need for the deferred materialized table yet).
- `services/admin_api/app/schemas.py` — `PerformanceSummary` response model.
- **Frontend, same PR (operator decision, 2026-08-15) — not deferred:**
  - `frontend/src/types.ts` — `PerformanceSummary` type mirroring the new response model.
  - `frontend/src/api.ts` — `getPerformance(params)` client call.
  - `frontend/src/App.tsx` — new "Performance" view/tab: headline stat tiles (Win Rate, Expectancy R/trade, Total R, Profit Factor, Max Drawdown), filterable by symbol/regime/score-bucket, reusing the console's existing auth/session pattern. Follow the `dataviz` skill for any chart/stat-tile styling so it reads as one system with the rest of the console.
  - `frontend/src/App.test.tsx` — smoke test for the new view.
- Tests: `services/common/tests/test_performance.py` (property-based via `hypothesis`, already a project dependency per the existing `.hypothesis/` cache and PROJECT.md §12 Phase 2's sizing test), `services/admin_api/tests/test_performance.py`, frontend test above.
- `PROJECT.md`: §11 (new endpoint), §4 (React Operator Console responsibility already covers "Present ... P&L" — extend to explicitly mention performance/expectancy metrics), §6 repo structure (new `routers/performance.py`); consider a new §9-adjacent section documenting the Performance Engine analogous to how §9 documents the Risk Engine.

### M4 — Regime & Score Backtesting Analysis

**Goal:** Retrofit the *existing* offline replay tool with R/score/regime reporting, so backtest and live measure expectancy identically (vision doc Phase 8: "no strategy change should be deployed directly from backtest results" implicitly requires backtest and live math to be provably the same).

- `scripts/backtest/mechanical_replay.py` — import the M2 `TradeScorer`/`volatility_classifier` directly (already pure functions, no network calls — the script already imports `StrategySelector` the same way via `build_context`). Extend `ClosedTrade`/CSV output with `r_multiple`, `score`, `volatility_regime`. Extend `print_summary`'s existing by-regime block (`mechanical_replay.py:358-372`) with an Expectancy(R) column, and add a matching by-score-bucket block.
- `scripts/backtest/ledger.py` — extend `ClosedTrade` with `r_multiple`, computed via the same `actual_risk_usdt` logic as M1's production path (via the already-shared `sizing.compute_sizing` call in `Ledger.apply_entry`).
- New: `scripts/backtest/expectancy_report.py` — thin wrapper that calls into `services/common/performance.py` (built in M3) over `mechanical_replay`'s CSV output or live `positions`, producing the vision doc's own Phase 4/5 example tables (regime × expectancy, score-bucket × expectancy) as a reusable report.
- Tests: extend `scripts/backtest/test_ledger.py`.
- No `PROJECT.md` change required (scripts/ isn't part of the audited production contract).

### M5 — Expectancy Filter (ships inert; D4)

**Goal:** Implement the vision doc's Phase 6 pipeline (`Signal → Regime → Score → Historical Setup Performance → Expectancy → Risk Engine → Execute/Reject`) as a new Risk Engine rule, disabled by default.

- `services/risk_engine/app/expectancy_state.py` (new, mirrors `account_state.py`) — `load_expectancy_state(session, setup_key) -> ExpectancyView`, queries closed `positions` by `market_regime`+`trade_score` bucket, computes `sample_size`/`expectancy_r` via `services/common/performance.py`. Pre-fetched **before** calling the pure `evaluate()`, preserving PROJECT.md §9's "pure, deterministic function... no I/O" contract for the Risk Engine core — same pattern already used for `AccountState`.
- `services/risk_engine/app/schemas.py` — extend `RuleContext` with `expectancy: ExpectancyView`.
- `services/risk_engine/app/rules/expectancy_filter.py` (new, one rule per file per AGENTS.md §3):
  - `expectancy_filter_enabled == False` → always pass, but still write `expectancy_check` JSON for shadow-mode visibility (D4).
  - `sample_size < expectancy_min_sample_size` → pass (`decision="INSUFFICIENT_DATA"`) — absence of evidence is not evidence of a bad setup; only a *proven* negative-expectancy setup should ever block (matches the vision doc's own worked examples, which always show a computed negative number, never "unknown").
  - `expectancy_r < expectancy_min_r` (adequate sample) → new `RejectionReason.NEGATIVE_EXPECTANCY_SETUP`.
- `services/common/enums.py` — add `NEGATIVE_EXPECTANCY_SETUP` to `RejectionReason`.
- `services/risk_engine/app/rules/__init__.py` — register in `RULES_IN_ORDER` **after** `min_confidence.check`, **before** `max_open_positions.check` (an entry-quality gate, not a portfolio/capital gate — this ordering determines the reported rejection reason under the existing short-circuit rule and should be reviewed deliberately, not defaulted).
- `services/common/config.py` — `RiskConfig` additions: `expectancy_filter_enabled: bool = False`, `expectancy_min_sample_size: int = 30` (placeholder — tune once shadow data exists; the vision doc's "100–300 trades" is a *paper-trading validation* threshold, not a per-setup-bucket minimum), `expectancy_min_r: Decimal = Decimal("0")` (reject only setups with *proven* negative expectancy by default).
- Tests: `services/risk_engine/tests/test_expectancy_filter.py` (1:1 per AGENTS.md §5), extend `test_evaluator.py`.
- `PROJECT.md`: §9.1 new rule row (documented as present-but-disabled-by-default), §9.3 rejection reasons, §6 new file.
- **Rollout note:** ship with the flag off. Let it run in shadow mode for real weeks of live trades. Review `/performance` broken out by regime/score (M3). Only then does the operator decide to `PATCH /config {"expectancy_filter_enabled": true}` — the same audited, human-gated mechanism already used for `dry_run` flips.

### M6 — Validation Pipeline

**Goal:** Formalize walk-forward validation using the existing replay tooling.

- `scripts/backtest/walk_forward.py` (new, thin orchestration) — repeatedly invokes the existing `mechanical_replay.run()` across rolling train/test windows, collecting per-window Expectancy(R)/win-rate/max-DD, flagging instability across windows (vision doc: "should not be accepted because it performs well on one historical period"). No reimplementation of the simulation engine — orchestration only.
- **No email work in this milestone** (operator decision, 2026-08-15): the vision doc's Phase 9 "compare backtest vs realized expectancy" check is served by the M3 Performance view instead of the weekly summary email — the FE already gives the operator an on-demand, filterable view of the same numbers, so extending `services/notifier/app/main.py`'s weekly email with Expectancy(R)/Profit Factor is dropped from this plan. The weekly email keeps its current PnL-only shape (2026-08-15 shipped scope) unless a future decision reopens this.
- **Explicit deviation from the vision doc:** no separate paper-trading environment is proposed. The account is already live at small size (~$115) and already sits at the vision doc's own Phase 10 end-state ("small real allocation, increase gradually only after validation") — building a parallel paper-trading mode now would be scope creep against a system that has already graduated past that stage. Flag this as a deliberate, reasoned skip, not a silent one.
- Tests: `scripts/backtest/test_walk_forward.py`.

---

## 5. Audit logging requirements

PROJECT.md §14 rule 7 already governs this: **"Every state-changing database write is paired with an `AuditEvent` row in the same transaction. No silent state transitions."** This is the north star for every milestone below — nothing in M1–M6 introduces a value that matters for understanding a trading decision without also landing in `audit_events`, reconstructable via `trace_id` through the existing `GET /audit?trace_id=` endpoint (PROJECT.md §11). Two findings from reading the current code shape what "add logging" concretely means here:

- **`SIGNAL_RECEIVED` is defined in `AuditEventType` (`common/enums.py`) but never actually written anywhere in the codebase today.** It's a live gap in the existing audit trail, not something this plan is introducing.
- `RISK_APPROVED`/`RISK_REJECTED` (`risk_engine/app/main.py: _write_decision_audit_event`) already fire for every risk-evaluated signal with `{signal_id, approved, rejection_reason}`, and `POSITION_CLOSED`/`ORDER_FILLED` (`admin_api/app/routers/webhooks.py`) already carry `exit_reason` straight from the Freqtrade webhook payload — it's just never copied onto the `Position.exit_reason` column M1 adds (Section 3), only into the audit payload. So `exit_reason` needs no new *sourcing* work in M1, only persistence onto the row itself, and the audit event already has it.

### What each milestone must wire up

| Milestone | New value | Audit home | Change required |
|---|---|---|---|
| M1 | `nominal_risk_amount_usdt`, `actual_risk_usdt`, `stop_distance_pct` | `RISK_APPROVED` / `RISK_REJECTED` | Extend `_write_decision_audit_event`'s payload with these three fields (when approved) — today the payload only carries `signal_id`/`approved`/`rejection_reason`, not the sizing math that produced the decision |
| M1 | `r_multiple`, `fees_usdt`, `fees_estimated` | `POSITION_CLOSED` | Extend the payload built in `_handle_exit_fill` (`webhooks.py:240-248`) with these three — `exit_reason` is already there, this brings the rest of the close event up to the same standard |
| M2 | `trade_score`, `score_breakdown`, `setup_regime`, `volatility_regime` | `SIGNAL_RECEIVED` (**newly wired up**, not new) | Have the Scheduler write a `SIGNAL_RECEIVED` `AuditEvent` at the same point it persists the `Signal` row (`scheduler/app/jobs.py`), payload `{signal_id, symbol, action, confidence, trade_score, setup_regime, volatility_regime}`. This finally gives the long-unused enum value a writer, and separates "what the signal *was*" (audited once, regardless of outcome) from "what the Risk Engine *decided*" (already audited via `RISK_APPROVED`/`RISK_REJECTED`) |
| M3 | Performance Engine reads (`GET /performance`) | — | **No new audit event.** This is a read-only reporting endpoint; PROJECT.md §14 rule 7 scopes the requirement to *state-changing* writes, not reads — an audit log entry per `GET` would conflate the audit trail (a record of trading-state changes) with an access log (a different concern, out of scope here). Say this explicitly so "log everything" isn't over-applied to read paths |
| M5 | `expectancy_check` (`{historical_expectancy_r, sample_size, setup_key, decision, enforced}`) | `RISK_APPROVED` / `RISK_REJECTED` | Extend the same payload as M1's row above, on **every** evaluated signal — approved or rejected, flag on or off. This is what makes D4's shadow mode actually auditable: per-trade reconstruction of what the filter *would have* decided, from day one, not just from whenever the flag is later flipped on |
| M5 | `expectancy_filter_enabled` flips | `CONFIG_CHANGED` | **Already covered, no new work** — `PATCH /config` audits every risk-config change generically today. Operational recommendation, not a code change: when the operator flips this flag, put the walk-forward report filename (M6) in the change's `reason` field, so the audit trail links the config change to the validation evidence that justified it |
| M6 | Walk-forward validation runs | Filesystem, not Postgres | These are offline, operator-run analyses, not live trading state — they don't belong in `audit_events`. Write each run's report to `reports/walk-forward-<timestamp>/`, matching the existing `reports/mechanical*`/`reports/my-replay` convention already in the repo, so there's a durable, timestamped artifact to reference from the `CONFIG_CHANGED` `reason` field above |

### Tests

Every payload extension above needs its assertion updated in the same PR the field is added, following the existing pattern (this repo already asserts exact audit payload shapes, e.g. `services/admin_api/tests/test_webhooks.py`'s checks on `POSITION_CLOSED`/`POSITION_OPENED` payloads) — a field that lands in the database but not in the audit payload assertion is exactly the kind of silent gap rule 7 exists to prevent, so treat "payload has the new key" as part of each milestone's Definition of Done, not an afterthought.

---

## 6. Rollout safety (live-money system)

- **M1–M4 are additive-only and behavior-neutral.** New nullable columns, new read-only endpoints, new offline-script capabilities. None of them touch `sizing.py`'s clamp order, `RULES_IN_ORDER`'s existing 11 rules, or `exit_evaluator.py`. Safe to ship directly to the live system with normal test coverage — no special live-trading review needed beyond the usual.
- **M5 is the only milestone that can ever change what gets traded**, and it ships with that capability switched off. Enabling it is a single explicit `PATCH /config` call by the operator — never bundled into the same change that ships the rule.
- Before recommending the flag be enabled, run M6's walk-forward tool against the M5 shadow-mode data and get a clean read on stability across windows — matching the vision doc's own anti-overfitting stance, not just a single backtest run.
- No milestone reorders or removes any existing Section 9.1 rule; `expectancy_filter` is strictly appended.

---

## 7. Testing & validation strategy

- Every new Risk Engine rule gets a 1:1 unit test in the same PR (AGENTS.md §5, non-negotiable).
- `sizing.py`'s `actual_risk_usdt` gets the same property-based testing treatment PROJECT.md §12 Phase 2 already mandates for `position_size_usdt` (never negative, never exceeds `position_size_usdt`).
- `TradeScorer` gets golden-fixture regression tests, following the existing pattern in `services/llm_service/tests/fixtures/regression_*.json` used for the semantic validator — 2–3 hand-verified `MarketContext` fixtures with known expected scores.
- Add one test that feeds the same closed-trade list through both `services/common/performance.py` and `scripts/backtest/mechanical_replay.py`'s reporting path and asserts identical output — guards against the two ever silently drifting apart, which would violate the "backtest and live must be provably the same math" property this whole plan depends on.

---

## 8. Sequencing & effort estimate

| Milestone | Depends on | Rough size | Ships behavior change? |
|---|---|---|---|
| M1 — Risk & R | — | 0.5–1 day | No |
| M2 — Trade Journal | — | 1–2 days | No |
| M3 — Performance Engine (incl. FE view, same PR) | M1, M2 | 1.5–2.5 days | No |
| M4 — Regime/Score Backtest Analysis | M2, M3 | 0.5–1 day | No |
| M5 — Expectancy Filter | M3, M4 | 1–2 days | **Only after manual flag flip** |
| M6 — Validation Pipeline | M1–M4 | 1 day | No |

Recommended order: exactly M1 → M2 → M3 → M4 → M5 → M6, one PR each, per AGENTS.md's "implement one phase at a time." M5 and M6 can swap if preferred (M6's tooling is more useful once M5 exists to validate).

---

## 9. Decisions confirmed by the operator (2026-08-15)

All open questions from the original draft are resolved. Nothing below is still a question — this section is now a record, not a checklist.

1. **D1 — confirmed.** `actual_risk_usdt` (post-clamp, what was truly at stake) is the R-multiple denominator, not the nominal `equity × risk_pct` budget.
2. **D2 — confirmed.** Extend `StrategySelector`'s existing 4-way regime taxonomy (+ new volatility bucket) rather than introducing the vision doc's literal `BULL/BEAR/SIDEWAYS/HIGH_VOL/LOW_VOL` labels.
3. **D4 — confirmed.** The expectancy filter ships disabled-by-default (`expectancy_filter_enabled = False`), shadow-mode only, manual `PATCH /config` to enable. Exact shadow-period length (weeks/trades) before considering the flip is still the operator's live call at the time — not fixed here — informed by the M3 Performance view and M6 walk-forward output.
4. **Frontend Performance view — lands in the same PR as M3, not deferred.** Plan updated in Section 4 (M3) and the effort table (Section 8) accordingly.
5. **No email work.** The M6 weekly-email extension (Expectancy(R)/Profit Factor in the summary email) is dropped from this plan — the M3 Performance view covers that reporting need. Weekly email keeps its current PnL-only shape.
