#!/usr/bin/env bash
# One-time, destructive reset: stops the whole stack, deletes the
# postgres/redis/freqtrade data volumes, then brings everything back up
# from a clean slate. `migrate` re-runs `alembic upgrade head` on the fresh
# Postgres volume (recreating the schema and seeding `system_state`),
# Freqtrade recreates an empty tradesv3.dryrun.sqlite, and Redis starts
# empty — no manual TRUNCATE/psql/redis-cli steps needed.
#
# Deletes: all signals/risk_decisions/orders/positions/audit_events,
# Freqtrade's dry-run trade ledger, all Redis state (idempotency keys,
# cycle locks, cooldowns, kill switch, signals:pending stream).
#
# Preserves: `risk_config_state`/`llm_config_state`/`notifier_state`
# overrides and the Ollama model cache (ollama_data) — neither is touched.
#
# Intended as a one-time manual reset, not a routine operation. Run on the
# VPS, from the repo root:
#   scripts/reset_all_data.sh
#
# On a deployment using the production overlay (the default here), set
# COMPOSE_FILES to override:
#   COMPOSE_FILES="-f docker-compose.yml" scripts/reset_all_data.sh

set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_dir}"

compose_files=(
    -f docker-compose.yml
    -f docker-compose.production.yml
    -f docker-compose.public.yml
)
compose=(docker compose "${compose_files[@]}")

data_volumes=(trademind_postgres_data trademind_redis_data trademind_freqtrade_data)

echo "This will PERMANENTLY DELETE all TradeMind trading data:"
echo "  - Postgres: signals, risk_decisions, orders, positions, audit_events"
echo "  - Freqtrade's dry-run trade ledger"
echo "  - All Redis state (kill switch, locks, cooldowns, idempotency keys)"
echo
read -r -p "Type 'reset' to continue: " confirm
if [[ "${confirm}" != "reset" ]]; then
    echo "Aborted — no changes made."
    exit 1
fi

backup_file="backups/pre-reset-$(date -u +%Y%m%dT%H%M%SZ).dump"
mkdir -p backups
echo "Backing up Postgres to ${backup_file}..."
"${compose[@]}" exec -T postgres pg_dump -U trademind -Fc trademind > "${backup_file}"
echo "Backup saved ($(du -h "${backup_file}" | cut -f1))."

echo "Building images..."
"${compose[@]}" build

echo "Stopping the stack..."
"${compose[@]}" down

echo "Deleting data volumes: ${data_volumes[*]}"
for volume in "${data_volumes[@]}"; do
    if docker volume inspect "${volume}" >/dev/null 2>&1; then
        docker volume rm "${volume}"
    else
        echo "Volume ${volume} not found (already removed?) — skipping" >&2
    fi
done

# A freshly-created named volume is mounted root:root by default. Freqtrade's
# container always runs as the non-root `ftuser` (freqtrade/Dockerfile pins
# USER ftuser, no runtime privilege drop to fix ownership itself), so without
# this step the very first boot after a reset fails with sqlite3
# "unable to open database file" and crash-loops on the healthcheck.
echo "Pre-creating trademind_freqtrade_data with ftuser ownership..."
docker volume create trademind_freqtrade_data >/dev/null
docker run --rm -u root -v trademind_freqtrade_data:/freqtrade/db trademind-freqtrade \
    chown -R ftuser:ftuser /freqtrade/db

echo "Starting from a clean slate..."
"${compose[@]}" up -d --wait --wait-timeout 300

echo
echo "Reset complete. Current state:"
"${compose[@]}" ps
echo
echo "Verify with:"
echo "  docker exec trademind-postgres-1 psql -U trademind -d trademind -c 'SELECT count(*) FROM signals;'"
echo "  docker logs trademind-freqtrade-1 --since 2m"
