from decimal import Decimal

import httpx
import pytest

from risk_engine.app.freqtrade_client import FreqtradeClient, FreqtradeUnavailable


def _client_with_handler(handler) -> FreqtradeClient:
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
    return FreqtradeClient(http_client=http_client)


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


async def test_raises_freqtrade_unavailable_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    client = _client_with_handler(handler)
    with pytest.raises(FreqtradeUnavailable):
        await client.forceenter(pair="BTC/USDT", stake_amount=Decimal("100"))


async def test_raises_freqtrade_unavailable_on_connection_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = _client_with_handler(handler)
    with pytest.raises(FreqtradeUnavailable):
        await client.forceexit(trade_id=1)


async def test_raises_freqtrade_unavailable_on_malformed_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    client = _client_with_handler(handler)
    with pytest.raises(FreqtradeUnavailable):
        await client.forceenter(pair="BTC/USDT", stake_amount=Decimal("100"))
