# AGENTS.md

Guidance for AI coding agents (Claude Code, Codex, Cursor, Gemini CLI, etc.) contributing to this repository.

## 1. Purpose

`PROJECT.md` is the authoritative specification for this system — architecture, trust zones, domain models, contracts, risk rules, and phased scope. **Read it before making any change.** This document does not restate that architecture; it tells you how to work inside it.

If an implementation decision conflicts with `PROJECT.md`, either conform to it or update the document first, in the same PR (see Section 6).

## 2. Working Principles

- **Implement one phase at a time**, per the phase table in `PROJECT.md` Section 12. Do not jump ahead to a later phase's scope (e.g. don't wire real order execution while still in Phase 1).
- **Keep changes small and reviewable.** One logical change per PR/commit series. If a task looks like it spans multiple phases or services, split it.
- **Prefer clarity over cleverness.** This is a financial-adjacent system with a human operator who must be able to read the code during an incident. Optimize for that reader, not for brevity or abstraction.
- **Maintain backward compatibility** for existing contracts (API responses, domain model shapes, Redis key schema, env vars) unless the task explicitly changes a contract — and if it does, update `PROJECT.md` and any dependent services in the same change.
- Don't build for hypothetical future requirements (multi-exchange, multi-provider, live trading, etc.) — these are explicit non-goals in `PROJECT.md` Section 2.2 until the document says otherwise.

## 3. Coding Standards

- **Strong typing** everywhere — type hints on all function signatures, no untyped public interfaces.
- **Pydantic at every boundary**: HTTP request/response bodies, Redis message payloads, LLM input/output. No raw `dict` crossing a service boundary.
- **Business logic belongs in services** (`app/` modules, `rules/`, `sizing.py`, etc.) — never in route handlers or CLI entrypoints.
- **Thin API controllers.** FastAPI routers only parse, delegate, and shape the response; they contain no rule evaluation, sizing, or persistence logic themselves.
- **Structured logging** (JSON, one event per line) with `trace_id` included on every log line inside a trading-cycle code path.
- **UTC timestamps** everywhere, `timestamptz` in Postgres, no naive `datetime` objects, no local-time formatting in persisted data.
- **No hardcoded secrets.** Config and credentials come from environment variables only (`common/config.py`); `.env` is never committed.
- **Small, testable modules.** One rule per file in `risk_engine/app/rules/`, one provider per file in `llm_service/app/providers/` — match the module boundaries already laid out in `PROJECT.md` Section 6. If a change doesn't obviously belong in an existing module, treat that as a signal to reconsider the change before adding a new one.

## 4. Architecture Rules

These are hard constraints, not preferences — see `PROJECT.md` Section 14 for the full list and rationale.

- **Never let the LLM call Binance.** No exchange credentials, no network path to Binance or Freqtrade from `llm_service`.
- **Never bypass the Risk Engine.** Every signal that could result in a trade must pass through Risk Engine evaluation — no shortcuts, no direct `forceenter` calls from anywhere else.
- **Never let Freqtrade make AI decisions.** `ExternalSignalStrategy` has no autonomous entry logic; entries occur only via authenticated calls from `risk_engine`.
- **Never calculate position size inside the LLM.** Sizing is computed exclusively in `risk_engine/app/sizing.py`. The LLM's output schema has no numeric sizing field — don't add one.
- **Preserve the separation of responsibilities** across the three trust zones (Isolated / Core Trading / Administration). If a change would require a new cross-zone dependency or credential, stop and flag it — that's a scope change to `PROJECT.md`, not a routine implementation detail.

## 5. Testing Rules

- **Add or update tests for every feature.** No behavioral change merges without test coverage for that behavior.
- **Unit tests before integration tests.** Cover a rule, function, or validator in isolation before writing a full-stack scenario in `tests/integration/`.
- Every Risk Engine rule (`PROJECT.md` Section 9.1) requires a 1:1 unit test in the same PR that introduces or modifies it.
- **Never remove or weaken a test to make a build pass.** If a test is wrong, fix the test with a clear explanation of why in the commit message — don't delete it to unblock yourself.
- **Do not merge code with failing tests.** A red test suite blocks the change, full stop.

## 6. Git Workflow

- **Keep commits focused** — one concern per commit, matching the "small and reviewable" principle above.
- **Use descriptive commit messages** that explain *why*, not just *what*.
- **Avoid unrelated refactoring** inside a feature or fix commit. If you notice something worth cleaning up, do it as a separate, clearly labeled commit or call it out rather than folding it in silently.
- **Update `PROJECT.md` in the same PR** whenever a change alters a contract, adds scope, or changes a rule described there (Section 2.2, Section 14 item 14). Documentation drift is treated as a bug.

## 7. Safety Rules

- **Default to `HOLD` on uncertainty.** Any ambiguous, malformed, timed-out, or low-confidence signal resolves to `HOLD` — never a best-effort `BUY`/`SELL` guess. No `try/except` around the LLM call chain may default to anything else.
- **No trade may occur if any required dependency fails.** Redis, Postgres, or Freqtrade being unavailable means the system fails closed (`PROJECT.md` Section 9.4) — never fails open into a silent approval.
- **Never disable risk checks.** The kill switch check is always the first gate; no Section 9.1 rule may be skipped, reordered ahead of it, or bypassed for convenience (including in tests that exercise real code paths).
- **Never enable live trading unless explicitly requested by the user.** `DRY_RUN=true` is the MVP default; flipping it is a deliberate human decision gated by explicit confirmation, never a side effect of an unrelated change.
- **Never expose secrets in logs, error messages, or API responses.** Redact credentials and tokens before they can reach structured logs or Telegram notifications.

## 8. When Unsure

1. Follow `PROJECT.md` — it is the tiebreaker for any architectural or contract question.
2. If the document doesn't answer the question, make the **safest reasonable assumption** (fail closed, prefer `HOLD`, prefer rejecting a trade over approving one, prefer smaller scope).
3. **Ask for clarification only when a missing decision actually blocks implementation** — not for style preferences or anything you can infer from existing patterns in the codebase.

## 9. Definition of Done

A change is complete only when all of the following hold:

- [ ] Code builds successfully (services start, no import/type errors).
- [ ] Tests pass, including any new ones required by Section 5.
- [ ] Documentation is updated — `PROJECT.md` if a contract or rule changed, inline docs if a module's purpose changed.
- [ ] Structured logging is included for new state-changing code paths.
- [ ] Error handling is implemented, and it fails closed per Section 7.
- [ ] No `TODO` is left without an explanation of what remains and why it's deferred.
