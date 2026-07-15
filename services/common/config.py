from decimal import Decimal

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    analyze_timeout_seconds: float = 30.0


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
    min_confidence: Decimal = Decimal("0.65")
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
    llm_request_timeout_seconds: float = 35.0
    timeframe: str = "1h"
    candle_lookback: int = 200
    symbols: list[str] = ["BTC/USDT", "ETH/USDT"]


class AccountSettings(BaseSettings):
    """Placeholder equity source until Phase 3 wires a live Freqtrade
    balance query (PROJECT.md Section 4: Freqtrade owns balance/equity).
    `account_state.py` isolates every caller from this being a stand-in."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    starting_equity_usdt: Decimal = Decimal("10000")
