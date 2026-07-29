"""
Database layer: one async engine, one session factory, one FastAPI dependency.

Why async (see the plan): the rest of Phase 1 is a WebSocket feed to /live plus
several consumer loops that each poll their own cursor. Async lets them share
one event loop; the sync driver would need a thread apiece.

Why a connection pool at all: opening a Postgres connection costs a TCP
handshake plus auth plus a backend process fork. The pool keeps a handful open
and hands them out, so a request pays none of that.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    """Declarative base. Base.metadata is what Alembic autogenerate compares
    against the live database."""


def make_engine(url: str, echo: bool = False) -> AsyncEngine:
    return create_async_engine(
        url,
        echo=echo,
        pool_pre_ping=True,  # drop dead connections instead of erroring a request
    )


_settings = get_settings()
engine: AsyncEngine = make_engine(_settings.database_url, echo=_settings.sql_echo)

# expire_on_commit=False: after commit, we still want to read attributes off the
# returned object without SQLAlchemy firing another SELECT to refresh them.
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. One session per request; commits on success, rolls
    back if the handler raised."""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
