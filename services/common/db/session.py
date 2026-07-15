from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from common.config import DatabaseSettings


@lru_cache
def get_engine() -> AsyncEngine:
    return create_async_engine(DatabaseSettings().postgres_dsn)


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)
