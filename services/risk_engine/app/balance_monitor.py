import asyncio
import logging

import redis.asyncio as redis
from common import redis_keys
from common.account_balance import AccountBalanceSnapshot
from common.config import FreqtradeSettings, RedisSettings

from .freqtrade_client import FreqtradeClient, FreqtradeUnavailable

logger = logging.getLogger(__name__)


async def publish_balance_snapshot(
    redis_client: redis.Redis, freqtrade_client: FreqtradeClient
) -> AccountBalanceSnapshot:
    """Fetch and publish one fresh typed Freqtrade balance snapshot."""
    snapshot = await freqtrade_client.get_account_balance()
    await redis_client.set(
        redis_keys.ACCOUNT_BALANCE_SNAPSHOT_KEY,
        snapshot.model_dump_json(),
        ex=redis_keys.ACCOUNT_BALANCE_SNAPSHOT_TTL_SECONDS,
    )
    return snapshot


async def run_balance_refresh_loop() -> None:
    """Keep the read-only Admin snapshot fresh; invalidate it on any failure."""
    settings = FreqtradeSettings()
    redis_client = redis.from_url(RedisSettings().redis_url, decode_responses=True)
    freqtrade_client = FreqtradeClient(settings)
    try:
        while True:
            try:
                snapshot = await publish_balance_snapshot(redis_client, freqtrade_client)
                logger.info(
                    "account_balance_refreshed",
                    extra={
                        "equity_usdt": str(snapshot.equity_usdt),
                        "free_balance_usdt": str(snapshot.free_balance_usdt),
                    },
                )
            except FreqtradeUnavailable as exc:
                await redis_client.delete(redis_keys.ACCOUNT_BALANCE_SNAPSHOT_KEY)
                logger.error("account_balance_refresh_failed", extra={"error": str(exc)})
            except Exception:
                await redis_client.delete(redis_keys.ACCOUNT_BALANCE_SNAPSHOT_KEY)
                logger.exception("account_balance_refresh_failed")
            await asyncio.sleep(settings.balance_refresh_interval_seconds)
    finally:
        await freqtrade_client.aclose()
        await redis_client.aclose()
