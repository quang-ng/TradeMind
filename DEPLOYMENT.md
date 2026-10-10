# TradeMind deployment and monitoring

TradeMind is an MVP dry-run system. `DRY_RUN=true` is mandatory. Deploy it on
one Docker host and keep PostgreSQL, Redis, Freqtrade, and the LLM service off
the public network.

## Prerequisites

- Docker Engine with the Compose plugin
- 4 CPU, 8 GB RAM, and persistent SSD storage recommended for the core stack
  (Postgres, Redis, Freqtrade, admin services, operator console)
- the default `LLM_PROVIDER=anthropic` needs only a workspace-scoped
  `LLM_API_KEY` and no extra hardware. `LLM_PROVIDER=ollama` (self-hosted
  local model) is retained in code but **not** in `docker-compose.yml` — CPU
  inference on a shared VPS could not keep the `/analyze` budget (PROJECT.md
  Section 8.4). To run it, re-add an `ollama` service on `llm_net`, budget
  8+ GB RAM for a 7-8B Q4 model plus an NVIDIA GPU (`nvidia-container-toolkit`)
  unless calls are very infrequent, and point `OLLAMA_BASE_URL` at it.
- a VPN, SSH tunnel, or TLS reverse proxy for the Admin API
- off-host encrypted backup storage

## First deployment

Create the environment file and replace every blank secret:

```bash
cp .env.example .env
chmod 600 .env
openssl rand -hex 32
```

At minimum configure `POSTGRES_PASSWORD`, `LLM_API_KEY`, Freqtrade API
credentials, `FREQTRADE_JWT_SECRET`, `ADMIN_API_KEY`,
`WEBHOOK_SHARED_SECRET`, and Telegram credentials. Confirm `DRY_RUN=true`.

Validate and start the production composition:

```bash
uv run ruff check .
uv run pytest
docker compose -f docker-compose.yml -f docker-compose.production.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.production.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.production.yml ps
```

The one-shot `migrate` service runs Alembic after PostgreSQL is healthy.
Scheduler, Risk Engine, Admin API, and Notifier will not start if migration
fails. The scheduler runs each symbol in `SYMBOLS` (default: BTC/USDT, ETH/USDT,
BNB/USDT, USDC/USDT), staggered 70 seconds apart starting 15 seconds after
each five-minute candle closes by default.

Verify the API from the Docker host:

```bash
curl --fail http://127.0.0.1:8000/health
curl --fail -H "Authorization: Bearer ${ADMIN_API_KEY}" \
  http://127.0.0.1:8000/status
```

The status response must report `"dry_run": true` before the kill switch is
disabled.

Open `http://127.0.0.1:3000` on the Docker host and sign in with
`ADMIN_API_KEY`. For access from another machine, place port 3000 behind the
same VPN or TLS reverse proxy used for administrative access. Keep the
loopback-only Compose binding and do not publish the console or Admin API
directly to the internet.

### Optional public-IP access (dry-run evaluation only)

If a domain, VPN, or SSH tunnel is not available, the explicit public overlay
publishes only the operator console on all IPv4 interfaces:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.production.yml \
  -f docker-compose.public.yml \
  up -d --build
```

Equivalently, run `make up-public`. Allow TCP port 3000 in both the VPS host
firewall and the provider's security group, then open
`http://VPS_PUBLIC_IP:3000`. Verify that only the frontend is public:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.production.yml \
  -f docker-compose.public.yml \
  ps
sudo ss -lntp | grep ':3000'
```

The frontend should show `0.0.0.0:3000->80/tcp`. The Admin API must remain
bound to `127.0.0.1:8000`; PostgreSQL, Redis, Freqtrade, and the LLM service
must have no public host binding. This mode uses plain HTTP, so the
bearer API key is not protected from network interception. Use a long random
`ADMIN_API_KEY`, keep `DRY_RUN=true`, and move to encrypted access before any
non-evaluation use.

## Release procedure

1. Enable the kill switch.
2. Take and verify a PostgreSQL backup.
3. Pull the reviewed release.
4. Run lint and tests.
5. Build images and run `docker compose up -d` using both Compose files.
6. Verify every container is healthy and `migrate` exited with code zero
   (`scripts/healthcheck.sh` checks this, plus a 60s crash-loop soak and the
   loopback HTTP endpoints; `scripts/deploy.sh` runs it automatically).
7. Verify `/status`, logs, and one manually triggered cycle per pair.
8. Review the resulting trace IDs in the operator console, then disable the
   kill switch only after the signal, risk-decision, and order timelines are
   consistent.

## Monitoring

Basic monitoring uses Docker health checks, JSON logs, the Admin API, and
Telegram audit notifications:

```bash
docker compose -f docker-compose.yml -f docker-compose.production.yml ps
docker compose -f docker-compose.yml -f docker-compose.production.yml logs --since 15m
docker stats
```

Alert immediately when:

- any long-running container is unhealthy or unexpectedly restarts;
- `/status` reports `dry_run=false`;
- any configured symbol has no new signal for 75 minutes beyond its stagger;
- an `ORDER_FAILED`, `INTERNAL_ERROR`, or `RECONCILIATION_REQUIRED` event occurs;
- a `SUBMITTED` order remains unresolved for more than 10 minutes;
- Redis has pending Risk Engine messages for more than two minutes;
- disk usage exceeds 80%, or the newest off-host backup is older than 24 hours.

Useful database checks:

```bash
docker compose exec postgres psql -U trademind -d trademind -c \
  "SELECT symbol, max(created_at) AS last_cycle FROM signals GROUP BY symbol;"

docker compose exec postgres psql -U trademind -d trademind -c \
  "SELECT created_at, symbol, status, trace_id FROM orders
   WHERE status = 'FAILED'
      OR (status = 'SUBMITTED' AND created_at < now() - interval '10 minutes')
   ORDER BY created_at DESC;"

docker compose exec redis redis-cli XINFO GROUPS signals:pending
```

All trading-cycle investigation should start with `trace_id` and use
`GET /audit?trace_id=<uuid>` to reconstruct the complete timeline.

## Backups

PostgreSQL is the audit system of record. Create a daily custom-format dump
and transfer it off-host over an encrypted channel:

```bash
docker compose exec -T postgres \
  pg_dump -U trademind -d trademind -Fc > trademind.dump
pg_restore --list trademind.dump
```

Retain at least seven daily and four weekly backups. Perform a restore drill
before relying on the deployment. Redis is reconstructable coordination
state, but its persistent volume should still be included in host snapshots.

## Incident response

Enable the kill switch first. Do not delete Redis keys, edit order rows, or
retry a Freqtrade command manually until the Postgres audit timeline and the
Freqtrade trade record have been compared. A `RECONCILIATION_REQUIRED` event
means the system deliberately refused to guess the remote order state.

## Retired public Wiki

Public Wiki publication is disabled on GitHub and on the VPS at the operator's
request. Generated pages were removed from the current Wiki tree; Git history is
not erased. Do not re-enable publishing through legacy Wiki settings. Compose
supplies blank Wiki destination/token values, and the archive worker only publishes
the private report. Email delivery remains enabled on its existing weekly schedule.

## Private archive and archive-only worker

Set `PRIVATE_REPORT_REPOSITORY=dqflow/TradeMind-Reports` and
`PRIVATE_REPORT_TOKEN` on notifier to enable the internal report. The repository
must already exist, have a default branch, and be **private**. The publisher verifies
its identity and privacy before committing and again before pushing. It refuses
unknown/public destinations. Keep access limited to the intended collaborators.
A repo administrator can still expose old data by making it public later.

The private `REPORTS.md` index links weekly Markdown and PNG charts. Reports begin
with historical conclusions and investigation suggestions, then show a separately
timestamped current account snapshot, gross win/loss breakdown and closed-trade
journal. Failed Admin status reads are labeled unverified. No secrets/raw payloads
are exported. Reports are published only to the private repository.

```bash
docker compose exec -T notifier python -m app.manual_trigger weekly-private --preview
docker compose exec -T notifier python -m app.manual_trigger weekly-private
```

The private archive uses the existing weekday/hour settings and retries hourly.
For a separate deployment, build the notifier image from a reviewed source bundle
and run `python -m app.archive_worker` on the existing core network. Provide only
`POSTGRES_DSN`, `ADMIN_API_URL`, `ADMIN_API_KEY`, the private report destination/token
pair, and weekly schedule variables in a root-owned mode-0600 env file. A Docker
restart policy keeps the loop running after host/container restarts; startup checks
the latest completed week. Do not also enable the archive loop in the regular
notifier, to avoid redundant snapshots/commits. This worker does not send email or
Telegram or change trading state. Remove/stop it before migrating archive delivery
back into the normal notifier deployment.

The standalone composition is `docker-compose.reports.yml`. A source-only release
bundle (no `.env`, logs, reports or credentials) can be deployed under
`/opt/trademind-reports` with its separate `runtime.env` (directory 0700, file 0600):

```bash
cd /opt/trademind-reports
docker compose --env-file runtime.env -f docker-compose.reports.yml config --quiet
docker compose --env-file runtime.env -f docker-compose.reports.yml up -d --build
docker compose --env-file runtime.env -f docker-compose.reports.yml logs --tail 30
```

Confirm `private_report_published` and verify
GitHub visibility/content before relying on the schedule. The container's PID
health check is only liveness; publication failures must be monitored from the
structured log events. The next default run is Monday 08:00 UTC / 15:00 Vietnam.
The worker uploads the last completed scheduled week on startup, so its first run
can happen immediately rather than waiting until Monday. PostgreSQL remains the
source of truth; no automatic backfill of older missed weeks occurs.


### Weekly email matching the private report

Weekly email uses the same private report content plus the existing since-start
cumulative P&L section. Both the overview chart and the 12-period account-equity
chart are embedded as CID PNG attachments; readers do not need GitHub access to
view them. Text-only email clients receive the data tables and assessment.
Set `WEEKLY_EMAIL_ARCHIVE_REPOSITORY=dqflow/TradeMind-Reports` to add an archive
link without enabling another archive loop. Existing SMTP settings and weekly
schedule still apply. The daily Telegram summary is unchanged.

The equity chart selects the last recorded non-null account-equity snapshot in
each weekly period and displays its actual observation time. It never reconstructs
balance from P&L, never fills missing periods with zero, and is private-only.
Deposits/withdrawals may affect the account-value series. Refresh the archive worker
image as well as notifier when changing these shared renderers.


The current VPS source-bundle rollout uses `/opt/trademind-reports/trademind-email.override.yml`
for notifier and `trademind-archive-update.yml` for the archive worker, both pointing
to `trademind-reports:email`. Keep these overrides when recreating those containers
until this source change is merged and deployed through the normal release path;
rebuilding notifier from older main code would restore the old email template.
The notifier override enables only the archive link, leaving both archive destinations
empty there because the standalone worker already owns publication.
