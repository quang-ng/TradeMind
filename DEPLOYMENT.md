# TradeMind deployment and monitoring

TradeMind is an MVP dry-run system. `DRY_RUN=true` is mandatory. Deploy it on
one Docker host and keep PostgreSQL, Redis, Freqtrade, and the LLM service off
the public network.

## Prerequisites

- Docker Engine with the Compose plugin
- 4 CPU, 8 GB RAM, and persistent SSD storage recommended for the core stack
  (Postgres, Redis, Freqtrade, admin services, operator console)
- if `LLM_PROVIDER=ollama` (self-hosted local model instead of a hosted API),
  add on top of the core stack: 4+ CPU cores and enough RAM to hold the model
  in memory — roughly 4-6 GB for a 3B-parameter model, 8+ GB for a 7-8B
  model, at Q4 quantization — plus a few GB of persistent disk per pulled
  model. An NVIDIA GPU with `nvidia-container-toolkit` is optional but
  strongly recommended: CPU-only inference risks exceeding the 30s `/analyze`
  timeout (PROJECT.md Section 8.3) once you go past a small (~3B) model.
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
fails. The scheduler runs BTC/USDT and ETH/USDT at `HH:00:15 UTC` by default.

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
bound to `127.0.0.1:8000`; PostgreSQL, Redis, Freqtrade, the LLM service, and
Ollama must have no public host binding. This mode uses plain HTTP, so the
bearer API key is not protected from network interception. Use a long random
`ADMIN_API_KEY`, keep `DRY_RUN=true`, and move to encrypted access before any
non-evaluation use.

## Release procedure

1. Enable the kill switch.
2. Take and verify a PostgreSQL backup.
3. Pull the reviewed release.
4. Run lint and tests.
5. Build images and run `docker compose up -d` using both Compose files.
6. Verify every container is healthy and `migrate` exited with code zero.
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
- either pair has no new signal for 75 minutes;
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
