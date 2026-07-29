from decimal import Decimal

import httpx
import pytest
from common.config import FreqtradeSettings

from risk_engine.app.freqtrade_client import FreqtradeClient, FreqtradeUnavailable


def _client_with_handler(handler) -> FreqtradeClient:
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
    return FreqtradeClient(
        settings=FreqtradeSettings(freqtrade_exit_retry_delay_seconds=0),
        http_client=http_client,
    )


async def test_forceenter_posts_pair_side_and_stake_amount():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.read()
        return httpx.Response(200, json={"status": "Force entry accepted"})

    client = _client_with_handler(handler)
    result = await client.forceenter(pair="BTC/USDT", stake_amount=Decimal("250.00"))

    assert result == {"status": "Force entry accepted"}
    assert captured["url"].endswith("/api/v1/forceenter")
    assert b'"pair":"BTC/USDT"' in captured["body"]
    assert b'"side":"long"' in captured["body"]
    assert b'"stakeamount":250.0' in captured["body"]


async def test_forceenter_includes_and_verifies_entry_tag_when_provided():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read()
        return httpx.Response(
            200,
            json={
                "trade_id": 42,
                "status": "Force entry accepted",
                "enter_tag": "slpct:0.02",
            },
        )

    client = _client_with_handler(handler)
    await client.forceenter(
        pair="BTC/USDT", stake_amount=Decimal("250.00"), entry_tag="slpct:0.02"
    )

    assert b'"entry_tag":"slpct:0.02"' in captured["body"]


async def test_forceenter_omits_entrytag_when_not_provided():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read()
        return httpx.Response(200, json={"status": "Force entry accepted"})

    client = _client_with_handler(handler)
    await client.forceenter(pair="BTC/USDT", stake_amount=Decimal("250.00"))

    assert b"entry_tag" not in captured["body"]


async def test_forceexit_posts_trade_id():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.read()
        return httpx.Response(200, json={"result": "Created exit order"})

    client = _client_with_handler(handler)
    result = await client.forceexit(trade_id=42)

    assert result == {"result": "Created exit order"}
    assert captured["url"].endswith("/api/v1/forceexit")
    assert b'"tradeid":"42"' in captured["body"]
    assert b'"ordertype":"market"' in captured["body"]


async def test_forceenter_emergency_exits_when_stop_tag_is_not_attached():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url).endswith("/api/v1/forceenter"):
            return httpx.Response(
                200,
                json={"trade_id": 42, "status": "accepted", "enter_tag": "force_entry"},
            )
        return httpx.Response(200, json={"result": "Created exit order"})

    client = _client_with_handler(handler)
    with pytest.raises(FreqtradeUnavailable, match="protective stop tag was not attached"):
        await client.forceenter(
            pair="BTC/USDT", stake_amount=Decimal("250.00"), entry_tag="slpct:0.02"
        )

    assert len(requests) == 2
    assert str(requests[1].url).endswith("/api/v1/forceexit")


async def test_get_trade_returns_typed_trade_state():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/api/v1/trade/42")
        return httpx.Response(
            200,
            json={
                "trade_id": 42,
                "pair": "BTC/USDT",
                "is_open": True,
                "amount": 0.01,
                "open_rate": 60000,
            },
        )

    client = _client_with_handler(handler)
    trade = await client.get_trade(trade_id=42)

    assert trade.trade_id == 42
    assert trade.pair == "BTC/USDT"
    assert trade.amount == Decimal("0.01")


async def test_get_account_balance_returns_live_equity_and_free_usdt():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/api/v1/balance")
        return httpx.Response(
            200,
            json={
                "currencies": [
                    {
                        "currency": "USDT",
                        "free": 87.25,
                        "balance": 100,
                        "used": 12.75,
                        "stake": "USDT",
                    },
                    {
                        "currency": "BTC",
                        "free": 0.0002,
                        "balance": 0.0002,
                        "used": 0,
                        "stake": "USDT",
                    },
                ],
                "total": 115.40,
                "stake": "USDT",
            },
        )

    balance = await _client_with_handler(handler).get_account_balance()

    assert balance.equity_usdt == Decimal("115.4")
    assert balance.free_balance_usdt == Decimal("87.25")
    assert balance.source == "freqtrade"


@pytest.mark.parametrize(
    "payload",
    [
        {"currencies": [], "total": 115, "stake": "USDT"},
        {
            "currencies": [
                {
                    "currency": "USDT",
                    "free": -1,
                    "balance": 100,
                    "used": 0,
                    "stake": "USDT",
                }
            ],
            "total": 100,
            "stake": "USDT",
        },
        {
            "currencies": [
                {
                    "currency": "EUR",
                    "free": 100,
                    "balance": 100,
                    "used": 0,
                    "stake": "EUR",
                }
            ],
            "total": 100,
            "stake": "EUR",
        },
    ],
)
async def test_get_account_balance_rejects_unsafe_payloads(payload):
    client = _client_with_handler(lambda request: httpx.Response(200, json=payload))

    with pytest.raises(FreqtradeUnavailable, match="invalid balance response"):
        await client.get_account_balance()


async def test_raises_freqtrade_unavailable_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    client = _client_with_handler(handler)
    with pytest.raises(FreqtradeUnavailable):
        await client.forceenter(pair="BTC/USDT", stake_amount=Decimal("100"))


async def test_raises_freqtrade_unavailable_on_connection_error():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("connection refused", request=request)

    client = _client_with_handler(handler)
    with pytest.raises(FreqtradeUnavailable):
        await client.forceexit(trade_id=1)
    assert attempts == 3


async def test_raises_freqtrade_unavailable_on_malformed_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    client = _client_with_handler(handler)
    with pytest.raises(FreqtradeUnavailable):
        await client.forceenter(pair="BTC/USDT", stake_amount=Decimal("100"))
