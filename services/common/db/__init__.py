from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base for the domain models in PROJECT.md Section 7."""


from common.db import models  # noqa: E402,F401 -- registers tables on Base.metadata for Alembic
