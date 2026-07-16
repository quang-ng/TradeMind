import json
import uuid

import httpx
import pytest
from common.config import SchedulerSettings
from scheduler.app import jobs

HOUR_MS = 3_600_000
BASE_MS = 1_700_000_000_000


def _candles(count: int) -> list[dict]:
    return [
        {
            "t": BASE_MS + i * HOUR_MS,
            "o": 100.0 + i,
            "h": 101.0 + i,
            "l": 99.0 + i,
            "c": 100.5 + i,
            "v": 10.0,
        }
        for i in range(count)
    ]


class FakeRedis:
    def __init__(self, *, deny_keys: set[str] | None = None):
        self.store: dict[str, str] = {}
        self.deny_keys = deny_keys or set()
        self.xadd_calls: list[tuple[str, dict]] = []
        self.deleted: list[str] = []

    async def set(self, key, value, nx=False, ex=None):
        if key in self.deny_keys:
            return False
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    async def delete(self, key):
        self.store.pop(key, None)
        self.deleted.append(key)

    async def xadd(self, stream, fields):
        self.xadd_calls.append((stream, fields))

    async def aclose(self):
        pass


class FakeResult:
    def first(self):
        return None  # no open position


class FakeLLMConfigState:
    def __init__(self, overrides: dict):
        self.overrides = overrides


class FakeSession:
    def __init__(self, llm_config_overrides: dict | None = None):
        self.added: list = []
        self._llm_config_overrides = llm_config_overrides

    async def execute(self, _stmt):
        return FakeResult()

    async def get(self, _model, _pk):
        if self._llm_config_overrides is None:
            return None  # no persisted LLMConfigState row -> env defaults apply
        return FakeLLMConfigState(self._llm_config_overrides)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    async def commit(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


def _session_factory():
    return FakeSession()


def _http_client_returning(payload: dict) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


LLM_PAYLOAD = {
    "action": "BUY",
    "confidence": 0.75,
    "reasoning": "test reasoning",
    "model_name": "test:model",
    "raw_response": {"raw": "ok"},
}


@pytest.fixture
def settings() -> SchedulerSettings:
    return SchedulerSettings(candle_lookback=25)


async def test_run_cycle_skips_when_lock_already_held(settings):
    redis_client = FakeRedis(deny_keys={jobs.redis_keys.cycle_lock("BTC/USDT")})

    result = await jobs.run_cycle(
        "BTC/USDT",
        redis_client=redis_client,
        session_factory=_session_factory,
        http_client=_http_client_returning(LLM_PAYLOAD),
        settings=settings,
    )

    assert result is None
    assert redis_client.xadd_calls == []


async def test_run_cycle_skips_when_candle_already_processed(monkeypatch, settings):
    candles = _candles(25)
    monkeypatch.setattr(jobs, "fetch_closed_candles", _fake_fetch_closed_candles(candles))
    candle_ts = candles[-1]["t"]
    idempotency_key = jobs.redis_keys.candle_idempotency(
        "BTC/USDT", settings.timeframe, str(candle_ts)
    )
    redis_client = FakeRedis(deny_keys={idempotency_key})

    result = await jobs.run_cycle(
        "BTC/USDT",
        redis_client=redis_client,
        session_factory=_session_factory,
        http_client=_http_client_returning(LLM_PAYLOAD),
        settings=settings,
    )

    assert result is None
    assert redis_client.xadd_calls == []
    # lock was acquired then released even though the cycle was skipped
    assert jobs.redis_keys.cycle_lock("BTC/USDT") in redis_client.deleted


async def test_run_cycle_persists_signal_and_publishes_to_stream(monkeypatch, settings):
    candles = _candles(25)
    monkeypatch.setattr(jobs, "fetch_closed_candles", _fake_fetch_closed_candles(candles))
    redis_client = FakeRedis()

    trace_id = await jobs.run_cycle(
        "BTC/USDT",
        redis_client=redis_client,
        session_factory=_session_factory,
        http_client=_http_client_returning(LLM_PAYLOAD),
        settings=settings,
    )

    assert trace_id is not None
    assert len(redis_client.xadd_calls) == 1
    stream, fields = redis_client.xadd_calls[0]
    assert stream == jobs.redis_keys.SIGNALS_PENDING_STREAM
    assert "signal_id" in fields
    assert jobs.redis_keys.cycle_lock("BTC/USDT") in redis_client.deleted


async def test_run_cycle_falls_back_to_hold_when_llm_service_unreachable(monkeypatch, settings):
    candles = _candles(25)
    monkeypatch.setattr(jobs, "fetch_closed_candles", _fake_fetch_closed_candles(candles))
    redis_client = FakeRedis()
    captured_session = FakeSession()

    def failing_session_factory():
        return captured_session

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    trace_id = await jobs.run_cycle(
        "BTC/USDT",
        redis_client=redis_client,
        session_factory=failing_session_factory,
        http_client=http_client,
        settings=settings,
    )

    assert trace_id is not None
    signal_row = captured_session.added[0]
    assert signal_row.action == "HOLD"


async def test_run_cycle_forwards_effective_llm_config_as_provider_override(monkeypatch, settings):
    """PROJECT.md Section 3/8.4: llm_service has no DB access, so the
    Scheduler loads the effective LLM config (env defaults + any persisted
    `PATCH /config/llm` override) and forwards it on every `/analyze` call."""
    candles = _candles(25)
    monkeypatch.setattr(jobs, "fetch_closed_candles", _fake_fetch_closed_candles(candles))
    redis_client = FakeRedis()
    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(200, json=LLM_PAYLOAD)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    overrides = {"llm_provider": "ollama", "ollama_temperature": 0.9}

    trace_id = await jobs.run_cycle(
        "BTC/USDT",
        redis_client=redis_client,
        session_factory=lambda: FakeSession(llm_config_overrides=overrides),
        http_client=http_client,
        settings=settings,
    )

    assert trace_id is not None
    assert len(captured_requests) == 1
    body = json.loads(captured_requests[0].content)
    assert body["provider_override"] == {
        "llm_provider": "ollama",
        "anthropic_model": "claude-sonnet-5",
        "ollama_model": "llama3.2:3b",
        "ollama_temperature": 0.9,
    }


def _fake_fetch_closed_candles(candles: list[dict]):
    async def _fetch(symbol, timeframe="1h", limit=200):
        return candles

    return _fetch
