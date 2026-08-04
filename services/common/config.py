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
    # The exit rubric (validators/semantic.py) locks in gains once
    # unrealized_pnl_pct clears this margin — a bare > 0 isn't enough, since
    # analyze latency plus forceexit execution slippage can turn a marginally
    # positive reading at decision time into a net loss by fill time once
    # round-trip fees are counted. Require a cushion above that.
    min_exit_profit_pct: float = 0.005
    # Symmetric loss-cutting threshold: the exit rubric cuts a losing
    # position once unrealized_pnl_pct falls below -min_exit_loss_pct and the
    # same cross-category bearish confirmation bar is met, so a confirmed
    # trend reversal is cut before riding down to the wider ATR/static
    # stop-loss (PROJECT.md Section 9.2). Kept small but non-zero so it
    # doesn't fire on ordinary noise.
    min_exit_loss_pct: float = 0.005
    # Emergency backstop (added after the 2026-08-04 P&L review found losers
    # riding down to -3.6%+ over multiple days because a grinding/choppy
    # decline never produced two cross-category bearish confirmations): once
    # unrealized_pnl_pct breaches -hard_loss_cut_pct, the exit rubric forces
    # SELL unconditionally — no confirmation count required, and unlike
    # min_exit_loss_pct this can override a model-proposed HOLD, not just
    # confirm a model-proposed SELL. Set above min_exit_loss_pct so the
    # confirmation-gated cut still gets first chance on a clean reversal.
    hard_loss_cut_pct: float = 0.015
    # The bounded timeout remains a fail-closed backstop for unusually slow
    # local inference; the Scheduler staggers normal requests within each
    # thirty-minute candle period, which leaves plenty of room above 180s
    # for a CPU-bound model (e.g. llama3.2:3b) without risking overlap into
    # the next candle's cycle.
    analyze_timeout_seconds: float = 300.0
    # ResponseValidator's repair-prompt retry (llm_service/app/validators/
    # response_validator.py). 0 reproduces PROJECT.md Section 8.3's
    # documented "no retry" behavior for a malformed/schema-invalid response
    # exactly. Raise this only after updating Section 8.3's failure-mode
    # table to describe the repair-prompt retry it would activate.
    max_repair_attempts: int = 0
    # PromptBuilder's optional strategy-regime framing line (llm_service/app/
    # prompts/builder.py) — advisory only, does not override the rubric.
    # Enabled 2026-08-04: a 566-trade mechanical backtest
    # (scripts/backtest/mechanical_replay.py) found no regime whose win rate
    # clearly beat the others, so this isn't expected to be a big lever, but
    # giving the model the same regime label the Strategy Selector already
    # computes is a free, low-risk source of extra context.
    include_strategy_context_in_prompt: bool = True


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
    # Position-sizing multiplier applied at exactly `min_confidence`,
    # scaling linearly up to 1.0 (full size) at confidence 1.0 — added
    # 2026-08-04 (mục 3) so a signal that barely clears the confidence bar
    # risks less capital than one the model is fully convinced by.
    min_confidence_size_scale: Decimal = Decimal("0.5")
    # A 16-symbol CPU-bound 1h cycle is staggered across the hour. Measure
    # from the closed candle's actual close time and leave enough room for
    # the final symbol's bounded inference/queue delay.
    signal_max_age_minutes: int = 65
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


class FreqtradeSettings(BaseSettings):
    """FREQTRADE_API_URL / _USER / _PASS (PROJECT.md Section 6) — internal-
    network-only Freqtrade REST credentials, consumed by risk_engine only."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    freqtrade_api_url: str = "http://localhost:8080"
    freqtrade_api_user: str = ""
    freqtrade_api_pass: str = ""
    freqtrade_request_timeout_seconds: float = 15.0
    balance_refresh_interval_seconds: float = 30.0
    reconciliation_interval_seconds: float = 60.0
    reconciliation_order_age_minutes: int = 10
    # Risk-reducing exits retry promptly inside the bounded request path.
    # Entries are never retried because duplicate entry submission would
    # increase exposure.
    freqtrade_exit_retry_attempts: int = 3
    freqtrade_exit_retry_delay_seconds: float = 1.0


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
    daily_pnl_report_hour_utc: int = 0
