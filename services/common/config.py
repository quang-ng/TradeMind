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
