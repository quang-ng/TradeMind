import asyncio
import logging

from common.config import LLMServiceSettings
from common.logging import configure_json_logging
from fastapi import Depends, FastAPI

from .prompts.v1 import SYSTEM_PROMPT_V1, build_user_prompt
from .providers import get_provider
from .providers.base import Provider
from .schemas import AnalyzeRequest, Signal
from .validators import ValidationFailure, build_hold_signal, build_signal, parse_llm_response

configure_json_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="TradeMind LLM Analysis Service")


def get_settings() -> LLMServiceSettings:
    return LLMServiceSettings()


def get_provider_dependency(settings: LLMServiceSettings = Depends(get_settings)) -> Provider:
    return get_provider(settings)


@app.post("/analyze", response_model=Signal)
async def analyze(
    request: AnalyzeRequest,
    provider: Provider = Depends(get_provider_dependency),
    settings: LLMServiceSettings = Depends(get_settings),
) -> Signal:
    model_name = f"{settings.llm_provider}:{settings.anthropic_model}"
    raw_text, failure_reason = await _call_provider_with_retry(
        provider, request, settings.analyze_timeout_seconds
    )

    if failure_reason is not None:
        logger.warning(
            "llm_call_failed", extra={"reason": failure_reason, "symbol": request.symbol}
        )
        return build_hold_signal(request, reason=failure_reason, model_name=model_name)

    try:
        output = parse_llm_response(raw_text)
    except ValidationFailure as exc:
        logger.warning(
            "llm_response_invalid", extra={"reason": exc.reason, "symbol": request.symbol}
        )
        return build_hold_signal(
            request, reason=exc.reason, model_name=model_name, raw_response={"raw": raw_text}
        )

    return build_signal(request, output, model_name=model_name, raw_response={"raw": raw_text})


async def _call_provider_with_retry(
    provider: Provider, request: AnalyzeRequest, timeout_seconds: float
) -> tuple[str | None, str | None]:
    """Section 8.3 failure-mode table: one retry with backoff on a transport
    failure, whole call (including the retry) bounded by `timeout_seconds`;
    a successful-but-malformed response is not retried here — that is
    validators.py's job, with no retry (Section 8.3: "no retry — treat as a
    prompt/model problem, not a transient one")."""
    system_prompt = SYSTEM_PROMPT_V1
    user_prompt = build_user_prompt(request)

    try:
        async with asyncio.timeout(timeout_seconds):
            try:
                return await provider.generate(system_prompt, user_prompt), None
            except Exception:
                await asyncio.sleep(1.0)
                try:
                    return await provider.generate(system_prompt, user_prompt), None
                except Exception:
                    return None, "provider_error"
    except TimeoutError:
        return None, "llm_timeout"
