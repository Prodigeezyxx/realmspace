"""
Test fixtures.

Deliberately runs against a real Postgres (realmspace_test), not a mock and not
SQLite. The entire point of this checklist item is that Postgres constraints
behave the way the spec claims — a mock would just replay our own assumptions
back at us, and SQLite has no ON CONFLICT ... RETURNING semantics to match.

The schema is built by running the real Alembic migration, so the tests also
prove the migration works.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.db import get_session, make_engine
from app.main import app

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _test_url() -> str:
    settings = get_settings()
    if not settings.test_database_url:
        raise RuntimeError("TEST_DATABASE_URL is not set in backend/.env")
    if "test" not in settings.test_database_url:
        # cheap guard against pointing the truncating fixtures at the dev DB
        raise RuntimeError("refusing to run tests against a non-test database")
    return settings.test_database_url


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> None:
    """Build the test schema from scratch with the real migration.

    Sync on purpose: alembic's async env.py calls asyncio.run() itself, which
    would blow up inside an already-running event loop.
    """
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    # env.py prefers this over DATABASE_URL, so the dev DB is never touched.
    import os

    os.environ["ALEMBIC_DATABASE_URL"] = _test_url()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """A session on the test database, with the log emptied first.

    RESTART IDENTITY resets the BIGSERIAL, so seq starts at 1 in every test and
    assertions about ordering stay readable.
    """
    engine = make_engine(_test_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await session.execute(text("TRUNCATE event_log RESTART IDENTITY;"))
        await session.commit()
        yield session
    await engine.dispose()


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """HTTP client wired to the app, with every request sharing db_session so a
    test can inspect the same data the endpoint just wrote."""

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        yield db_session
        await db_session.commit()

    app.dependency_overrides[get_session] = override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
