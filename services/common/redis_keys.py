"""Single source of truth for Redis key naming (PROJECT.md Section 10.2).
No service may construct a Redis key by hand — import the builder here so
the key schema can only drift in one place."""

CYCLE_LOCK_TTL_SECONDS = 5 * 60
DECISION_IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60
ACCOUNT_BALANCE_SNAPSHOT_TTL_SECONDS = 90
# Refreshed on every failed `/analyze` cycle and cleared by the first good
# one, so this only outlives a scheduler that has stopped running entirely
# mid-streak — long enough to survive a redeploy, short enough to not carry
# a stale partial count into a new day.
LLM_TIMEOUT_STREAK_TTL_SECONDS = 24 * 60 * 60
SIGNALS_PENDING_STREAM = "signals:pending"
SIGNALS_PENDING_CONSUMER_GROUP = "risk_engine"
KILLSWITCH_GLOBAL_KEY = "killswitch:global"
ACCOUNT_BALANCE_SNAPSHOT_KEY = "account:balance:latest"


def cycle_lock(symbol: str) -> str:
    return f"lock:cycle:{symbol}"


def candle_idempotency(symbol: str, timeframe: str, candle_ts: str) -> str:
    return f"idempotency:candle:{symbol}:{timeframe}:{candle_ts}"


def decision_idempotency(signal_id: str) -> str:
    return f"idempotency:decision:{signal_id}"


def signals_latest(symbol: str) -> str:
    return f"signals:latest:{symbol}"


def cooldown(symbol: str) -> str:
    return f"cooldown:{symbol}"


def llm_ratelimit(provider: str) -> str:
    return f"ratelimit:llm:{provider}"


def llm_timeout_streak() -> str:
    """Global (not per-symbol) count of consecutive `/analyze` cycles that
    returned no usable signal — a stalled local model fails every symbol,
    so one counter across all of them detects an outage in a candle period
    or two instead of `threshold` periods per symbol."""
    return "llm:analyze:timeout_streak"
