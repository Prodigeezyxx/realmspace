"""
The insight agent, and the three properties that make an insight worth reading.

The specification is `floats-agent`'s, adopted verbatim (see the consumer's
docstring): *"periodic bounded graph snapshot → `insight.generated` with text +
supporting event IDs; shown on `/live`; click-through opens the underlying
events"*, with the acceptance clause *"insights on `/live` trace to source
events"*.

**Every claim resolves.** The refs are event seqs that can be read back out of
the log, and they point at the events the number actually came from. An insight
nobody can check is the same failure as the report's invented `1,287 visitors`.

**A replay reproduces it exactly.** The trigger is event time, not the clock, so
rewinding a cursor regenerates the same windows with the same text and the same
citations. Wall-clock triggering would make the log's meaning depend on when it
happened to be read.

**Nothing about a person reaches it.** `insight.generated` is anonymous in the
browser contract and on the anonymised cloud-sync path, and this file asserts
that against a session that has consented contacts and handoffs in the very
window being summarised.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid

import pytest
from neo4j import AsyncSession as GraphSession
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.consumers.attribution import AttributionConsumer
from app.consumers.identity import IdentityConsumer
from app.consumers.insights import InsightsConsumer
from app.graph import repository as graph_repo
from app.llm import digest as digest_builder
from app.schemas import EventIn
from tests.test_handoff import BASE, S, T, consent, seed_activation


@pytest.fixture(autouse=True)
async def _scope_to_test_tenant(db_session: AsyncSession):
    from tests.conftest import as_tenant

    await as_tenant(db_session, T)
    yield


async def emit(
    db_session: AsyncSession,
    *,
    type: str,
    payload: dict,
    minutes: float,
) -> None:
    await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(),
            tenant_id=T,
            session_id=S,
            type=type,
            payload=payload,
            occurred_at=BASE + dt.timedelta(minutes=minutes),
        ),
    )


async def a_busy_ten_minutes(db_session: AsyncSession) -> None:
    """Two visitors, two zones, one surface — and an event past the boundary.

    The last one matters: a window is only summarised once event time has moved
    past its end, which is what makes the trigger replayable.
    """
    for minute, anon in ((0, "P-001"), (1, "P-002")):
        await emit(
            db_session,
            type="spatial.zone_enter",
            payload={"anon_id": anon, "zone_id": "z_entry", "zone_name": "Entry"},
            minutes=minute,
        )
        await emit(
            db_session,
            type="spatial.dwell",
            payload={
                "anon_id": anon,
                "zone_id": "z_entry",
                "zone_name": "Entry",
                "duration": 30.0,
            },
            minutes=minute + 0.5,
        )
        await emit(
            db_session,
            type="spatial.zone_enter",
            payload={"anon_id": anon, "zone_id": "z_pod", "zone_name": "Pod"},
            minutes=minute + 1,
        )
        await emit(
            db_session,
            type="spatial.dwell",
            payload={
                "anon_id": anon,
                "zone_id": "z_pod",
                "zone_name": "Pod",
                "duration": 200.0,
            },
            minutes=minute + 4,
        )
    await emit(
        db_session,
        type="surface.interaction",
        payload={
            "anon_id": "P-001",
            "surface_id": "s_mirror",
            "surface_label": "AR Mirror",
            "kind": "touch",
        },
        minutes=3,
    )
    # Past the ten-minute boundary: the event that closes the window.
    await emit(
        db_session,
        type="spatial.zone_enter",
        payload={"anon_id": "P-003", "zone_id": "z_entry", "zone_name": "Entry"},
        minutes=11,
    )
    await db_session.commit()


async def insights(db_session: AsyncSession) -> list[dict]:
    rows = await repository.read_events(
        db_session, tenant_id=T, session_id=S, type="insight.generated", limit=20
    )
    return [row.payload for row in rows]


# ── the digest ────────────────────────────────────────────────────────────────


async def test_the_digest_reads_only_anonymous_types() -> None:
    """The privacy property is the type list, not a promise made elsewhere."""
    assert set(digest_builder.SOURCE_TYPES) == {
        "spatial.zone_enter",
        "spatial.dwell",
        "spatial.passby",
        "surface.interaction",
    }
    for type in digest_builder.SOURCE_TYPES:
        assert not type.startswith(("consent.", "identity.", "handoff.", "followup."))


# ── the insight ───────────────────────────────────────────────────────────────


async def test_a_window_is_summarised_from_what_happened_in_it(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    await seed_activation(graph_session)
    await a_busy_ten_minutes(db_session)
    await InsightsConsumer().run_once()

    built = await insights(db_session)
    assert len(built) == 1
    insight = built[0]

    measured = insight["measurements"]
    assert measured["people"] == 2
    assert measured["zone_entries"] == 4
    # Pod holds attention longest: 400s against Entry's 60s.
    assert measured["top_zone"] == "Pod"
    assert measured["top_zone_seconds"] == 400.0
    assert measured["surfaces"] == [{"surface": "AR Mirror", "interactions": 1}]

    assert "Pod" in insight["text"]
    assert insight["basis"] == "deterministic"
    assert insight["generated_by"] == "rule"
    assert insight["window"]["minutes"] == 10
    assert insight["truncated"] is False


async def test_every_ref_resolves_to_the_event_it_cites(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """The acceptance clause. A citation that does not resolve is worse than
    none — it looks like evidence."""
    await seed_activation(graph_session)
    await a_busy_ten_minutes(db_session)
    await InsightsConsumer().run_once()

    insight = (await insights(db_session))[0]
    assert insight["refs"]

    for ref in insight["refs"]:
        row = await repository.get_event_by_seq(
            db_session, tenant_id=T, seq=ref["seq"]
        )
        assert row is not None
        assert str(row.event_id) == ref["event_id"]
        # And they are the events the claim rests on, not the window's traffic:
        # the top zone's dwells and entries, plus the surface's interactions.
        assert row.type in digest_builder.SOURCE_TYPES
        subject = row.payload.get("zone_name") or row.payload.get("surface_label")
        assert subject in ("Pod", "AR Mirror"), row.payload


async def test_a_replay_reproduces_the_same_insight(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """The trigger is event time. A wall-clock one would make a replay of a
    finished session produce different windows from identical events."""
    await seed_activation(graph_session)
    await a_busy_ten_minutes(db_session)
    await InsightsConsumer().run_once()

    before = await insights(db_session)
    assert len(before) == 1

    await repository.reset_cursor(db_session, tenant_id=T, consumer="insights")
    await db_session.commit()
    await InsightsConsumer().run_once()

    after = await insights(db_session)
    assert len(after) == 1
    assert after[0]["text"] == before[0]["text"]
    assert after[0]["refs"] == before[0]["refs"]
    assert after[0]["window"] == before[0]["window"]


async def test_a_quiet_window_produces_nothing(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """"Nothing happened" is a row an operator learns to scroll past, and it
    would move the next window's boundary for no reason."""
    await seed_activation(graph_session)
    await emit(
        db_session,
        type="spatial.zone_enter",
        payload={"anon_id": "P-001", "zone_id": "z_entry", "zone_name": "Entry"},
        minutes=0,
    )
    # Nothing for half an hour, then one event far past the boundary.
    await emit(
        db_session,
        type="spatial.zone_enter",
        payload={"anon_id": "P-002", "zone_id": "z_entry", "zone_name": "Entry"},
        minutes=40,
    )
    await db_session.commit()
    await InsightsConsumer().run_once()

    # The first window had one entry in it and is summarised; the empty windows
    # between are not invented.
    built = await insights(db_session)
    assert len(built) == 1
    assert built[0]["measurements"]["zone_entries"] == 1


async def test_a_session_still_filling_its_window_says_nothing_yet(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    await seed_activation(graph_session)
    for minute in (0, 1, 2):
        await emit(
            db_session,
            type="spatial.zone_enter",
            payload={"anon_id": f"P-{minute}", "zone_id": "z_entry", "zone_name": "Entry"},
            minutes=minute,
        )
    await db_session.commit()
    await InsightsConsumer().run_once()

    assert await insights(db_session) == []


async def test_the_interval_is_the_operators(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    await seed_activation(graph_session)
    await graph_repo.upsert_session(
        graph_session,
        tenant_id=T,
        session_id=S,
        client="Acme",
        insight_interval_minutes=2,
    )
    await a_busy_ten_minutes(db_session)
    await InsightsConsumer().run_once()

    built = await insights(db_session)
    assert len(built) > 1
    assert {i["window"]["minutes"] for i in built} == {2}

    # Windows are contiguous: each starts where the last ended, so no event
    # falls between two insights.
    for earlier, later in zip(built, built[1:]):
        assert earlier["window"]["to"] == later["window"]["from"]


async def test_no_pii_reaches_an_insight(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """An insight is about the room. Asserted against a session where a consented
    contact and their handoff sit in the very window being summarised."""
    await seed_activation(graph_session)
    await graph_repo.upsert_person(
        graph_session,
        tenant_id=T,
        session_id=S,
        anon_id="P-001",
        first_seen=BASE.isoformat(),
        last_seen=BASE.isoformat(),
    )
    await a_busy_ten_minutes(db_session)
    await consent(db_session, anon_id="P-001")
    await IdentityConsumer().run_once()
    await AttributionConsumer().run_once()
    await InsightsConsumer().run_once()

    built = await insights(db_session)
    assert built
    body = json.dumps(built)
    assert "sam@example.com" not in body
    assert "Sam Rivera" not in body
    assert "contact" not in body


async def test_the_insight_is_a_node_in_the_graph_too(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """`data-model.md`: "every LLM-generated insight is persisted as a
    first-class node in the same graph"."""
    await seed_activation(graph_session)
    await a_busy_ten_minutes(db_session)
    await InsightsConsumer().run_once()

    result = await graph_session.run(
        """
        MATCH (i:Insight {tenant_id: $t})
        OPTIONAL MATCH (i)-[:ABOUT]->(z:Zone)
        RETURN i.text AS text, i.generated_by AS generated_by, z.id AS zone
        """,
        t=T,
    )
    rows = [dict(record) async for record in result]
    assert len(rows) == 1
    assert "Pod" in rows[0]["text"]
    assert rows[0]["generated_by"] == "rule"
    # It is about the zone the claim is about.
    assert rows[0]["zone"] == "z_pod"


async def test_an_event_on_a_boundary_belongs_to_exactly_one_window(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """Fixed windows move both ends. An exclusive start drops the session's very
    first event every time; an inclusive end counts a boundary event in two
    windows. Either way a number is wrong and nothing says so.
    """
    await seed_activation(graph_session)
    # One event at 0, one exactly on the 10-minute boundary, one past it.
    for minutes, anon in ((0, "P-001"), (10, "P-002"), (21, "P-003")):
        await emit(
            db_session,
            type="spatial.zone_enter",
            payload={"anon_id": anon, "zone_id": "z_entry", "zone_name": "Entry"},
            minutes=minutes,
        )
    await db_session.commit()
    await InsightsConsumer().run_once()

    built = await insights(db_session)
    assert [i["window"]["minutes"] for i in built] == [10, 10]

    # The first window keeps the event that starts it; the boundary event counts
    # in the second window and not the first.
    assert built[0]["measurements"]["zone_entries"] == 1
    assert built[1]["measurements"]["zone_entries"] == 1
    assert sum(i["measurements"]["zone_entries"] for i in built) == 2


# ── the surface ───────────────────────────────────────────────────────────────


# An insight is about the room; every role may read it. "reader" was never in
# `USER_ROLES` — the old gate admitted any human role by name, so an invalid
# one passed silently until the capability map made a role mean something.
async def _client(db_session: AsyncSession, role: str = "viewer"):
    from collections.abc import AsyncIterator

    import httpx

    from app.auth.models import AuthUser
    from app.auth.tokens import issue_token
    from app.db import get_session
    from app.main import app

    user_id = f"u_{role}_{T}"
    db_session.add(
        AuthUser(
            user_id=user_id,
            email=f"{user_id}@floats.demo",
            display_name=user_id,
            tenant_id=T,
            role=role,
        )
    )
    await db_session.commit()

    async def override() -> AsyncIterator[AsyncSession]:
        yield db_session
        await db_session.commit()

    app.dependency_overrides[get_session] = override
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={
            "Authorization": f"Bearer {issue_token(subject=user_id, tenant_id=T, role=role)}"
        },
    )


async def test_a_click_through_opens_the_events_the_claim_rests_on(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """`floats-agent`'s acceptance clause, end to end: "insights on /live trace
    to source events"."""
    await seed_activation(graph_session)
    await a_busy_ten_minutes(db_session)
    await InsightsConsumer().run_once()

    client = await _client(db_session)
    async with client:
        listed = (await client.get("/v1/insights")).json()
        assert listed["count"] == 1
        insight = listed["insights"][0]
        assert "Pod" in insight["text"]

        opened = (
            await client.get(f"/v1/insights/{insight['seq']}/sources")
        ).json()

    assert opened["count"] == len(insight["refs"])
    assert all(not s["missing"] for s in opened["sources"])
    # And they are the events behind the claim, not the window's whole traffic.
    subjects = {
        s["payload"].get("zone_name") or s["payload"].get("surface_label")
        for s in opened["sources"]
    }
    assert subjects == {"Pod", "AR Mirror"}


async def test_a_ref_that_does_not_resolve_is_reported_not_dropped(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """An insight quietly showing four of its five sources would be a weaker
    claim presented as the original one."""
    await seed_activation(graph_session)
    await a_busy_ten_minutes(db_session)
    await InsightsConsumer().run_once()

    from sqlalchemy import update

    from app.models import EventLog

    rows = await repository.read_events(
        db_session, tenant_id=T, session_id=S, type="insight.generated", limit=1
    )
    payload = dict(rows[0].payload)
    payload["refs"] = [*payload["refs"], {"seq": 999_999, "event_id": "gone"}]
    await db_session.execute(
        update(EventLog).where(EventLog.seq == rows[0].seq).values(payload=payload)
    )
    await db_session.commit()

    client = await _client(db_session)
    async with client:
        opened = (await client.get(f"/v1/insights/{rows[0].seq}/sources")).json()

    missing = [s for s in opened["sources"] if s["missing"]]
    assert [s["seq"] for s in missing] == [999_999]


# ── the deployment's model budget ─────────────────────────────────────────────


async def test_a_spent_budget_writes_the_measured_insight_and_does_not_stall(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """The spender the budget exists for: this one fires on a timer.

    An exhausted budget is the same situation as a provider outage — the window
    was still measured, and only the prose is missing. A consumer that raised
    here would stall its cursor over a sentence, so the insight still lands and
    still says which kind of thing wrote it.
    """
    from app.config import get_settings

    settings = get_settings()
    before = settings.llm_monthly_token_budget
    settings.llm_monthly_token_budget = 0  # nothing left this month
    try:
        await seed_activation(graph_session)
        await a_busy_ten_minutes(db_session)
        await InsightsConsumer().run_once()
    finally:
        settings.llm_monthly_token_budget = before

    built = await insights(db_session)
    assert len(built) == 1, "the window is still summarised"
    assert built[0]["basis"] == "deterministic"
    assert built[0]["measurements"]["people"] == 2

    dead = await repository.read_events(
        db_session, tenant_id=T, session_id=S, type="dead_letter.recorded", limit=5
    )
    assert not dead, "a spent budget is not a failure to retry"
