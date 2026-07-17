import json
from pathlib import Path

import pytest
from common.config import LLMServiceSettings
from fastapi.testclient import TestClient
from llm_service.app import main as main_module
from llm_service.app.main import app, get_provider_dependency, get_settings
from llm_service.app.providers.base import Provider

FIXTURES = Path(__file__).parent / "fixtures"


class StubProvider(Provider):
    def __init__(
        self,
        response_text: str | None = None,
        raise_exc: Exception | None = None,
        captured_prompts: list[tuple[str, str]] | None = None,
    ):
        self._response_text = response_text
        self._raise_exc = raise_exc
        self._captured_prompts = captured_prompts

    @property
    def model(self) -> str:
        return "stub-model"

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        if self._captured_prompts is not None:
            self._captured_prompts.append((system_prompt, user_prompt))
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._response_text


class SlowProvider(Provider):
    @property
    def model(self) -> str:
        return "slow-model"

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        import asyncio

        await asyncio.sleep(1.0)
        return "irrelevant"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(autouse=True)
def _clear_dependency_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "fixture_name", ["analyze_request_btcusdt.json", "analyze_request_ethusdt.json"]
)
def test_analyze_returns_schema_valid_signal_for_both_pairs(fixture_name):
    valid_response = json.dumps(
        {
            "action": "BUY",
            "confidence": 0.78,
            "reasoning": "RSI recovering from oversold, price reclaimed EMA50 with rising volume.",
            "key_indicators": ["rsi_recovery", "ema50_reclaim", "volume_increase"],
            "invalidation_condition": "Close back below EMA50 on the next candle.",
        }
    )
    app.dependency_overrides[get_provider_dependency] = lambda: StubProvider(
        response_text=valid_response
    )

    request_payload = _load_fixture(fixture_name)
    with TestClient(app) as client:
        response = client.post("/analyze", json=request_payload)

    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == request_payload["symbol"]
    assert body["action"] in {"BUY", "SELL", "HOLD"}
    assert 0.0 <= body["confidence"] <= 1.0
    assert body["reasoning"]
    assert len(body["reasoning"]) <= 500
    assert body["status"] == "PENDING"


@pytest.mark.parametrize(
    "malformed_response,expected_reason",
    [
        ("this is not json", "malformed_json"),
        ("[1, 2, 3]", "malformed_json"),
        (json.dumps({"confidence": 0.5}), "schema_invalid"),
        (
            json.dumps(
                {
                    "action": "MAYBE",
                    "confidence": 0.5,
                    "reasoning": "ambiguous",
                    "key_indicators": [],
                    "invalidation_condition": "n/a",
                }
            ),
            "invalid_action",
        ),
        (
            json.dumps(
                {
                    "action": "BUY",
                    "confidence": 1.5,
                    "reasoning": "overconfident",
                    "key_indicators": [],
                    "invalidation_condition": "n/a",
                }
            ),
            "invalid_confidence",
        ),
    ],
)
def test_analyze_falls_back_to_hold_on_malformed_llm_output(malformed_response, expected_reason):
    app.dependency_overrides[get_provider_dependency] = lambda: StubProvider(
        response_text=malformed_response
    )

    request_payload = _load_fixture("analyze_request_btcusdt.json")
    with TestClient(app) as client:
        response = client.post("/analyze", json=request_payload)

    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "HOLD"
    assert body["reasoning"] == expected_reason


def test_analyze_falls_back_to_hold_when_provider_errors_on_every_attempt():
    app.dependency_overrides[get_provider_dependency] = lambda: StubProvider(
        raise_exc=ConnectionError("provider unreachable")
    )

    request_payload = _load_fixture("analyze_request_btcusdt.json")
    with TestClient(app) as client:
        response = client.post("/analyze", json=request_payload)

    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "HOLD"
    assert body["reasoning"] == "provider_error"


def test_analyze_falls_back_to_hold_on_timeout():
    app.dependency_overrides[get_provider_dependency] = lambda: SlowProvider()
    app.dependency_overrides[get_settings] = lambda: LLMServiceSettings(
        analyze_timeout_seconds=0.05
    )

    request_payload = _load_fixture("analyze_request_btcusdt.json")
    with TestClient(app) as client:
        response = client.post("/analyze", json=request_payload)

    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "HOLD"
    assert body["reasoning"] == "llm_timeout"


def test_analyze_normalizes_model_hold_to_sell_for_unambiguous_bearish_exit():
    app.dependency_overrides[get_provider_dependency] = lambda: StubProvider(
        response_text=json.dumps(
            {
                "action": "HOLD",
                "confidence": 0.52,
                "reasoning": "The model missed the documented exit rubric.",
                "key_indicators": [],
                "invalidation_condition": "n/a",
            }
        )
    )

    request_payload = _load_fixture("regression_bearish_open.json")
    request_payload.pop("provider_override", None)
    with TestClient(app) as client:
        response = client.post("/analyze", json=request_payload)

    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "SELL"
    assert body["raw_response"]["model_action"] == "HOLD"
    assert body["raw_response"]["semantic_action"] == "SELL"
    assert len(body["raw_response"]["exit_confirmations"]) >= 3


def test_analyze_never_raises_an_unhandled_exception_on_failure():
    app.dependency_overrides[get_provider_dependency] = lambda: StubProvider(
        raise_exc=RuntimeError("boom")
    )

    request_payload = _load_fixture("analyze_request_ethusdt.json")
    with TestClient(app) as client:
        response = client.post("/analyze", json=request_payload)

    assert response.status_code == 200


def test_analyze_uses_dependency_provider_when_no_override_present():
    app.dependency_overrides[get_provider_dependency] = lambda: StubProvider(
        response_text=json.dumps(
            {
                "action": "HOLD",
                "confidence": 0.5,
                "reasoning": "n/a",
                "key_indicators": [],
                "invalidation_condition": "n/a",
            }
        )
    )

    request_payload = _load_fixture("analyze_request_btcusdt.json")
    with TestClient(app) as client:
        response = client.post("/analyze", json=request_payload)

    assert response.status_code == 200
    assert response.json()["model_name"] == "stub:stub-model"


def test_analyze_routes_through_provider_override_bypassing_the_injected_default(monkeypatch):
    """PROJECT.md Section 3/8.4: the Scheduler computes the effective LLM
    config and forwards it as `provider_override` on every real request; the
    DI-injected default provider must not be used when it's present."""
    captured_prompts: list[tuple[str, str]] = []
    captured_settings: list[LLMServiceSettings] = []
    override_provider = StubProvider(
        response_text=json.dumps(
            {
                "action": "BUY",
                "confidence": 0.9,
                "reasoning": "override path",
                "key_indicators": [],
                "invalidation_condition": "n/a",
            }
        ),
        captured_prompts=captured_prompts,
    )

    def fake_get_provider(settings: LLMServiceSettings):
        captured_settings.append(settings)
        return override_provider

    monkeypatch.setattr(main_module, "get_provider", fake_get_provider)
    # The DI-injected provider should never be called once an override is present.
    app.dependency_overrides[get_provider_dependency] = lambda: StubProvider(
        raise_exc=AssertionError("DI-injected provider must not be used when overriding")
    )

    request_payload = _load_fixture("analyze_request_btcusdt.json")
    request_payload["provider_override"] = {
        "llm_provider": "ollama",
        "ollama_model": "qwen2.5:7b",
        "ollama_temperature": 0.9,
    }
    with TestClient(app) as client:
        response = client.post("/analyze", json=request_payload)

    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "BUY"
    assert body["model_name"] == "stub:stub-model"
    assert captured_settings[0].llm_provider == "ollama"
    assert captured_settings[0].ollama_model == "qwen2.5:7b"
    assert captured_settings[0].ollama_temperature == 0.9
    assert "provider_override" not in captured_prompts[0][1]
