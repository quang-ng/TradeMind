# Used only by AnthropicProvider (`output_config.format`); OllamaProvider
# deliberately runs free-form — see its docstring. Anthropic's structured
# outputs accepts only a subset of JSON Schema: numeric `minimum`/`maximum`
# and string `minLength`/`maxLength` are rejected with a 400
# ("For 'number' type, properties maximum, minimum are not supported"), so
# this schema carries types + enum + required + additionalProperties only.
# The real bounds (confidence 0-1, non-empty reasoning/invalidation, 500-char
# cap) are still enforced downstream by validators/ + the wire model, and a
# response that violates them falls back safely to HOLD.
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
