"""
Row-level security: the database refuses, not the application.

`multi-tenant.md` §2 asks for isolation "enforced at the DB layer, not just the
app". Auth already made the *outcome* true — the endpoints derive `tenant_id`
from a credential and there is no parameter to forge. These tests are about the
*mechanism*: what happens when the application is bypassed entirely.

Every test here issues raw SQL against the session. That is deliberate — it is
the closest a test can get to "somebody adds a query later and forgets to
scope it", which is the failure the policies exist to make impossible.

**None of this would prove anything if the connection were a superuser**, which
bypasses policies unconditionally even with FORCE. `conftest._test_url()`
connects as `realmspace_app` for exactly this reason.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.db import scope_to_tenant
from app.schemas import EventIn
from tests.conftest import TENANT, as_tenant

OTHER = "t_rls_other"
BASE = dt.datetime(2026, 8, 1, 12, 0, 0, tzinfo=dt.timezone.utc)


def event_for(tenant: str, marker: str) -> EventIn:
    return EventIn(
        event_id=uuid.uuid4(),
        tenant_id=tenant,
        session_id="s_rls",
        type="perception.detection",
        payload={"marker": marker, "anon_id": "P-1", "bbox": [1, 1, 2, 2],
                 "frame_width": 10, "frame_height": 10},
        occurred_at=BASE,
    )


async def seed_both_tenants(session: AsyncSession) -> None:
    """One row per tenant, each written while acting as that tenant."""
    await as_tenant(session, TENANT)
    await repository.append_event(session, event_for(TENANT, "mine"))
    await session.commit()

    await as_tenant(session, OTHER)
    await repository.append_event(session, event_for(OTHER, "theirs"))
    await session.commit()

    await as_tenant(session, TENANT)


# ── the guarantee ─────────────────────────────────────────────────────────────


async def test_the_connection_is_not_a_superuser(db_session: AsyncSession) -> None:
    """Guards every other test in this file.

    A superuser bypasses row-level security unconditionally — FORCE does not
    change that. If the test connection were ever switched back to one, every
    assertion below would pass while proving nothing, silently. This fails
    loudly instead.
    """
    row = (
        await db_session.execute(
            text("select rolsuper, rolbypassrls from pg_roles where rolname = current_user")
        )
    ).one()
    assert row.rolsuper is False, "tests are running as a superuser; RLS is inert"
    assert row.rolbypassrls is False


async def test_policies_are_enabled_and_forced(db_session: AsyncSession) -> None:
    """Asserts configuration, not behaviour — because behaviour cannot reach it.

    FORCE ROW LEVEL SECURITY only changes anything for the table *owner*.
    Everything else in this file connects as the app role, which is not the
    owner, so the policies apply to it whether FORCE is set or not. Turning
    FORCE off and re-running this file was tried: all ten tests still passed.

    That is a real blind spot. FORCE is what stops a process connecting as the
    owner — a migration script, a maintenance job, an ORM misconfigured with the
    admin URL — from seeing every tenant. Since no test running as the app role
    can observe it, the setting is asserted directly.
    """
    rows = (
        await db_session.execute(
            text("""SELECT relname, relrowsecurity, relforcerowsecurity
                      FROM pg_class
                     WHERE relname IN ('event_log','consumer_cursor','dead_letter')
                     ORDER BY relname""")
        )
    ).all()
    assert len(rows) == 3
    for r in rows:
        assert r.relrowsecurity, f"{r.relname}: row-level security is off"
        assert r.relforcerowsecurity, (
            f"{r.relname}: RLS is not FORCEd, so the table owner bypasses it"
        )


async def test_unscoped_connection_sees_nothing(db_session: AsyncSession) -> None:
    """Fails closed. No tenant declared → no rows, not all rows.

    The policy compares tenant_id to `current_setting('app.tenant_id', true)`,
    which is NULL when unset, and NULL never equals anything. A connection that
    forgot to scope itself gets an empty result rather than the whole table.
    """
    await seed_both_tenants(db_session)

    await db_session.execute(text("SELECT set_config('app.tenant_id', '', true)"))
    count = await db_session.scalar(text("SELECT count(*) FROM event_log"))
    assert count == 0


async def test_scoped_connection_sees_only_its_own(db_session: AsyncSession) -> None:
    await seed_both_tenants(db_session)

    rows = (await db_session.execute(text("SELECT tenant_id FROM event_log"))).scalars().all()
    assert set(rows) == {TENANT}


async def test_forging_a_where_clause_returns_nothing(db_session: AsyncSession) -> None:
    """**The test this whole task exists for.**

    Raw SQL, naming the other tenant explicitly, bypassing every repository
    function and every dependency. The row is there — another connection can see
    it — and this one gets nothing. That is the difference between the database
    enforcing isolation and the application remembering to.
    """
    await seed_both_tenants(db_session)

    leaked = await db_session.scalar(
        text("SELECT count(*) FROM event_log WHERE tenant_id = :other"), {"other": OTHER}
    )
    assert leaked == 0

    # and prove the row genuinely exists, so the assertion above is not passing
    # because the setup silently failed
    await as_tenant(db_session, OTHER)
    assert await db_session.scalar(
        text("SELECT count(*) FROM event_log WHERE tenant_id = :other"), {"other": OTHER}
    ) == 1
    await as_tenant(db_session, TENANT)


async def test_writing_for_another_tenant_is_rejected(db_session: AsyncSession) -> None:
    """Isolation that only covered reads would still let a caller write into
    somebody else's tenant. That is what the WITH CHECK half of the policy is
    for."""
    import sqlalchemy.exc

    await as_tenant(db_session, TENANT)
    with pytest.raises(sqlalchemy.exc.ProgrammingError, match="row-level security"):
        await repository.append_event(db_session, event_for(OTHER, "smuggled"))
        await db_session.flush()
    await db_session.rollback()


# ── the pooling trap ──────────────────────────────────────────────────────────


async def test_scope_does_not_survive_into_the_next_transaction(
    db_session: AsyncSession,
) -> None:
    """`set_config(..., is_local => true)` is transaction-scoped, and that is
    load-bearing.

    A plain `SET` would attach the tenant to the *connection*. Connections are
    pooled, so the next request to borrow one would inherit the previous
    request's tenant and be served their data — a cross-tenant leak created by
    the mechanism meant to prevent them.

    Checked at the driver level, underneath the test fixture's re-scoping
    listener, because that listener exists precisely to paper over this in
    tests.
    """
    raw = await db_session.connection()
    await raw.exec_driver_sql("SELECT set_config('app.tenant_id', 'leaky', true)")
    assert (
        await db_session.scalar(text("SELECT current_setting('app.tenant_id', true)"))
        == "leaky"
    )

    await db_session.commit()  # transaction ends — so should the setting

    raw = await db_session.connection()
    after = (
        await raw.exec_driver_sql("SELECT current_setting('app.tenant_id', true)")
    ).scalar()
    assert after != "leaky", "the tenant setting outlived its transaction"


# ── coverage and deliberate exclusions ────────────────────────────────────────


async def test_dead_letter_is_scoped_too(db_session: AsyncSession) -> None:
    """It stores a full traceback, which can quote the event payload — so an
    unscoped dead_letter is a real, if small, cross-tenant leak. It had no
    tenant_id at all before migration 0003."""
    await as_tenant(db_session, OTHER)
    await repository.record_dead_letter(
        db_session, consumer="probe", tenant_id=OTHER, event_seq=1,
        error="traceback that might quote a payload", attempts=3,
    )
    await db_session.commit()

    await as_tenant(db_session, TENANT)
    assert await db_session.scalar(text("SELECT count(*) FROM dead_letter")) == 0


async def test_consumer_cursor_is_scoped_too(db_session: AsyncSession) -> None:
    await as_tenant(db_session, OTHER)
    await repository.advance_cursor(
        db_session, consumer="probe", tenant_id=OTHER, last_seq=99
    )
    await db_session.commit()

    await as_tenant(db_session, TENANT)
    assert await db_session.scalar(text("SELECT count(*) FROM consumer_cursor")) == 0


async def test_auth_tables_are_deliberately_not_scoped(db_session: AsyncSession) -> None:
    """The one deliberate exclusion, exercised rather than assumed.

    Authenticating means reading `api_key` to discover which tenant the caller
    belongs to. A policy keyed on that tenant would need the answer before the
    lookup that produces it — so those two tables stay out, and auth works.
    If someone "helpfully" adds a policy to them later, this fails.
    """
    scoped = (
        await db_session.execute(
            text("""SELECT relname FROM pg_class
                     WHERE relname IN ('auth_user','api_key')
                       AND relrowsecurity""")
        )
    ).scalars().all()
    assert scoped == [], f"auth tables must not be under RLS: {scoped}"


async def test_tenant_discovery_still_works(db_session: AsyncSession) -> None:
    """Consumers must be able to ask which tenants exist — inherently a
    cross-tenant question, and therefore invisible under the policies. That is
    what the SECURITY DEFINER function is for, and it is the single exemption."""
    await seed_both_tenants(db_session)

    tenants = await repository.list_tenants(db_session)
    assert TENANT in tenants and OTHER in tenants

    # the exemption returns identifiers only — never row data
    cols = (
        await db_session.execute(
            text("SELECT * FROM app_tenants() LIMIT 1")
        )
    ).keys()
    assert list(cols) == ["tenant_id"]
