from decimal import Decimal
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """Shared audit database connection (PROJECT.md Section 6: POSTGRES_DSN,
    consumed by all services). Default points at the local docker-compose
    Postgres for zero-config local development."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    postgres_dsn: str = "postgresql+asyncpg://trademind:trademind@localhost:5432/trademind"


class LLMServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    llm_provider: str = "anthropic"
    llm_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    ollama_base_url: str = "http://ollama:11434"
    # Qwen2.5 7B is the smallest validated default for this indicator rubric.
    # The previous llama3.2 3B deployment repeatedly contradicted supplied
    # RSI/EMA/MACD values and copied prompt phrases into false BUY signals.
    ollama_model: str = "qwen2.5:7b"
    ollama_temperature: float = 0.4
    # The exit rubric (semantic_validator.py) only locks in gains, never cuts
    # losses — but "profitable" must mean more than literally > 0. Analyze
    # latency plus forceexit execution slippage can turn a marginally
    # positive unrealized_pnl_pct at decision time into a net loss by fill
    # time once round-trip fees are counted. Require a cushion above that.
    min_exit_profit_pct: float = 0.005
    # The bounded timeout remains a fail-closed backstop for unusually slow
    # local inference; the Scheduler staggers normal requests within each
    # thirty-minute candle period, which leaves plenty of room above 180s
    # for a CPU-bound model (e.g. llama3.2:3b) without risking overlap into
    # the next candle's cycle.
    analyze_timeout_seconds: float = 300.0


class RedisSettings(BaseSettings):
    """REDIS_URL (PROJECT.md Section 6), consumed by all services that touch
    coordination/cache state (scheduler, risk_engine, admin_api)."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    redis_url: str = "redis://localhost:6379/0"


class RiskConfig(BaseSettings):
    """PROJECT.md Section 9.1 rule-set defaults. Loaded from the environment
    for Phase 2; Phase 4's `PATCH /config` becomes the runtime-editable path
    (persisted + audited) without changing this shape."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    risk_per_trade_pct: Decimal = Decimal("0.01")
    max_position_pct: Decimal = Decimal("0.05")
    max_total_exposure_pct: Decimal = Decimal("0.20")
    max_open_positions: int = 2
    max_daily_loss_pct: Decimal = Decimal("0.03")
    consecutive_loss_limit: int = 3
    cooldown_minutes: int = 120
    # Raised from 0.65: the exit rubric's confidence is deterministic
    # (0.65 + 0.05 per confirmation past 3, capped at 0.80 — see
    # semantic_validator.py) and untouched by this rule (exit_evaluator.py
    # runs no confidence check), so this only tightens entries. 0.70 filters
    # the weakest 3-confirmation BUYs while still admitting anything with
    # either a 4th confirmation or genuine model conviction.
    min_confidence: Decimal = Decimal("0.70")
    signal_max_age_minutes: int = 10
    atr_stop_multiplier: Decimal = Decimal("2.0")
    min_stop_loss_pct: Decimal = Decimal("0.015")
    max_stop_loss_pct: Decimal = Decimal("0.08")
    dry_run: bool = True


class SchedulerSettings(BaseSettings):
    """PROJECT.md Section 6 repo structure: scheduler/app/jobs.py inputs.
    `llm_service_url` points at the isolated-zone LLM Analysis Service's
    `/analyze` endpoint (Section 8)."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    llm_service_url: str = "http://localhost:8001/analyze"
    # Must stay a few seconds above LLMServiceSettings.analyze_timeout_seconds
    # (300s) so the service's own timeout fires first and returns a HOLD
    # signal, rather than this HTTP client cutting the connection early.
    llm_request_timeout_seconds: float = 305.0
    candle_lookback: int = 200
    # Distinct from candle_lookback: indicator math (e.g. ema_200) needs the
    # full lookback, but the LLM already receives those computed indicators
    # separately (Section 8.1) — it doesn't need 200 raw candles, and on
    # CPU-bound local providers a too-large ohlcv array can consume the entire
    # /analyze budget (Section 8.3) before generation
    # even starts. Only the most recent `llm_ohlcv_window` candles are sent.
    llm_ohlcv_window: int = 4
    # Comma-separated in the environment (SYMBOLS=BTC/USDT,ETH/USDT,...) via
    # the validator below, so enabling/disabling a symbol is a .env edit, not
    # a code change. build_scheduler (main.py) registers one cron job per
    # entry and requires each to fit within the candle period once staggered
    # (SchedulerSettings.symbol_stagger_seconds) - remove a symbol from this
    # list rather than shrinking the stagger if that guard starts rejecting.
    symbols: Annotated[list[str], NoDecode] = [
        "BTC/USDT",
        "ETH/USDT",
        "BNB/USDT",
        "XRP/USDT",
    ]

    @field_validator("symbols", mode="before")
    @classmethod
    def _split_symbols_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [s.strip() for s in value.split(",") if s.strip()]
        return value
    # Four requests are spread across each five-minute candle period. The
    # stagger guard below rejects any symbol set whose final offset would
    # spill into the next candle.
    timeframe: str = "5m"
    candle_settle_second: int = 15
    # Per-symbol offset (see scheduler/app/main.py's stagger logic) so BTC
    # and ETH cycles don't call the LLM service at the same instant — on a
    # single local Ollama model, concurrent calls queue and can blow the
    # analyze timeout (the second call effectively pays 2x generation time).
    symbol_stagger_seconds: int = 70
    scheduler_health_port: int = 8000


class AccountSettings(BaseSettings):
    """Placeholder equity source until Phase 3 wires a live Freqtrade
    balance query (PROJECT.md Section 4: Freqtrade owns balance/equity).
    `account_state.py` isolates every caller from this being a stand-in."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    starting_equity_usdt: Decimal = Decimal("10000")


class FreqtradeSettings(BaseSettings):
    """FREQTRADE_API_URL / _USER / _PASS (PROJECT.md Section 6) — internal-
    network-only Freqtrade REST credentials, consumed by risk_engine only."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    freqtrade_api_url: str = "http://localhost:8080"
    freqtrade_api_user: str = ""
    freqtrade_api_pass: str = ""
    freqtrade_request_timeout_seconds: float = 15.0
    reconciliation_interval_seconds: float = 60.0
    reconciliation_order_age_minutes: int = 10


class WebhookSettings(BaseSettings):
    """WEBHOOK_SHARED_SECRET (PROJECT.md Section 6) — authenticates the
    Freqtrade -> admin_api webhook (Section 11), not the operator API key."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    webhook_shared_secret: str = ""


class AdminApiSettings(BaseSettings):
    """ADMIN_API_KEY (PROJECT.md Section 6) — static bearer token auth for
    every admin_api route except `/health` and `/webhooks/freqtrade`
    (Section 11)."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    admin_api_key: str = ""


class NotifierSettings(BaseSettings):
    """TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (PROJECT.md Section 6), plus
    the admin_api endpoint the Telegram bot calls back into for kill-switch
    slash commands — "Telegram is a client of the API, not a parallel
    control path" (Section 11)."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    admin_api_url: str = "http://admin_api:8000"
    admin_api_key: str = ""
    audit_poll_interval_seconds: float = 3.0
    telegram_poll_interval_seconds: float = 2.0
