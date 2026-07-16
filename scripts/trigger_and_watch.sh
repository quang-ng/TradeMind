#!/usr/bin/env bash
# Manually trigger a trading cycle and print its full audit trail —
# automates what we've been doing by hand: check /status, clear the
# current candle's idempotency lock if the cycle would otherwise skip,
# trigger, poll until the Risk Engine has decided, then dump /audit.
#
# This is a debugging/testing convenience (PROJECT.md Section 11's
# POST /cycles/{symbol}/trigger already runs the real pipeline "subject to
# all normal risk rules" — this script adds nothing unsafe, it just saves
# retyping the curl/redis-cli dance). Requires ADMIN_API_KEY in the
# environment: run `set -a; source .env; set +a` first.
#
# Usage:
#   scripts/trigger_and_watch.sh BTC-USDT
#   scripts/trigger_and_watch.sh ETH-USDT
#
# On a deployment using the production overlay, set COMPOSE_FILES first:
#   COMPOSE_FILES="-f docker-compose.yml -f docker-compose.production.yml" \
#     scripts/trigger_and_watch.sh BTC-USDT

set -euo pipefail

SYMBOL="${1:?Usage: $0 <SYMBOL, e.g. BTC-USDT or ETH-USDT>}"
API_URL="${ADMIN_API_LOCAL_URL:-http://127.0.0.1:8000}"
COMPOSE="docker compose ${COMPOSE_FILES:-}"

if [ -z "${ADMIN_API_KEY:-}" ]; then
  echo "ADMIN_API_KEY is not set. Run: set -a; source .env; set +a" >&2
  exit 1
fi

auth=(-H "Authorization: Bearer ${ADMIN_API_KEY}")
redis_symbol="${SYMBOL/-//}"

trigger() {
  curl -sS -X POST "${auth[@]}" "${API_URL}/cycles/${SYMBOL}/trigger"
}

dry_run=$(curl -sS "${auth[@]}" "${API_URL}/status" | python3 -c "import json,sys; print(json.load(sys.stdin)['dry_run'])")
echo "dry_run=${dry_run}"

echo "Triggering ${SYMBOL}..."
result="$(trigger)"
echo "$result"

skipped=$(echo "$result" | python3 -c "import json,sys; print(json.load(sys.stdin).get('skipped'))")

if [ "$skipped" = "True" ]; then
  echo "Candle already processed — clearing its idempotency lock and retrying..."
  keys=$($COMPOSE exec -T redis redis-cli KEYS "idempotency:candle:${redis_symbol}:*")
  while IFS= read -r key; do
    [ -n "$key" ] && $COMPOSE exec -T redis redis-cli DEL "$key" >/dev/null
  done <<< "$keys"
  result="$(trigger)"
  echo "$result"
fi

trace_id=$(echo "$result" | python3 -c "import json,sys; print(json.load(sys.stdin).get('trace_id') or '')")
if [ -z "$trace_id" ]; then
  echo "No trace_id returned — nothing to audit."
  exit 1
fi

echo "Waiting for the cycle to complete (LLM call can take up to ~35s)..."
audit=""
for _ in $(seq 1 15); do
  audit=$(curl -sS "${auth[@]}" "${API_URL}/audit?trace_id=${trace_id}")
  has_decision=$(echo "$audit" | python3 -c "import json,sys; print(bool(json.load(sys.stdin).get('risk_decisions')))")
  [ "$has_decision" = "True" ] && break
  sleep 3
done

echo "=== Audit trail for trace_id=${trace_id} ==="
echo "$audit" | python3 -m json.tool
