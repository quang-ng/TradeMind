import asyncio
import time

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
        treat as a prompt/model problem, not a transient one").

        The retry only fires when at least half the budget is still
        unspent. On a CPU-bound local model a failed attempt has usually
        already burned most of `timeout_seconds` (2026-08-25 review: 10
        days of production data showed 0 `provider_error` results, i.e.
        every observed failure was already a slow-inference timeout, never
        a fast transport error) — retrying blind in that situation just
        replays the same multi-minute call with no time left for it to
        finish, guaranteeing the outer timeout fires and wasting the
        compute both attempts spent. Skipping the retry when the budget is
        mostly gone turns that into a clean, immediate `provider_error`
        instead, and leaves the next scheduled symbol's call from queueing
        behind a doomed second attempt on a single-slot Ollama server."""
        start = time.monotonic()
        try:
            async with asyncio.timeout(self._timeout_seconds):
                try:
                    text = await self._provider.generate(
                        request.system_prompt, request.user_prompt
                    )
                    return LLMResponse(raw_text=text, failure_reason=None)
                except Exception:
                    remaining = self._timeout_seconds - (time.monotonic() - start)
                    if remaining < self._timeout_seconds / 2:
                        return LLMResponse(raw_text=None, failure_reason="provider_error")
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
