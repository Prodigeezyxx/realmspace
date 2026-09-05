"""
Retention actually removing something, and the three things that stop it.

`gtm.md` sells "30-day data retention". Until now that was a clamp on what a
client could *read* — the rows stayed, so the data outlived the window and was
merely invisible to the person who paid for it.

Most of this file is about when the purge declines. A destructive, irreversible
operation over a client's system of record earns that emphasis: the failure modes
are an activation quietly short of the data its own report is built from, a
person's erasure racing a bulk rewrite, and a parked event nobody can ever retry.

Runs against real Postgres and real Neo4j, in `t_test`.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator

import pytest
from neo4j import AsyncSession as GraphSession
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import plans, repository, retention
from app.consumers.retention import RetentionConsumer
from app.consumers.run import CONSUMER_CLASSES
from app.graph import repository as graph_repo
from app.auth.models import Tenant
from app.models import DeadLetter, EventLog
from app.schemas import EventIn
from tests.conftest import as_tenant

T = "t_test"
S = "s_old"
S_LIVE = "s_recent"

NOW = dt.datetime.now(dt.timezone.utc)
#: Comfortably past Booth's 30 days and Pavilion's 90.
LONG_AGO = NOW - dt.timedelta(days=200)
YESTERDAY = NOW - dt.timedelta(days=1)


@pytest.fixture(autouse=True)
async def _scope_to_test_tenant(db_session: AsyncSession) -> AsyncIterator[None]:
    await as_tenant(db_session, T)
    # An explicit tier, so the floor under test is not whatever LEGACY_PLAN is.
    db_session.add(Tenant(tenant_id=T, name="Test Org", plan="booth"))
    await db_session.commit()
    yield


async def emit(
    db_session: AsyncSession,
    *,
    type: str,
    at: dt.datetime,
    session_id: str = S,
    payload: dict | None = None,
) -> EventLog:
    row, _ = await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(), tenant_id=T, session_id=session_id,
            type=type, payload=payload or {"anon_id": "P-1", "zone_id": "z_a"},
            occurred_at=at,
        ),
    )
    await db_session.commit()
    return row


async def catch_every_consumer_up(db_session: AsyncSession) -> None:
    """Advance every cursor past the log, so check 1 is satisfied.

    Written as "every consumer in the registry" rather than a list, because the
    check under test reads that registry — a hand-written list here would drift
    from it and start passing for the wrong reason.
    """
    head = (
        await db_session.execute(select(EventLog.seq).order_by(EventLog.seq.desc()))
    ).scalars().first() or 0
    for cls in CONSUMER_CLASSES:
        # Not retention itself: its cursor has to stay behind the request it is
        # about to be handed, or it reads nothing and the purge never runs.
        if cls.name == "retention":
            continue
        await repository.advance_cursor(
            db_session, consumer=cls.name, tenant_id=T, last_seq=head + 1000
        )
    await db_session.commit()


async def run_purge(db_session: AsyncSession) -> list[dict]:
    """Request a purge, run the consumer, return the receipts."""
    await emit(
        db_session, type=retention.PURGE_REQUESTED, at=NOW,
        session_id="__tenant__", payload={"requested_by": "u_admin"},
    )
    # Everybody else caught up; retention's own cursor is left where it is, so
    # it reads the request it was just handed. Rewinding it here would trip the
    # purge watermark on a second call, which is the guard working.
    await catch_every_consumer_up(db_session)
    await RetentionConsumer().run_once()

    rows = await repository.read_events(
        db_session, tenant_id=T, session_id="__tenant__", type=retention.PURGED, limit=10
    )
    return [r.payload for r in rows]


async def events_now(db_session: AsyncSession, session_id: str = S) -> list[EventLog]:
    await db_session.commit()
    # The consumer writes on its own connection. Without expiring, SQLAlchemy
    # hands back the instance already in this session's identity map — the row
    # as it was before the purge — and the assertion checks a stale object. The
    # same trap `routers/rules.py` records about `get_rule` before an upsert.
    db_session.expire_all()
    return (
        await db_session.execute(
            select(EventLog)
            .where(EventLog.session_id == session_id)
            .order_by(EventLog.seq)
        )
    ).scalars().all()


# ── what it removes, and what it leaves ──────────────────────────────────────


async def test_an_old_event_keeps_its_shape_and_loses_its_contents(
    db_session: AsyncSession,
):
    """The whole design in one assertion.

    The row stays so derived ids still resolve, dead letters still point at
    something and cursors stay contiguous. What goes is everything the client's
    data actually is.
    """
    row = await emit(db_session, type="spatial.dwell", at=LONG_AGO,
                     payload={"anon_id": "P-1", "zone_id": "z_a", "duration": 42})
    # Read off before the purge and held as plain values, for the reason above.
    before = (row.seq, row.event_id, row.type, row.occurred_at, row.recorded_at)

    receipts = await run_purge(db_session)

    [after] = [e for e in await events_now(db_session) if e.seq == before[0]]
    assert (after.seq, after.event_id, after.type, after.occurred_at, after.recorded_at) == before
    assert after.payload == {}
    assert after.purged_at is not None
    # Its own column — never `redacted_at`, which means somebody asked to be
    # forgotten and whose counts an auditor reads.
    assert after.redacted_at is None
    assert receipts[0]["events_purged"] == 1


async def test_an_event_inside_the_window_is_untouched(db_session: AsyncSession):
    """And it is measured on `occurred_at`, not `recorded_at`.

    A batch buffered through an outage and replayed a week late must not earn a
    week of extra retention for having arrived late — the rule
    `plans.retention_floor` states and this is where it would break silently.
    """
    # Plain ints, taken before anything is expired — holding the ORM instances
    # across the purge and reading `.seq` afterwards triggers a lazy refresh
    # outside the async context.
    old_seq = (await emit(db_session, type="spatial.dwell", at=LONG_AGO)).seq
    recent_seq = (
        await emit(db_session, type="spatial.dwell", at=YESTERDAY, session_id=S_LIVE)
    ).seq

    await run_purge(db_session)

    # Values pulled out as each list is read: every call to `events_now`
    # expires this session, so an instance held across two of them refreshes
    # lazily and blows up outside the async context.
    [aged] = [e.payload for e in await events_now(db_session) if e.seq == old_seq]
    [live] = [
        (e.payload, e.purged_at)
        for e in await events_now(db_session, S_LIVE)
        if e.seq == recent_seq
    ]

    assert aged == {}
    assert live[0] != {}
    assert live[1] is None


async def test_the_receipts_survive_their_own_purge(db_session: AsyncSession):
    """A retention policy that ate its own receipts could not prove it ran."""
    await emit(db_session, type="spatial.dwell", at=LONG_AGO)
    await emit(db_session, type="erasure.completed", at=LONG_AGO,
               payload={"contact_ids": ["c_1"], "events_redacted": 3})
    await emit(db_session, type=retention.PURGED, at=LONG_AGO,
               payload={"events_purged": 7})

    await run_purge(db_session)

    kept = {
        e.type: e.payload
        for e in await events_now(db_session)
        if e.type.startswith(("erasure.", "retention."))
    }
    assert kept["erasure.completed"] == {"contact_ids": ["c_1"], "events_redacted": 3}
    assert kept[retention.PURGED] == {"events_purged": 7}


async def test_running_it_twice_is_a_no_op(db_session: AsyncSession):
    """Idempotent by construction — already-purged rows are skipped."""
    await emit(db_session, type="spatial.dwell", at=LONG_AGO)

    first = await run_purge(db_session)
    assert first[0]["events_purged"] == 1

    # A second request, which is the correct way to re-run one: the purge is
    # idempotent because already-emptied rows are skipped.
    await emit(
        db_session, type=retention.PURGE_REQUESTED, at=NOW,
        session_id="__tenant__", payload={"requested_by": "u_admin"},
    )
    await catch_every_consumer_up(db_session)
    await RetentionConsumer().run_once()

    rows = await events_now(db_session)
    assert sum(1 for e in rows if e.purged_at is not None) == 1


# ── the three refusals ───────────────────────────────────────────────────────


async def test_it_refuses_while_a_consumer_is_behind(db_session: AsyncSession):
    """The sharpest check.

    Emptying an event nothing has processed produces no error and no gap — just
    a smaller number on a client's report, which is the shape of failure this
    codebase keeps finding.
    """
    await emit(db_session, type="spatial.dwell", at=LONG_AGO)
    request = await emit(
        db_session, type=retention.PURGE_REQUESTED, at=NOW,
        session_id="__tenant__", payload={"requested_by": "u_admin"},
    )
    # Everybody caught up except one, which is the case that must refuse.
    await catch_every_consumer_up(db_session)
    await repository.reset_cursor(
        db_session, consumer="graph_writer", tenant_id=T, to_seq=0
    )
    await repository.reset_cursor(
        db_session, consumer="retention", tenant_id=T, to_seq=request.seq - 1
    )
    await db_session.commit()

    with pytest.raises(retention.NotYet, match="graph_writer"):
        await RetentionConsumer().handle(request)


async def test_it_refuses_while_an_erasure_is_in_flight(db_session: AsyncSession):
    """Both rewrite log rows; only one of them is somebody's legal right."""
    await emit(db_session, type="spatial.dwell", at=LONG_AGO)
    await emit(db_session, type="erasure.requested", at=YESTERDAY,
               payload={"contact_id": "c_1"})
    request = await emit(
        db_session, type=retention.PURGE_REQUESTED, at=NOW,
        session_id="__tenant__", payload={"requested_by": "u_admin"},
    )
    await catch_every_consumer_up(db_session)

    with pytest.raises(retention.NotYet, match="erasure"):
        await RetentionConsumer().handle(request)


async def test_it_refuses_while_a_dead_letter_points_into_the_window(
    db_session: AsyncSession,
):
    """A parked event with no payload could never be retried."""
    old_seq = (await emit(db_session, type="spatial.dwell", at=LONG_AGO)).seq
    db_session.add(
        DeadLetter(
            tenant_id=T, consumer="graph_writer", event_seq=old_seq,
            error="boom", attempts=3,
        )
    )
    request = await emit(
        db_session, type=retention.PURGE_REQUESTED, at=NOW,
        session_id="__tenant__", payload={"requested_by": "u_admin"},
    )
    await catch_every_consumer_up(db_session)

    with pytest.raises(retention.NotYet, match="dead letter"):
        await RetentionConsumer().handle(request)


# ── the replay guard ─────────────────────────────────────────────────────────


async def test_a_rewind_into_a_purged_window_is_refused(db_session: AsyncSession):
    """The wrong replay is unwritable rather than merely wrong.

    Payloads are gone, so replaying from zero would feed consumers empty events
    and rebuild a wrong graph — quietly, because most consumers skip what they
    cannot read.
    """
    await emit(db_session, type="spatial.dwell", at=LONG_AGO)
    await run_purge(db_session)

    watermark = await repository.purge_watermark(db_session, tenant_id=T)
    assert watermark > 0

    with pytest.raises(repository.PurgedWindow, match="purged for retention"):
        await repository.reset_cursor(
            db_session, consumer="graph_writer", tenant_id=T, to_seq=0
        )

    # Forward of the watermark is still an ordinary operator rewind.
    await repository.reset_cursor(
        db_session, consumer="graph_writer", tenant_id=T, to_seq=watermark
    )


async def test_the_watermark_only_moves_forward(db_session: AsyncSession):
    """A tier downgrade must not un-forbid a replay of an emptied window."""
    await repository.set_purge_watermark(
        db_session, tenant_id=T, seq=500, through=NOW
    )
    await repository.set_purge_watermark(
        db_session, tenant_id=T, seq=10, through=LONG_AGO
    )
    assert await repository.purge_watermark(db_session, tenant_id=T) == 500


# ── the graph half ───────────────────────────────────────────────────────────


async def test_the_visitors_go_and_the_booth_stays(
    db_session: AsyncSession, graph_session: GraphSession
):
    """Emptying the log and leaving the graph would be a half-measure.

    But zones are the operator's configuration, and deleting them would make a
    purged activation indistinguishable from one nobody ever set up — a
    distinction `useSessionReport` goes out of its way to keep.
    """
    await graph_repo.upsert_session(
        graph_session, tenant_id=T, session_id=S, venue="Old Hall"
    )
    await graph_repo.upsert_zone(
        graph_session, tenant_id=T, session_id=S, zone_id="z_a",
        name="Entry", type="entry", polygon=[[0, 0], [1, 0], [1, 1], [0, 1]],
    )
    await graph_repo.upsert_person(
        graph_session, tenant_id=T, session_id=S, anon_id="P-1",
        first_seen=LONG_AGO.isoformat(), last_seen=LONG_AGO.isoformat(),
    )
    await emit(db_session, type="spatial.dwell", at=LONG_AGO)

    receipts = await run_purge(db_session)

    assert receipts[0]["people_purged"] == 1
    assert await graph_repo.people_in_session(
        graph_session, tenant_id=T, session_id=S
    ) == 0
    # The booth is still there.
    zones = await graph_repo.zones_for_session(
        graph_session, tenant_id=T, session_id=S, include_undrawn=True
    )
    assert [z["id"] for z in zones] == ["z_a"]


async def test_a_session_with_recent_activity_keeps_its_visitors(
    db_session: AsyncSession, graph_session: GraphSession
):
    """An activation with anything inside the window is live.

    Judged on its **latest** event, not its earliest — a long run that started
    before the floor and is still going is not expired.
    """
    await graph_repo.upsert_session(
        graph_session, tenant_id=T, session_id=S_LIVE, venue="Live Hall"
    )
    await graph_repo.upsert_person(
        graph_session, tenant_id=T, session_id=S_LIVE, anon_id="P-9",
        first_seen=LONG_AGO.isoformat(), last_seen=YESTERDAY.isoformat(),
    )
    await emit(db_session, type="spatial.dwell", at=LONG_AGO, session_id=S_LIVE)
    await emit(db_session, type="spatial.dwell", at=YESTERDAY, session_id=S_LIVE)

    await run_purge(db_session)

    assert await graph_repo.people_in_session(
        graph_session, tenant_id=T, session_id=S_LIVE
    ) == 1


# ── a tier that sells no window ──────────────────────────────────────────────


async def test_a_plan_with_no_stated_retention_purges_nothing(
    db_session: AsyncSession,
):
    """`app/plans.py` enforces only what the pricing sheet writes down.

    Deleting a client's data on a limit nobody sold them would be the worst
    possible place to start inventing one.
    """
    await db_session.execute(
        Tenant.__table__.update().where(Tenant.tenant_id == T).values(plan="campaign")
    )
    await db_session.commit()
    assert plans.retention_floor("campaign") is None

    old_seq = (await emit(db_session, type="spatial.dwell", at=LONG_AGO)).seq
    receipts = await run_purge(db_session)

    [after] = [e for e in await events_now(db_session) if e.seq == old_seq]
    assert after.payload != {}
    assert receipts[0]["events_purged"] == 0
    assert "states no retention" in receipts[0]["note"]
