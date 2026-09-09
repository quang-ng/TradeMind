# Used only by AnthropicProvider (`output_config.format`); OllamaProvider
# deliberately runs free-form — see its docstring.
#
# Anthropic's structured outputs accept most of JSON Schema but NOT numeric
# `minimum`/`maximum` ("For 'number' type, properties maximum, minimum are
# not supported" — a 400). String `minLength`/`maxLength` ARE supported and
# are kept here: `maxLength` on `reasoning` makes the model stop at 500
# characters during generation instead of overrunning the wire model's cap
# and getting the whole response rejected to HOLD. The confidence range is
# enforced downstream (`validators/llm_contract.py`); an out-of-range value
# still falls back safely to HOLD.
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["BUY", "SELL", "HOLD"]},
        "confidence": {"type": "number"},
        "reasoning": {"type": "string", "minLength": 1, "maxLength": 500},
        "key_indicators": {"type": "array", "items": {"type": "string"}},
        "invalidation_condition": {"type": "string", "minLength": 1},
    },
    "required": ["action", "confidence", "reasoning", "key_indicators", "invalidation_condition"],
    "additionalProperties": False,
}
