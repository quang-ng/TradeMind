"""Single source of truth for Redis key naming (PROJECT.md Section 10.2).
No service may construct a Redis key by hand — import the builder here so
the key schema can only drift in one place."""

CYCLE_LOCK_TTL_SECONDS = 5 * 60
DECISION_IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60
SIGNALS_PENDING_STREAM = "signals:pending"
SIGNALS_PENDING_CONSUMER_GROUP = "risk_engine"
KILLSWITCH_GLOBAL_KEY = "killswitch:global"


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
