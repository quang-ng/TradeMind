from collections.abc import AsyncIterator

from common.config import RedisSettings
from common.db.session import get_session_factory
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession


async def get_db_session() -> AsyncIterator[AsyncSession]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        yield session


async def get_redis_client() -> AsyncIterator[Redis]:
    client = Redis.from_url(RedisSettings().redis_url, decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()
