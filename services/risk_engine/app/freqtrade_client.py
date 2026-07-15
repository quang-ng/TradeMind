from decimal import Decimal
from typing import Any

import httpx
from common.config import FreqtradeSettings
from pydantic import ValidationError

from .schemas import FreqtradeTrade


class FreqtradeUnavailable(Exception):
    """Raised for any transport-level failure talking to Freqtrade — the
    caller's job is to turn this into `Order(FAILED)` + an alert
    (PROJECT.md Section 9.4), never to let it propagate into an approval."""


class FreqtradeClient:
    """Thin wrapper around Freqtrade's REST API (PROJECT.md Section 14 rule
    8: this is the only code path that may call `forceenter`/`forceexit`).

    Freqtrade's `forceenter` has no field for a per-trade custom stop-loss
    — only the strategy's static `stoploss` (see
    `freqtrade/user_data/strategies/ExternalSignalStrategy.py`). The
    Risk Engine's computed `stop_loss_price` (Section 9.2) is still
    persisted to `RiskDecision` for audit; it is not passed here.
    """

    def __init__(self, settings: FreqtradeSettings | None = None, http_client: Any = None) -> None:
        self._settings = settings or FreqtradeSettings()
        self._owns_client = http_client is None
        self._http_client = http_client or httpx.AsyncClient(
            base_url=self._settings.freqtrade_api_url,
            auth=httpx.BasicAuth(
                self._settings.freqtrade_api_user, self._settings.freqtrade_api_pass
            ),
            timeout=self._settings.freqtrade_request_timeout_seconds,
        )

    async def forceenter(self, *, pair: str, stake_amount: Decimal) -> dict:
        """PROJECT.md Section 5.1 step 8."""
        return await self._post(
            "/api/v1/forceenter",
            {"pair": pair, "side": "long", "stakeamount": float(stake_amount)},
        )

    async def forceexit(self, *, trade_id: int) -> dict:
        """PROJECT.md Section 5.1 (exit path, Phase 3)."""
        return await self._post("/api/v1/forceexit", {"tradeid": str(trade_id)})

    async def get_trade(self, *, trade_id: int) -> FreqtradeTrade:
        """Return Freqtrade's authoritative state for one submitted trade."""
        payload = await self._get(f"/api/v1/trade/{trade_id}")
        try:
            return FreqtradeTrade.model_validate(payload)
        except ValidationError as exc:
            raise FreqtradeUnavailable(f"invalid trade response: {exc}") from exc

    async def _post(self, path: str, payload: dict) -> dict:
        try:
            response = await self._http_client.post(path, json=payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise FreqtradeUnavailable(str(exc)) from exc
        try:
            return response.json()
        except ValueError as exc:
            raise FreqtradeUnavailable(f"malformed response: {exc}") from exc

    async def _get(self, path: str) -> dict:
        try:
            response = await self._http_client.get(path)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise FreqtradeUnavailable(str(exc)) from exc
        if not isinstance(payload, dict):
            raise FreqtradeUnavailable("malformed response: expected an object")
        return payload

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http_client.aclose()
