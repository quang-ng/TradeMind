from collections.abc import AsyncIterator

from common.db.session import get_session_factory
from sqlalchemy.ext.asyncio import AsyncSession


async def get_db_session() -> AsyncIterator[AsyncSession]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        yield session
