import anthropic

from .base import Provider

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["BUY", "SELL", "HOLD"]},
        "confidence": {"type": "number"},
        "reasoning": {"type": "string"},
        "key_indicators": {"type": "array", "items": {"type": "string"}},
        "invalidation_condition": {"type": "string"},
    },
    "required": ["action", "confidence", "reasoning", "key_indicators", "invalidation_condition"],
    "additionalProperties": False,
}


class AnthropicProvider(Provider):
    def __init__(self, api_key: str, model: str):
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=system_prompt,
            output_config={"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
            messages=[{"role": "user", "content": user_prompt}],
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("provider refused the request")
        for block in response.content:
            if block.type == "text":
                return block.text
        raise RuntimeError("no text block in provider response")
