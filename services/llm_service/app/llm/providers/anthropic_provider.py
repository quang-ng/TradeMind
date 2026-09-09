import anthropic

from .base import Provider
from .output_schema import OUTPUT_SCHEMA


class AnthropicProvider(Provider):
    def __init__(self, api_key: str, model: str):
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=2048,
            system=system_prompt,
            # The decision rubric is fully spelled out in the system prompt;
            # adaptive thinking adds nothing here but is unbounded — on an
            # ambiguous ranging setup it once spent the whole token budget
            # thinking and returned truncated JSON (`malformed_json` -> HOLD).
            # Disabled, the structured response is a steady ~330-400 tokens.
            thinking={"type": "disabled"},
            output_config={"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
            messages=[{"role": "user", "content": user_prompt}],
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("provider refused the request")
        for block in response.content:
            if block.type == "text":
                return block.text
        raise RuntimeError("no text block in provider response")
