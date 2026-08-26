from typing import Any

import httpx

from .base import Provider


class OllamaProvider(Provider):
    """Talks to a self-hosted Ollama server's `/api/chat` endpoint.

    Deliberately does NOT set `format` (Ollama's grammar-constrained
    structured output): measured on CPU inference, schema-constrained
    decoding dropped generation to ~0.3 tokens/sec (vs ~48 tokens/sec for
    unconstrained prefill on the same request) — enough to blow the bounded
    `/analyze` budget (Section 8.3) even after the prompt itself was cut to
    a handful of candles. Free-form generation relies on the system
    prompt's "respond with ONLY the JSON object" instruction instead;
    non-conforming output already falls back safely to `HOLD` via
    `validators.py`'s existing malformed/schema-invalid handling, so this
    trades a small amount of format reliability for the model actually
    finishing within budget. AnthropicProvider keeps `output_config`
    structured output — no evidence of the same slowdown on hosted infra.

    `http_client` is injectable for tests (mirrors
    `risk_engine/app/freqtrade_client.py`'s pattern); `timeout=None` on the
    default client leaves the request deadline to `main.py`'s
    `asyncio.timeout(analyze_timeout_seconds)` wrapper instead of httpx's
    default 5s timeout, which would otherwise cut off legitimate local
    generation time.
    """

    def __init__(
        self, base_url: str, model: str, http_client: Any = None, temperature: float = 0.4
    ):
        self._model = model
        self._temperature = temperature
        self._http_client = http_client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=None
        )

    @property
    def model(self) -> str:
        return self._model

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        response = await self._http_client.post(
            "/api/chat",
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": False,
                "options": {
                    "temperature": self._temperature,
                    # Valid responses are normally ~100-160 tokens, but the
                    # full schema (action/confidence/reasoning/
                    # key_indicators/invalidation_condition) needs headroom
                    # above that or generation gets cut off mid-field,
                    # leaving unparseable truncated JSON. 160 (2026-08-25)
                    # was tuned to fit the *old* 300s analyze_timeout_seconds
                    # but was too tight for the schema in practice: it
                    # produced 0 malformed_json failures at 220 across
                    # 08-19->08-24, then 21 malformed_json failures the same
                    # day it dropped to 160 (2026-08-25) — trading the
                    # timeout problem for a truncation problem instead of
                    # fixing it. Restored to 220 now that
                    # `analyze_timeout_seconds` is 450s: on this CPU-only
                    # host, generation ran as slow as ~0.6 tok/s in the
                    # observed tail, so 220 tokens' worst case (~370s) still
                    # leaves ~80s of headroom for prompt eval before the
                    # budget is hit.
                    "num_predict": 220,
                },
                # Multiple symbols run within each five-minute candle period
                # (PROJECT.md Section 5). Keep the model resident so normal
                # staggered calls do not repeatedly pay its load cost.
                "keep_alive": "90m",
            },
        )
        response.raise_for_status()
        content = response.json()["message"]["content"]
        if not content:
            raise RuntimeError("empty content in Ollama response")
        return content
