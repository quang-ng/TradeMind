from decimal import Decimal

from pydantic import BaseModel


class FreqtradeWebhookPayload(BaseModel):
    """PROJECT.md Section 11 `POST /webhooks/freqtrade`. Field set matches
    `freqtrade/user_data/config.json.tpl`'s webhook templates — Freqtrade
    renders every templated value as a JSON string, so the numeric fields
    below rely on Pydantic's str -> Decimal/int coercion."""

    event: str
    trade_id: int
    pair: str
    secret: str
    open_rate: Decimal | None = None
    amount: Decimal | None = None
    open_date: str | None = None
    close_rate: Decimal | None = None
    profit_amount: Decimal | None = None
    profit_ratio: Decimal | None = None
    close_date: str | None = None
