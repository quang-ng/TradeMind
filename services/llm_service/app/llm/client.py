import asyncio

from ..models.llm import LLMRequest, LLMResponse
from .providers.base import Provider


class LLMClient:
    """Only invokes the configured `Provider` (PROJECT.md Section 8.4) —
    retry, timeout, and the resulting `model_name` label live here; no
    business logic does. Streaming is never requested (`Provider.generate`
    always returns a complete string); temperature is configured on the
    `Provider` itself at construction time (see `llm/providers/__init__.py`
    and `LLMServiceSettings.ollama_temperature`), since neither current
    provider implementation takes it per-call.
    """

    def __init__(self, provider: Provider, *, timeout_seconds: float):
        self._provider = provider
        self._timeout_seconds = timeout_seconds

    @property
    def model_name(self) -> str:
        """`provider:model-version` label for `TradingSignal.model_name`
        (PROJECT.md Section 7.1)."""
        return f"{self._provider.provider_name}:{self._provider.model}"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        """One retry with a 1s backoff on a transport failure; the whole
        call, including that retry, is bounded by `timeout_seconds`
        (PROJECT.md Section 8.3's failure-mode table). A successful-but-
        malformed response is not retried here — that is the Response
        Validator's job, with no retry by default (Section 8.3: "no retry —
        treat as a prompt/model problem, not a transient one")."""
        try:
            async with asyncio.timeout(self._timeout_seconds):
                try:
                    text = await self._provider.generate(
                        request.system_prompt, request.user_prompt
                    )
                    return LLMResponse(raw_text=text, failure_reason=None)
                except Exception:
                    await asyncio.sleep(1.0)
                    try:
                        text = await self._provider.generate(
                            request.system_prompt, request.user_prompt
                        )
                        return LLMResponse(raw_text=text, failure_reason=None)
                    except Exception:
                        return LLMResponse(raw_text=None, failure_reason="provider_error")
        except TimeoutError:
            return LLMResponse(raw_text=None, failure_reason="llm_timeout")
