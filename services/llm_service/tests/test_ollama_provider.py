import json

import httpx
from llm_service.app.providers.ollama_provider import OllamaProvider


def _provider_with_handler(handler) -> OllamaProvider:
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
    return OllamaProvider(base_url="http://test", model="llama3.2:3b", http_client=http_client)


async def test_generate_posts_chat_request_without_grammar_constrained_format():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.read())
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": '{"action": "HOLD"}'}},
        )

    provider = _provider_with_handler(handler)
    result = await provider.generate("system prompt", "user prompt")

    assert result == '{"action": "HOLD"}'
    assert captured["url"].endswith("/api/chat")
    assert captured["body"]["model"] == "llama3.2:3b"
    assert captured["body"]["messages"] == [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "user prompt"},
    ]
    # No `format` field — schema-constrained decoding measured at ~0.3
    # tokens/sec on CPU, see ollama_provider.py's docstring.
    assert "format" not in captured["body"]
    assert captured["body"]["keep_alive"] == "90m"
    assert captured["body"]["stream"] is False


async def test_model_property_returns_configured_model():
    provider = OllamaProvider(base_url="http://test", model="qwen2.5:7b")
    assert provider.model == "qwen2.5:7b"


async def test_generate_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "model not found"})

    provider = _provider_with_handler(handler)
    try:
        await provider.generate("system prompt", "user prompt")
    except httpx.HTTPStatusError:
        pass
    else:
        raise AssertionError("expected HTTPStatusError")


async def test_generate_raises_on_empty_content():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"role": "assistant", "content": ""}})

    provider = _provider_with_handler(handler)
    try:
        await provider.generate("system prompt", "user prompt")
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected RuntimeError")
