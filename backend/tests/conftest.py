"""
Test fixtures for both stores.

**Postgres** runs against a real `realmspace_test` database, not a mock and not
SQLite. The point is that Postgres constraints behave the way the spec claims —
a mock would just replay our own assumptions back at us, and SQLite has no
ON CONFLICT ... RETURNING semantics to match. The schema is built by running
the real Alembic migration, so the tests also prove the migration works.

**Neo4j** has no equivalent second database: Community edition is
single-database (SHOW DATABASES → only `neo4j` and `system`). So graph tests
share the dev instance and isolate by `tenant_id` instead, wiping their tenant
before each test. Two consequences worth knowing:

  - graph tests only ever touch tenants prefixed `t_test`, never `t_floats`
  - if tenant scoping is broken anywhere, tests leak into each other and fail,
    which is exactly the signal we want given the database cannot enforce it
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from neo4j import AsyncSession as Neo4jAsyncSession
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


# ── graph (Neo4j) ─────────────────────────────────────────────────────────────

# Every tenant a graph test may touch. The wipe below is scoped to these, so a
# test can never delete real data even if it is pointed at the dev instance.
TEST_TENANTS = ("t_test", "t_test_other")


@pytest.fixture(scope="session", autouse=True)
def graph_schema_applied() -> None:
    """Apply the graph migration once per test run, same as the Alembic fixture.

    Sync on purpose — it opens and closes its own event loop, which would clash
    with the per-test loop pytest-asyncio provides.
    """
    import asyncio

    from app.graph.driver import connect, disconnect
    from app.graph.migrations import upgrade

    async def run() -> None:
        settings = get_settings()
        driver = await connect()
        try:
            async with driver.session(database=settings.neo4j_database) as session:
                await upgrade(session)
        finally:
            await disconnect()

    asyncio.run(run())


@pytest.fixture
async def graph_session() -> AsyncIterator[Neo4jAsyncSession]:
    """A Neo4j session with the test tenants wiped first.

    Note this opens its own driver rather than reusing the app's: the app's is
    owned by the FastAPI lifespan, which isn't running under pytest.
    """
    from app.graph.driver import make_driver
    from app.graph.repository import delete_tenant

    settings = get_settings()
    driver = make_driver(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
    try:
        async with driver.session(database=settings.neo4j_database) as session:
            for tenant in TEST_TENANTS:
                await delete_tenant(session, tenant_id=tenant)
            yield session
    finally:
        await driver.close()
