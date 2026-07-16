from typing import Any

import httpx

from .base import Provider
from .output_schema import OUTPUT_SCHEMA


class OllamaProvider(Provider):
    """Talks to a self-hosted Ollama server's `/api/chat` endpoint,
    requesting schema-constrained structured output (Ollama >=0.5's
    `format` field) so the response matches PROJECT.md Section 8.2 the same
    way AnthropicProvider's `output_config` does — not relying on prompt
    compliance alone.

    `http_client` is injectable for tests (mirrors
    `risk_engine/app/freqtrade_client.py`'s pattern); `timeout=None` on the
    default client leaves the request deadline to `main.py`'s
    `asyncio.timeout(analyze_timeout_seconds)` wrapper instead of httpx's
    default 5s timeout, which would otherwise cut off legitimate local
    generation time.
    """

    def __init__(self, base_url: str, model: str, http_client: Any = None):
        self._model = model
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
                "format": OUTPUT_SCHEMA,
                "stream": False,
                "options": {"temperature": 0},
                # Cycles run hourly (PROJECT.md Section 5); Ollama's default
                # keep_alive (5m) would unload the model between every
                # cycle, paying the full model-load cost on each call. Keep
                # it resident so only the first call after startup pays it.
                "keep_alive": "90m",
            },
        )
        response.raise_for_status()
        content = response.json()["message"]["content"]
        if not content:
            raise RuntimeError("empty content in Ollama response")
        return content
