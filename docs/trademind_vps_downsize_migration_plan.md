# TradeMind — VPS Downsize Migration Plan

| | |
|---|---|
| Authority | Operational plan, not a `PROJECT.md` contract change. No code/behavior changes to the system itself — infrastructure only. |
| Status | **Proposed — not started.** Decisions confirmed with operator 2026-09-12: stay on Contabo (smaller tier), cut over only when the book is flat (0 open positions). |
| Context | System is **live with real money** on Binance Spot since 2026-07-27. The original 8 vCPU sizing existed to run `qwen2.5:7b` locally on Ollama for CPU-bound LLM inference (PROJECT.md §8.4). Ollama was removed 2026-09-09 (commit `569e73a`) in favor of the Anthropic API — the CPU-bound workload that justified the current VPS size no longer exists. |
| Audience | Whoever (human or agent) executes this migration, in order. |

---

## 0. TL;DR

The current VPS is sized for a workload (local CPU inference) that no longer runs. What's left — 5 small async Python services, Postgres, Redis, Freqtrade, and an nginx-served frontend, handling **4 symbols** (BTC/ETH/SOL/XRP) on a **1h** timeframe with the LLM call offloaded to Anthropic's API — is mostly I/O-bound and idle between hourly cycles. It does not need 8 vCPUs.

**Plan:** provision a smaller Contabo VPS, build and smoke-test the stack on it in parallel (old VPS keeps trading live, untouched), then cut over during a window when there are **zero open positions**, so Freqtrade's stop-loss/ROI monitoring is never silently unprotected mid-position. Keep the old VPS stopped-but-intact for a rollback window before decommissioning it.

**Confirmed with Contabo (2026-09-12): no in-place downgrade exists.** Their own wording: *"Downgrades are not possible. If you wish to downgrade - order a new server and cancel your current one."* So the full new-VPS-then-cutover-then-cancel path below isn't the cautious option among several — it's the only option Contabo offers.

**Recommended target:** Contabo Cloud VPS "4" tier — 4 vCPU / 8 GB RAM / 100 GB SSD, ~€5.50/mo (Contabo's current smallest listed Cloud VPS tier as of 2026-09; check the control panel for anything smaller, tiers/pricing change). This is still generous headroom over the estimated real need (~2 vCPU / 4 GB) to absorb on-box `docker compose build` and Postgres/log growth. **Verify against Section 3's measured numbers before ordering** — this is an estimate from the compose file's service list, not measured data.

---

## 1. Current state (verify before relying on any of this — see [[reference_trademind_vps_access]])

- Access: `ssh -i ~/.ssh/trademind-contabo root@169.58.27.182`, deployed via `docker-compose.yml` + `.production.yml` from `/root/TradeMind/`.
- Services (`docker-compose.yml`): `postgres`, `redis`, `migrate` (one-shot), `llm_service`, `scheduler`, `freqtrade`, `risk_engine`, `admin_api`, `frontend`, `notifier`. No `ollama` service anymore.
- Stateful volumes: `postgres_data` (audit trail — `signals`/`risk_decisions`/`orders`/`positions`, source of truth), `freqtrade_data` (Freqtrade's own SQLite ledger `tradesv3.sqlite`), `redis_data` (coordination/locks/idempotency/TTL cache only — **not** system-of-record, safe to start empty on the new box).
- Live config: `SYMBOLS=BTC/USDT,ETH/USDT,SOL/USDT,XRP/USDT`, `TIMEFRAME=1h`, `SYMBOL_STAGGER_SECONDS=660`, `ANALYZE_TIMEOUT_SECONDS=650`, `LLM_PROVIDER=anthropic` (see [[project_llm_timeout_host_contention_2026-08-27]]).
- Production overlay (`docker-compose.production.yml`) already binds everything to host loopback — the new box needs the same reverse-tunnel/SSH-tunnel access pattern for the admin console, nothing new to design there.

## 2. Pre-migration checklist — run on the OLD VPS, read-only

Hand these to whoever has VPS access; nothing here changes state:

```sh
nproc                          # current vCPU count
free -h                        # current RAM headroom
df -h                          # disk usage / free space
docker stats --no-stream       # live per-container CPU/RAM
docker system df               # image/volume/build-cache disk usage
docker exec trademind-postgres-1 psql -U trademind -d trademind -c "SELECT pg_size_pretty(pg_database_size('trademind'));"
```

**Critical check — Binance API key IP restriction:** log into Binance API Management and confirm whether the live key is IP-whitelisted. If it is, this is the single most likely way this migration silently breaks (orders start failing with an auth/IP error the moment traffic moves to the new IP). Add the new VPS's IP to the whitelist *before* cutover, alongside the old one; remove the old IP only after decommissioning.

Use the measured numbers above to confirm or revise the Section 0 target tier before ordering.

## 3. Provision the new VPS

1. Order the new Contabo instance (target: Section 0, or revised per Section 2's measurements). Note its IP.
2. Harden it the same way the current box presumably is: SSH key auth only (disable password login), `ufw allow 22/tcp` (+ default deny), install `docker`, `docker compose` plugin, `git`.
3. Add a distinct SSH config alias (e.g. `trademind-contabo-new`) so both boxes are reachable during the transition — rename to the canonical alias only after cutover is verified stable.

## 4. Stage 1 — build & smoke-test on the new VPS (old VPS keeps trading, untouched)

1. `git clone` the repo, checkout `main` at the same commit the old VPS runs.
2. `scp` the old VPS's `.env` to the new VPS (contains live secrets — Binance keys, `LLM_API_KEY`, Telegram token, `ADMIN_API_KEY`, DB password, JWT secret, SMTP creds; already gitignored, never commit it).
3. **Do not bring up the trading path yet.** Start only the non-trading services first, so nothing can place an order from two boxes at once:
   ```sh
   docker compose up -d postgres redis migrate llm_service admin_api notifier frontend
   ```
4. Verify: `docker compose ps` (all healthy), `curl` `admin_api`'s `/health`, confirm `llm_service` can reach Anthropic (its own `/health`, then a real cycle later in Stage 2), confirm the frontend loads through an SSH tunnel.
5. This validates the Docker build and every healthcheck pass on the smaller box — expect `docker compose build` to take noticeably longer than on the current 8 vCPU box; budget time for it here, not during the live cutover window.

## 5. Stage 2 — cutover (only when the book is flat)

**Precondition:** confirm **0 open positions** via the admin console or Freqtrade API before starting anything below — this was the operator's explicit choice over a faster-but-riskier immediate cutover, to avoid a gap in stop-loss/ROI coverage on an open position.

1. On the **old** VPS, stop only the trading-path services (leave Postgres up for the dump):
   ```sh
   docker compose stop scheduler risk_engine freqtrade
   ```
2. On the **old** VPS, take a Postgres dump and grab the Freqtrade SQLite ledger (stop `freqtrade` first, done above, so the file isn't mid-write):
   ```sh
   docker exec trademind-postgres-1 pg_dump -U trademind -d trademind -Fc -f /tmp/trademind.dump
   docker cp trademind-postgres-1:/tmp/trademind.dump /root/trademind.dump
   docker cp trademind-freqtrade-1:/freqtrade/db/tradesv3.sqlite /root/tradesv3.sqlite
   ls -la /root/trademind.dump /root/tradesv3.sqlite   # confirm both exist and aren't 0 bytes before moving on
   ```
   (Absolute `/root/...` paths on purpose — `docker cp`'s destination is relative to whatever directory you happened to `cd` into over SSH, and a mismatch there is exactly what breaks the `scp` step below with "No such file or directory".)
3. `scp` both files to the new VPS — run this from your **local machine**, not from either VPS, e.g.:
   ```sh
   scp -i ~/.ssh/trademind-contabo root@<OLD_IP>:/root/trademind.dump ~/Downloads/
   scp -i ~/.ssh/trademind-contabo root@<OLD_IP>:/root/tradesv3.sqlite ~/Downloads/
   scp -i ~/.ssh/<new-vps-key> ~/Downloads/trademind.dump root@<NEW_IP>:/root/
   scp -i ~/.ssh/<new-vps-key> ~/Downloads/tradesv3.sqlite root@<NEW_IP>:/root/
   ```
4. On the **new** VPS: restore the dump into the (already-running) `postgres` container, and copy the ledger into the `freqtrade_data` volume:
   ```sh
   docker cp ./trademind.dump trademind-postgres-1:/tmp/trademind.dump
   docker exec trademind-postgres-1 pg_restore -U trademind -d trademind --clean --if-exists /tmp/trademind.dump
   docker run --rm -v trademind_freqtrade_data:/data -v "$PWD":/backup alpine cp /backup/tradesv3.sqlite /data/tradesv3.sqlite
   ```
   Verify row counts match the old box (e.g. `SELECT count(*) FROM signals;` / `orders` / `positions`) before proceeding.
5. Confirm the new VPS's IP is on Binance's key whitelist (Section 2).
6. On the **new** VPS, bring up everything:
   ```sh
   docker compose up -d
   ```
   Confirm `migrate` exits 0 (idempotent against the already-current schema).
7. Verify before declaring done: `freqtrade`'s `/api/v1/ping` and `/api/v1/balance` (must match actual Binance balance — this is exactly what the existing reconciliation job checks), `admin_api`'s `/status` shows the correct latest state, Telegram notifier delivers the next real event, the scheduler picks up the next hourly candle on schedule.
8. On the **old** VPS: `docker compose down` (stop everything, but do **not** delete volumes yet — that's the rollback safety net).
9. Watch the next 2–3 full candle cycles closely (Telegram + admin console + `docker logs -f`) before considering the new box stable.

## 6. Rollback

If anything looks wrong on the new box within the first cycle or two: `docker compose down` on the new VPS, `docker compose up -d` on the old VPS (nothing there was deleted or altered beyond the Section 5.1 `stop`), remove the new VPS's IP from the Binance whitelist if it was added, investigate offline before retrying.

## 7. Post-migration

- Keep the old VPS **stopped but not deleted** for a few days as the rollback net (weigh ongoing cost vs. safety window — a snapshot + earlier cancellation is a cheaper alternative once confidence is high).
- Once the new VPS has run stable through several candle cycles, take a Contabo **snapshot of the new VPS** (its Cloud VPS 4 tier includes 1 snapshot slot) as a cheaper substitute for keeping the old VPS running:
  1. Log into `my.contabo.com` → **Your Services → VPS** → select the new instance.
  2. Open its **Snapshots** tab → **Create Snapshot**, name it something dated/descriptive (e.g. `post-cutover-stable-2026-09-xx`).
  3. Takes a few minutes; the tier's single slot means a later snapshot **overwrites** this one — there's no history, just one rolling checkpoint.
  4. Postgres/SQLite are crash-consistent (WAL-based), so a live snapshot without stopping containers is acceptable; briefly `docker compose stop` first only if maximum caution is wanted.
  5. Restoring (same Snapshots tab → **Restore**) reverts the *entire disk* to that point-in-time — anything written after the snapshot is lost. Only use it to recover from a bad deploy/operator mistake, not as a rolling backup strategy.
  This snapshot is a checkpoint of the *new* box, not a replacement for Section 6's rollback (which relies on the untouched old VPS) during the cutover window itself — take it only after the new box is already trusted.
- After the confidence window: cancel/downgrade the old VPS, remove its IP from the Binance whitelist.
  - **No refund applies** — Contabo only refunds within 14 days of a new order or a renewal paid in the last 72 hours; an established VPS cancelled outside that isn't eligible. Cancelling doesn't stop it immediately either — it stays active through the end of the already-paid period, then deactivates. Net effect: keeping the old VPS as the rollback net for a few extra days costs nothing beyond what's already paid, as long as cancellation happens before the next renewal date. Cancelling also **permanently deletes any snapshots/backups** on that VPS — pull anything worth keeping first.
- Update [[reference_trademind_vps_access]] with the new SSH command/IP.
- Update anything else that still points at the old IP (SSH config aliases, any bookmarked tunnel commands, README/CLAUDE.md if either mentions it).

## 8. Open risks to double-check before executing

- **Binance IP whitelist status is unknown** until checked (Section 2) — the single most likely silent failure mode.
- The SQLite ledger copy (Section 5.2/5.4) must happen only while `freqtrade` is stopped on both ends, to avoid copying a mid-write file.
- `docker compose build` will be slower on a smaller box — do it in Stage 1, not during the live cutover window.
- Section 0's sizing (2 vCPU / 4 GB estimated need, 4 vCPU / 8 GB recommended tier) is derived from the compose file's service list, not from measured production usage — Section 2's numbers should confirm or revise it before ordering.
