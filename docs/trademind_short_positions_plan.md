# TradeMind — Short Positions (Long + Short): Implementation Plan

| | |
|---|---|
| Authority | `PROJECT.md` is the single source of truth for contracts. This plan proposes changes to it; it does not supersede it. Each work item lists the exact `PROJECT.md` / `docs/ROADMAP.md` sections it must update in the same PR (AGENTS.md §6). |
| Status | **Proposed — not started.** Tracked as [#13](https://github.com/quang-ng/TradeMind/issues/13). Supersedes the deferred 2026-07-30 plan (`~/.claude/plans/groovy-frolicking-hellman.md`); file references below re-verified against the codebase on 2026-08-28. |
| Context | System is **live with real money** on Binance **Spot**, long-only, since 2026-07-27 (~$624 equity). The Positive-Expectancy build (M1–M6, `docs/trademind_positive_expectancy_implementation_plan.md`) is mid-flight: M1+M2 sit in unmerged PR #2, M3–M6 unbuilt. Short is currently listed under "Explicitly not on this roadmap" (ROADMAP.md §6). |
| Audience | Whoever (human or agent) implements this, in order. |

---

## 0. TL;DR and recommendation

**What the operator wants:** the system should be able to open **SHORT** positions, not just LONG — "ăn cả 2 chiều" (profit in both directions).

**The only clean mechanism is Binance USDⓈ-M Futures.** Binance Spot cannot short. Freqtrade's `can_short` only works under `trading_mode: "futures"`; its margin-trading mode is no longer supported. So this is not "add a flag" — it changes the exchange product, adds leverage/liquidation/funding mechanics, and doubles the LLM's decision rubric surface (a short-side rubric that has never been validated against live data, unlike the long rubric which was tuned on real model behaviour — see the 2026-07-24 exit-logic incident).

**Recommended sequencing — do NOT start this now:**

1. Land **PR #2** (M1+M2) first — this plan adds columns to `RiskDecision`/`Position` and it must not collide with an unmerged migration.
2. Ship **M3 (Performance Engine)** next. M3 gives per-cohort expectancy in R. Adding a `direction` breakdown to M3 is ~an hour of work and means that from the very first short trade you can see LONG vs SHORT expectancy separately. Going short-live without that is flying blind.
3. *Then* build this plan, and ship it **behind a config flag defaulting off**, with a mandatory dry-run-futures soak + shadow-logging period before the flag is flipped for real money.

The rest of this document is the "how" for step 3. It is sequenced as small, independently shippable, mostly-additive PRs per AGENTS.md.

---

## 1. What "long-only" is load-bearing in today

`PROJECT.md` §2.2 ("Margin, futures, leverage, or short positions (long-only spot)") is enforced, implicitly, in far more places than that one line suggests:

| Layer | File | What assumes long-only |
|---|---|---|
| Exchange product | `freqtrade/user_data/config.json.tpl:11` | `"trading_mode": "spot"` — cannot short at all |
| Strategy | `freqtrade/user_data/strategies/ExternalSignalStrategy.py:92` | `can_short = False`; `populate_entry_trend` sets only `enter_long`; `custom_stoploss` derives the stop as `open_rate * (1 - dist)` and trails behind `trade.max_rate` (the *peak*, i.e. a long's favourable extreme) |
| Order submission | `risk_engine/app/freqtrade_client.py:48` | `forceenter` payload hardcodes `"side": "long"` |
| Intent resolution | `risk_engine/app/main.py:80` | `if action == SELL: exit-pipeline else: entry-pipeline` — SELL can only mean "close a long", never "open a short" |
| Order side | `risk_engine/app/main.py:347,420` | entry `Order.side` hardcoded `BUY`, exit hardcoded `SELL` |
| Sizing | `risk_engine/app/sizing.py:71` | `stop_loss_price = entry_price * (1 - stop_distance_pct)` |
| Account state | `risk_engine/app/schemas.py:34` | `open_position_symbols: frozenset[str]` — tracks *whether* a symbol has a position, not *which way* |
| Exit gate | `risk_engine/app/exit_evaluator.py:62` | `signal.symbol not in account.open_position_symbols` → `NO_POSITION_TO_EXIT` |
| DB schema | `common/db/models.py:113` (`Position`), `:91` (`Order`) | `Position` has no direction column; `Order.side` is the raw `BUY`/`SELL` exchange direction, which for a long *is* the position side but for a short is the opposite |
| Webhook order matching | `admin_api/app/routers/webhooks.py:52,100,197` | `_find_order` matches on `Order.side`; `_handle_entry_fill` looks up the `BUY` order, `_handle_exit_fill` the `SELL` order — inverted for shorts |
| LLM contract | `llm_service/app/models/wire.py:44` | `PositionContext.has_open_position: bool` — no direction |
| LLM rubric | `llm_service/app/validators/semantic.py`, `prompts/v1.py:32` | "BUY means opening, SELL means closing"; bearish evidence with no position → HOLD; PnL bands assume a long's sign |
| Live PnL marks | `scheduler/app/jobs.py:135`, `admin_api/app/position_service.py:19` | `(current - entry)` — a long's unrealised-PnL formula |
| Frontend | `frontend/src/App.tsx` (position tables, `position_context` type ~L781) | no direction badge; unrealised-PnL card assumes long |

Note: the Positive-Expectancy layer that *does* exist is mostly direction-agnostic already — `RiskDecision.actual_risk_usdt = position_size_usdt * stop_distance_pct` and `Position.r_multiple = profit_amount / actual_risk_usdt` both work for a short unchanged, because `profit_amount` comes signed straight from Freqtrade's webhook (`webhooks.py:278`). That is the one piece that does *not* need touching.

---

## 2. Design decisions (carried from the 2026-07-30 plan — re-confirm before building)

| # | Decision | Rationale |
|---|---|---|
| **D1** | **Mechanism: Binance USDⓈ-M Futures, perpetual, isolated margin.** | Only Freqtrade mode that supports `can_short`. Isolated (not cross) margin caps the blast radius of any one position at its own stake. |
| **D2** | **Scope: unified long+short, one position per pair.** Every configured pair holds at most one open position, LONG *or* SHORT, chosen by the same signal→risk pipeline. Not a second parallel short system. | Keeps the mental model and the audit trail single-threaded per pair; matches how `max_open_positions` / cooldown / exposure already reason per-symbol. |
| **D3** | **Leverage: 1× isolated by default**, as a Risk-Engine-enforced, `PATCH /config`-editable ceiling (`RiskConfig.max_leverage`). | Mirrors today's fixed-fractional risk model. At 1× the ATR stop is nowhere near liquidation; the ceiling exists so a future increase can't silently remove that safety margin. |
| **D4** | **Ship behind `RiskConfig.shorts_enabled` (default `False`).** With it off, a bearish signal on no position resolves to HOLD exactly as today — zero behaviour change until the operator flips it via the audited `PATCH /config` path. | Same "the code that ships the capability is not the change that activates it" rule as the M5 expectancy filter (plan D4). |
| **D5** | **`unrealized_pnl_pct` becomes direction-aware profit%** at every point it is produced (scheduler mark, admin mark), so downstream consumers (`semantic.py`, frontend, `GET /positions`) keep reading "positive = winning" regardless of side. | Avoids sprinkling `if side == SHORT` through every PnL consumer; localises the sign flip to two `position_with_mark`-style functions. |

**Open question for the operator:** futures funding rates are a real cost on perpetuals (typically ±0.01%/8h, occasionally much larger). At 1× and short hold times it's minor, but it is a cost the current fee model (`estimated_fee_pct`, round-trip only) does not capture. Options: (a) ignore for MVP and revisit if hold times grow, (b) add a crude funding estimate to `_estimate_fees_usdt`. Recommendation: **(a)**, note it as a known gap.

---

## 3. Core design insight — resolve intent from position state, mirror the confirmations

Today `Action.BUY`/`SELL` carry *both* a directional opinion *and* an implicit open/close intent (only unambiguous because only LONG exists). Once a position can be LONG or SHORT, **open/close intent must be resolved from the current position, not the action:**

| Current position | Action | Resolved intent |
|---|---|---|
| none | BUY | open LONG *(today)* |
| none | SELL | **open SHORT** *(new — gated on `shorts_enabled`)* |
| LONG | SELL | close LONG *(today)* |
| SHORT | BUY | **close SHORT** *(new — mirror of close-long)* |
| LONG | BUY | contradicts position → HOLD / reject |
| SHORT | SELL | contradicts position → HOLD / reject |

The confirmation facts `ContextBuilder` already computes are **direction-neutral** and get reused symmetrically — no new fact categories:

- `entry_confirmations` (bullish: price>EMAs, EMA50>EMA200, bullish MACD, RSI 45–70, higher highs/lows, rising vol) → **open LONG** *and* **close SHORT**
- `exit_confirmations` (bearish: the mirror set) → **close LONG** *and* **open SHORT**

The asymmetry stays **per intent, not per direction**: **3 confirmations incl. trend+momentum to OPEN** (either side), **2 cross-category + PnL outside the cushion to CLOSE** (either side).

Regime suppression is *already symmetric for free*: `StrategySelector` labels a strong aligned **downtrend** as `TREND_FOLLOWING` just like a strong uptrend (`selector.py:57-64`). So the existing "suppress a fresh entry when the trend is already extended" check (`semantic.py:184`) applies to short entries with no change — a late chase down is suppressed the same way a late chase up is.

---

## 4. Schema & migration

New migration `migrations/versions/20260828_0001_position_side.py` (follow the style of `20260827_0001_performance_snapshots.py`), **additive, backfilled, no behaviour change on its own:**

| Change | Detail |
|---|---|
| `common/enums.py` | new `class PositionSide(str, Enum): LONG / SHORT`. Add `RejectionReason.LIQUIDATION_RISK` and `RejectionReason.POSITION_DIRECTION_CONFLICT`. |
| `Position.side` | `Mapped[PositionSide]`, `String`, NOT NULL. Migration: add nullable → `UPDATE positions SET side='LONG'` → set NOT NULL. |
| `Order.position_side` | `Mapped[PositionSide]` — *which side of the position* this order serves. Distinct from `Order.side` (raw exchange `BUY`/`SELL`): opening a SHORT is a `SELL` order with `position_side=SHORT`; closing it is a `BUY` order with `position_side=SHORT`. Backfill: `position_side = 'LONG'` for every existing row (today `side=BUY`↔entry-of-long, `side=SELL`↔exit-of-long). |
| `Order.intent` | `Mapped[Literal["ENTRY","EXIT"]]` (or reuse a bool `is_entry`). Needed because the webhook can no longer infer entry-vs-exit from `BUY`/`SELL` alone. Backfill from `side` (`BUY`→ENTRY, `SELL`→EXIT). |
| `RiskConfig` (`common/config.py`) | `max_leverage: Decimal = Decimal("1")`; `shorts_enabled: bool = False`. Env-sourced defaults, overridable through the existing `RiskConfigState` / `PATCH /config` mechanism — no new plumbing. |

`PROJECT.md` §7.3/§7.4 tables and §9.1 updated in the same PR.

---

## 5. Work breakdown by layer

Ordered so each PR leaves the system consistent and testable. PRs 1–2 are safe to merge while `shorts_enabled=False` with zero live effect.

### PR 1 — Domain model + migration
Schema changes from §4. No logic yet. `PositionSide`/`Order.position_side`/`Order.intent` added, everything backfilled `LONG`/`ENTRY`. Update `PROJECT.md` §7.

### PR 2 — Freqtrade futures plumbing (still long-only behaviour)
- `config.json.tpl`: `"trading_mode": "futures"`, add `"margin_mode": "isolated"`. Futures pairs are contract-suffixed (`BTC/USDT:USDT`) in `pair_whitelist` — translate in `docker-entrypoint.sh` where `PAIR_WHITELIST_JSON` is rendered, so the rest of the system keeps using the plain `BTC/USDT` form it uses everywhere (DB, LLM contract, admin API, frontend).
- `ExternalSignalStrategy.py`: `can_short = True`; `populate_entry_trend`/`populate_exit_trend` also zero `enter_short`/`exit_short` (still no autonomous entries); add a `leverage()` callback returning `min(proposed, config max, 1.0)`; `custom_stoploss()` — pass `is_short=trade.is_short` into `stoploss_from_absolute()`, derive the stop as `open_rate * (1 + dist)` for shorts, and trail behind `trade.min_rate` (short's favourable extreme) instead of `trade.max_rate`.
- `freqtrade_client.py::forceenter`: add `side: Literal["long","short"] = "long"`, pass through to the payload (currently hardcoded).
- **Operational prerequisite (cannot be done from the repo):** the Binance API key must have **Futures trading permission enabled**. Dry-run does not bypass Binance's own API-key permission check. Call this out to the operator before this PR deploys.
- Verify in dry-run that a normal long still opens/closes/stops identically under `futures` mode before moving on.

### PR 3 — Risk Engine: account state + direction resolution
- `schemas.py`: `AccountState.open_position_symbols: frozenset[str]` → `open_position_sides: dict[str, PositionSide]`. Update the 4 consumers (`exit_evaluator.py:62`, `rules/max_open_positions.py:10-11`, and the test factory `tests/factories.py:37`) — `symbol in X` becomes `symbol in X` / `X.get(symbol)`.
- `account_state.py::load_account_state`: source `open_position_sides` from `Position.side` on the query that already loads open positions (`:47`).
- `main.py::process_signal`: replace the `action == SELL` branch (`:80`) with the §3 resolution table:
  - no position + BUY → entry pipeline, `side=LONG`
  - no position + SELL → **if `config.shorts_enabled`**: entry pipeline, `side=SHORT`; **else**: reject `SIGNAL_WAS_HOLD` (unchanged behaviour)
  - LONG + SELL → exit pipeline (unchanged)
  - SHORT + BUY → exit pipeline
  - LONG + BUY / SHORT + SELL → reject `POSITION_DIRECTION_CONFLICT` (defense-in-depth; the semantic layer should already have normalised these to HOLD, but the Risk Engine never trusts that).
- `_submit_entry_order` / `_submit_exit_order`: set `Order.side` and `Order.position_side` from the resolved direction (`SHORT` entry → `side=SELL, position_side=SHORT, intent=ENTRY`; `SHORT` exit → `side=BUY, position_side=SHORT, intent=EXIT`). Pass `side` to `forceenter`. The `slpct:` tag math (`main.py:316`) — see PR 4.

### PR 4 — Risk Engine: sizing + liquidation buffer
- `sizing.py::compute_sizing`: add `side: PositionSide`. `stop_loss_price` = `entry_price * (1 - dist)` for LONG, `entry_price * (1 + dist)` for SHORT. Same ATR-based `stop_distance_pct` clamp for both. `actual_risk_usdt` / `nominal_risk_amount_usdt` unchanged (both direction-agnostic).
- `evaluator.py`: thread `side` from the resolved intent into `compute_sizing`. The rule pipeline (`RULES_IN_ORDER`) is otherwise direction-agnostic.
- `main.py::_submit_entry_order`: the `stop_distance_pct = 1 - (stop_loss_price / price)` reconstruction (`:316`) is long-only — recompute it as `abs(stop_loss_price / price - 1)` or carry `stop_distance_pct` through directly from the `RiskDecision` (it's already persisted since M1).
- New rule `rules/liquidation_buffer.py` + `test_liquidation_buffer.py` (AGENTS.md §5, 1:1): reject `LIQUIDATION_RISK` if the candidate `stop_distance_pct` does not sit safely inside the liquidation distance implied by `config.max_leverage` at isolated margin. At 1× this never fires; it guards a future leverage bump. Add to `RULES_IN_ORDER` right after `insufficient_balance` (same "defense-in-depth, not primary gate" placement).
- `exit_evaluator.py`: **no logic change** — it's symbol-keyed and direction-agnostic already. Add a test proving it approves closing a SHORT the same way it approves closing a LONG.

### PR 5 — LLM contract + rubric
- `wire.py`: `PositionContext.has_open_position: bool` → keep it, **add** `position_side: Literal["LONG","SHORT"] | None = None` (additive; `has_open_position` stays for back-compat / older persisted signals). Update `PROJECT.md` §8.1 example.
- `scheduler/app/jobs.py`: pass `open_position.side` into `_build_analyze_payload` → `position_context.position_side`. Make `unrealized_pnl_pct` direction-aware (D5): `(latest_c - entry)/entry` for LONG, `(entry - latest_c)/entry` for SHORT.
- `validators/semantic.py`: generalise the branching to the §3 table. Concretely:
  - **no position + SELL + `shorts_enabled`** → treat as an *open* request: require `len(exit_confirmations) >= 3` spanning trend+momentum (mirror of the current BUY entry bar at `:177-182`), plus the same `regime != TREND_FOLLOWING` suppression (works for downtrends unchanged). Otherwise normalise to HOLD.
  - **SHORT position + BUY** → treat as a *close* request: require `len(entry_confirmations) >= 2` across ≥2 categories AND PnL outside the cushion (mirror of the current SELL-to-close bar at `:113-118`).
  - **SHORT position + hard loss** → the `hard_loss_cut_pct` backstop (`:88`) already works once `unrealized_pnl_pct` is direction-aware (D5); `key_indicators` should cite `entry_confirmations` (the bullish reversal) instead of `exit_confirmations`.
  - `shorts_enabled=False` → the "no position + SELL → HOLD" path is exactly today's behaviour; keep it as the default branch.
  - Thread `shorts_enabled` in as a parameter (the pipeline already passes config-ish knobs like `min_exit_profit_pct`).
- `prompts/v1.py`: rewrite rubric rule 1 (drop "long-only system") and add the mirrored open-short / close-short rules symmetric to rules 2/3. Keep the 3-to-open / 2-to-close asymmetry. Do not add worked examples (small-model mimicry risk, per the existing note).
- Fixtures: add `bearish-no-position` (→ open short) and `bullish-short-position` (→ close short) to whatever test currently covers `semantic.py` + the long/no-position rubric. Update `PROJECT.md` §8.5.

### PR 6 — Admin API + frontend
- `admin_api/app/schemas.py`: `PositionOut` and `OrderOut` gain `side` / `position_side`.
- `position_service.py::position_with_mark`: `unrealized_pnl = (current - entry) * amount` → branch on `position.side` (SHORT flips the sign). `unrealized_pnl_pct` likewise (D5).
- `admin_api/app/routers/webhooks.py`: `_find_order` / `_handle_entry_fill` / `_handle_exit_fill` must key on `Order.intent` (ENTRY/EXIT) + `freqtrade_trade_id`, not on `Order.side` — for a short, the entry fill matches a `SELL` order and the exit fill a `BUY` order. `_handle_entry_fill` sets `Position.side` from the matched entry order's `position_side`. Realised PnL still comes straight from `payload.profit_amount` (already signed correctly by Freqtrade).
- `frontend/src/App.tsx`: extend the `position_context` type with `position_side`; add a LONG/SHORT badge next to the symbol in the open + closed position tables (reuse the positive/negative-text styling pattern); the unrealised-PnL card already reads `unrealized_pnl_usdt` from the API, so D5 fixes it server-side.

### PR 7 — Performance Engine `direction` breakdown *(only if M3 already shipped)*
Add `direction` (LONG/SHORT) as a breakdown dimension alongside symbol/regime/score in `common/performance_query.py` and the `GET /performance` filters, and a LONG-vs-SHORT split in the frontend Performance view. Cheap, and it is the only way to know whether shorts are actually net-positive.

### PR 8 — Docs
`PROJECT.md`: §2.1/2.2 (scope now includes USDⓈ-M Futures, isolated margin, unified long+short, `max_leverage` ceiling; remove "long-only spot", keep "cross margin / hedge mode / leverage beyond the ceiling" as explicit non-goals), §7.3/7.4, §8.1/8.5, §9.1/9.2, §6 (new `rules/liquidation_buffer.py`). `docs/ROADMAP.md`: move short off §6 and into a real phase; note the M3 `direction` dependency.

---

## 6. Rollout — for a live-money system

`AGENTS.md` §7: "Never enable live trading … unless explicitly requested." Same bar here — `shorts_enabled=True` on real money is a deliberate, separate, audited step.

1. **PRs 1–6 merged, `shorts_enabled=False`, deployed.** Verify longs behave identically under futures mode (open, ATR stop, trailing stop, ROI, LLM-SELL close, webhook reconciliation, weekly email). Run for at least a week of normal long trading. No shorts yet.
2. **Dry-run futures soak.** Point a second Freqtrade/`risk_engine` at `DRY_RUN=true` futures with `shorts_enabled=True` (or flip the live one to dry-run for a defined window). Let it take simulated shorts. Watch: does the short rubric actually fire on clean downtrends? Do stops sit on the correct side? Does the webhook create a `Position(side=SHORT)` that closes cleanly with correct signed PnL and R? Does `GET /positions` show the right unrealised PnL sign?
3. **Shadow log on live (optional but recommended).** With `shorts_enabled` still off, have `semantic.py` compute *what it would have done* for no-position-SELL cases and log a `short_shadow` line (mirrors the M5 expectancy-filter shadow pattern). Accumulate a few weeks. Reconstruct forward outcomes — would those shorts have won?
4. **Flip `shorts_enabled=True` via `PATCH /config`** (audited `CONFIG_CHANGED` event), at 1× leverage, and **only after** the shadow data and dry-run soak both look sane. Keep position sizing untouched — do not raise risk because the system "can now do more."
5. Re-evaluate leverage (`max_leverage`) only much later, only through the M6 walk-forward harness, never by hand.

---

## 7. Testing

- `risk_engine/tests/`: direction-resolution matrix in `test_main`/`test_evaluator` (all 6 rows of the §3 table, both `shorts_enabled` states); `test_exit_evaluator` close-a-short case; `test_sizing` SHORT stop-above-entry + the property test extended so size never exceeds `max_position_pct`/free balance for SHORT; new `test_liquidation_buffer.py`.
- `risk_engine/tests/test_freqtrade_client.py`: `forceenter(side="short")` payload assertion.
- `llm_service/tests/`: the two new fixtures (open-short, close-short); direction-aware PnL sign in the `semantic.py` tests; `shorts_enabled=False` still normalises no-position-SELL → HOLD.
- `admin_api/tests/`: webhook entry/exit fill for a SHORT trade creates and closes `Position(side=SHORT)` with correctly signed PnL/R; `position_with_mark` sign for a short; `PositionOut.side` surfaced.
- `tests/integration/`: one full-stack scenario — bearish-no-position signal → `Position(OPEN, SHORT)` visible identically in Postgres and the Freqtrade UI → bullish reversal → clean close with signed PnL. Mirrors the existing long end-to-end scenario.

---

## 8. Risks

| Risk | Mitigation |
|---|---|
| Short rubric never validated on real model behaviour (unlike the long rubric) | Dry-run soak + shadow log before activation (§6 steps 2–3); `shorts_enabled` default off |
| Futures mode subtly changes long behaviour (fees, margin, contract symbols) | PR 2 ships futures plumbing with behaviour still long-only; §6 step 1 is a dedicated long-regression week |
| Webhook order-matching breaks (entry=SELL for shorts) | `Order.intent` column + match on trade-id, not raw side; explicit short webhook tests (PR 6) |
| Liquidation risk if leverage is ever raised | `liquidation_buffer` rule ships now, no-op at 1×; leverage change gated on M6 |
| Funding-rate cost not modelled | Accepted gap for MVP (§2 open question); revisit if hold times grow |
| Collides with unmerged PR #2 migration | Hard dependency: PR #2 lands first (§0) |
| Doubles LLM rubric surface while the LLM is already the system's bottleneck | No extra LLM calls added — same one `/analyze` per cycle; only the deterministic validator branches grow |
