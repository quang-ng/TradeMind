import logging

from common.config import LLMServiceSettings
from common.logging import configure_json_logging
from fastapi import Depends, FastAPI

from .context.builder import ContextBuilder
from .llm.client import LLMClient
from .llm.providers import get_provider
from .llm.providers.base import Provider
from .models.wire import AnalyzeRequest, ProviderOverride, TradingSignal
from .prompts.builder import PromptBuilder
from .services.pipeline import AnalysisPipeline
from .signals.generator import SignalGenerator
from .strategies.selector import StrategySelector
from .validators.response_validator import ResponseValidator

configure_json_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="TradeMind LLM Analysis Service")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def get_settings() -> LLMServiceSettings:
    return LLMServiceSettings()


def get_provider_dependency(settings: LLMServiceSettings = Depends(get_settings)) -> Provider:
    return get_provider(settings)


def _apply_override(
    settings: LLMServiceSettings, override: ProviderOverride
) -> LLMServiceSettings:
    updates = override.model_dump(exclude_none=True)
    return settings.model_copy(update=updates) if updates else settings


def _build_pipeline(provider: Provider, settings: LLMServiceSettings) -> AnalysisPipeline:
    """Composition root: wires one `AnalysisPipeline` per request from the
    resolved `Provider` + settings. Rebuilt per call (rather than cached as
    a FastAPI dependency) because `provider` may be the DI-injected default
    or a request-scoped `provider_override` swap-in — see `analyze()`."""
    return AnalysisPipeline(
        context_builder=ContextBuilder(),
        strategy_selector=StrategySelector(),
        prompt_builder=PromptBuilder(
            include_strategy_context=settings.include_strategy_context_in_prompt
        ),
        llm_client=LLMClient(provider, timeout_seconds=settings.analyze_timeout_seconds),
        response_validator=ResponseValidator(
            min_exit_profit_pct=settings.min_exit_profit_pct,
            min_exit_loss_pct=settings.min_exit_loss_pct,
            max_repair_attempts=settings.max_repair_attempts,
        ),
        signal_generator=SignalGenerator(),
    )


@app.post("/analyze", response_model=TradingSignal)
async def analyze(
    request: AnalyzeRequest,
    provider: Provider = Depends(get_provider_dependency),
    settings: LLMServiceSettings = Depends(get_settings),
) -> TradingSignal:
    if request.provider_override is not None:
        provider = get_provider(_apply_override(settings, request.provider_override))
    pipeline = _build_pipeline(provider, settings)
    return await pipeline.run(request)
