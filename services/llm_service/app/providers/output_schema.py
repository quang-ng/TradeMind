OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["BUY", "SELL", "HOLD"]},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "reasoning": {"type": "string", "minLength": 1, "maxLength": 500},
        "key_indicators": {"type": "array", "items": {"type": "string"}},
        "invalidation_condition": {"type": "string", "minLength": 1},
    },
    "required": ["action", "confidence", "reasoning", "key_indicators", "invalidation_condition"],
    "additionalProperties": False,
}
