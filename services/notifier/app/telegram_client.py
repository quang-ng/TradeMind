import logging
from typing import Any

import httpx
from common.config import NotifierSettings

logger = logging.getLogger(__name__)


class TelegramClient:
    """Thin wrapper around the Telegram Bot API (PROJECT.md Section 4: the
    Telegram Notifier owns outbound Telegram messages only). Two HTTP calls
    (`sendMessage`, `getUpdates`) don't warrant a full SDK dependency —
    matches the hand-rolled client style already used for Freqtrade
    (`risk_engine/app/freqtrade_client.py`).

    Messages are sent as plain text, deliberately without `parse_mode`.
    Notification text embeds LLM `reasoning` and other untrusted-origin
    strings (PROJECT.md Section 8: the Isolated Zone); enabling Telegram's
    HTML/Markdown parsing would let that content inject formatting or links
    into an operator-facing message.
    """

    def __init__(self, settings: NotifierSettings | None = None, http_client: Any = None) -> None:
        self._settings = settings or NotifierSettings()
        self._owns_client = http_client is None
        self._http_client = http_client or httpx.AsyncClient(
            base_url=f"https://api.telegram.org/bot{self._settings.telegram_bot_token}",
            timeout=15.0,
        )

    async def send_message(self, text: str) -> bool:
        try:
            response = await self._http_client.post(
                "/sendMessage",
                json={"chat_id": self._settings.telegram_chat_id, "text": text},
            )
            response.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            # PROJECT.md Section 9.4: Telegram unreachable -> logged as a
            # warning, never blocks or delays a trading decision.
            logger.warning("telegram_send_failed", extra={"error": str(exc)})
            return False

    async def get_updates(self, *, offset: int | None, timeout_seconds: int = 20) -> list[dict]:
        params: dict[str, Any] = {"timeout": timeout_seconds}
        if offset is not None:
            params["offset"] = offset
        try:
            response = await self._http_client.get(
                "/getUpdates", params=params, timeout=timeout_seconds + 10
            )
            response.raise_for_status()
            return response.json().get("result", [])
        except httpx.HTTPError as exc:
            logger.warning("telegram_get_updates_failed", extra={"error": str(exc)})
            return []

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http_client.aclose()
