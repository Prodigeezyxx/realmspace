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
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.models import ApiKey, AuthUser
from app.auth.tokens import generate_api_key, issue_token
from app.config import get_settings
from app.db import get_session, make_engine, scope_to_tenant
from app.main import app

BACKEND_DIR = Path(__file__).resolve().parent.parent

#: The tenant almost every test works in. `t_other` exists only to be
#: unreachable — see test_auth.py and test_rls.py.
TENANT = "t_floats"

async def as_tenant(session, tenant: str) -> None:
    """Act as `tenant` for the rest of this test.

    Under row-level security a session may only read and write the tenant it has
    declared, so a test that seeds two tenants has to say so — the same
    discipline the application follows, where each request scopes itself from
    its credential.

    The choice is stored on the session rather than in a module-level variable,
    and that is not incidental: pytest loads conftest.py under its own module
    name, so a test doing `from tests.conftest import …` gets a *second* module
    object with its own globals. Module state would have been mutated in one
    copy and read from the other — which is exactly the bug this replaced, and
    it presented as scoping silently reverting after a commit.
    """
    session.sync_session.info["tenant"] = tenant
    await scope_to_tenant(session, tenant)


def _test_url() -> str:
    """The connection tests use — **as the RLS-constrained app role**.

    This is the single most important line in the row-level-security work. The
    fixtures used to connect as `antoniorobles`, a superuser, and a superuser
    bypasses row-level security unconditionally — verified, and FORCE does not
    change it. Every RLS test written against that connection would have passed
    while proving precisely nothing.

    It also means the whole existing suite now runs under the policies, which is
    the real regression check: any query in the codebase that quietly relied on
    seeing across tenants fails here.
    """
    settings = get_settings()
    if not settings.test_database_url:
        raise RuntimeError("TEST_DATABASE_URL is not set in backend/.env")
    if "test" not in settings.test_database_url:
        # cheap guard against pointing the truncating fixtures at the dev DB
        raise RuntimeError("refusing to run tests against a non-test database")
    return settings.test_database_url


def _admin_url() -> str:
    """The owner connection, for fixture setup that must bypass the policies —
    truncating tables between tests, and seeding another tenant's rows so an
    isolation test has something to fail to see."""
    return _test_url().replace("realmspace_app@", "antoniorobles@")


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

    # The owner, not the app role. Migrations create tables, roles, policies and
    # a SECURITY DEFINER function — every one of which the app role is
    # deliberately not allowed to do.
    os.environ["ALEMBIC_DATABASE_URL"] = _admin_url()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """A session on the test database, with all three bus tables emptied first.

    RESTART IDENTITY resets the BIGSERIAL, so seq starts at 1 in every test and
    assertions about ordering stay readable.

    consumer_cursor and dead_letter must be truncated too, not just event_log.
    Leaving cursors behind is a nasty failure: the next test resets seq to 1 but
    a consumer's cursor still points at the previous test's high-water mark, so
    it silently skips every event and the test fails with an empty graph and no
    hint as to why. Tests would pass alone and fail in a suite.
    """
    # Wipe as the owner: TRUNCATE is a privilege the app role deliberately does
    # not have, and it would in any case only be able to see its own tenant.
    admin = make_engine(_admin_url())
    async with async_sessionmaker(admin, expire_on_commit=False)() as cleaner:
        await cleaner.execute(
            text(
                "TRUNCATE event_log, consumer_cursor, dead_letter, "
                "auth_user, api_key RESTART IDENTITY;"
            )
        )
        await cleaner.commit()
    await admin.dispose()

    engine = make_engine(_test_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        # Re-scope on *every* transaction, not once at the start.
        #
        # `set_config(..., is_local => true)` is deliberately transaction-scoped
        # — that is what stops a pooled connection carrying one request's tenant
        # into the next. The consequence is that a commit clears it, so a fixture
        # that scoped once would silently see nothing after the first commit.
        # In the app this is a non-issue: every request scopes itself. Here the
        # listener reproduces that.
        @event.listens_for(session.sync_session, "after_begin")
        def _rescope(_sess, _trans, connection):  # noqa: ANN001
            # Literal rather than a bound parameter: asyncpg uses $1 paramstyle,
            # not %s, and this runs below SQLAlchemy's parameter handling.
            # TENANT is a module constant, never user input.
            tenant = _sess.info.get("tenant", TENANT)
            connection.exec_driver_sql(
                f"SELECT set_config('app.tenant_id', '{tenant}', true)"
            )

        await scope_to_tenant(session, TENANT)
        yield session
    await engine.dispose()


@pytest.fixture
async def dev_user(db_session: AsyncSession) -> AuthUser:
    user = AuthUser(
        user_id="u_test",
        email="test@floats.demo",
        display_name="Test",
        tenant_id=TENANT,
        role="admin",
    )
    db_session.add(user)
    await db_session.commit()
    return user


@pytest.fixture
async def user_token(dev_user: AuthUser) -> str:
    return issue_token(
        subject=dev_user.user_id, tenant_id=dev_user.tenant_id, role=dev_user.role
    )


@pytest.fixture
async def device_key(db_session: AsyncSession) -> str:
    """A producer credential for TENANT. Returns the plaintext."""
    key_id, plaintext, key_hash = generate_api_key()
    db_session.add(
        ApiKey(key_id=key_id, key_hash=key_hash, tenant_id=TENANT, label="test device")
    )
    await db_session.commit()
    return plaintext


@pytest.fixture
async def client(db_session: AsyncSession, user_token: str) -> AsyncIterator[AsyncClient]:
    """HTTP client wired to the app, sharing db_session, **authenticated**.

    Carries a user token only. A user may both read and write, so one credential
    covers every existing test; sending a device key alongside it would actually
    break reads, because `get_principal` prefers the API key and devices are
    write-only. Device behaviour has its own tests in test_auth.py.

    There is no unauthenticated mode. Enforcement having no off switch is the
    point of the task; a suite with a bypass would be testing a configuration
    nobody runs.
    """

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        yield db_session
        await db_session.commit()

    app.dependency_overrides[get_session] = override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {user_token}"},
    ) as ac:
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


# ── consumers ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
async def consumer_wiring() -> AsyncIterator[None]:
    """Point the consumers at the test database and open the graph driver.

    Consumers open their own sessions via `db.SessionLocal` — they are loops, not
    request handlers, so there is no dependency to override the way the HTTP
    tests override get_session. Swapping the factory is the equivalent seam.

    They also call `graph.driver.get_driver()`, which is normally opened by the
    FastAPI lifespan. That isn't running under pytest, so open it here.

    Autouse because forgetting it wouldn't fail loudly — the consumers would
    quietly read and write the *dev* databases instead.
    """
    from app import db as app_db
    from app.graph.driver import connect, disconnect

    engine = make_engine(_test_url())
    original = app_db.SessionLocal
    app_db.SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    await connect()
    try:
        yield
    finally:
        await disconnect()
        app_db.SessionLocal = original
        await engine.dispose()


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
