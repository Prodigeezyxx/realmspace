"""
The contextual SDR — a follow-up that names where somebody actually stood.

Phase 5's acceptance: *"post-session, a consented lead receives a draft follow-up
referencing the exact zones/surfaces they engaged"*.

Four properties carry this file, and three of them are about restraint.

**The draft references the real path**, not a template with a name slotted in.
The zones in it are the zones that visitor entered.

**T1 drafts nothing.** `consent-and-identity.md` §2 puts "personalised follow-up
(SDR)" in T2's column. T1 is "take my details", which is a different sentence a
visitor chose instead.

**A withdrawal stops it, checked against the graph and not the event.** The
handoff on the log keeps their name forever; whether they still consent is
current state.

**It never sends.** There is no endpoint that would, and the read side says so on
every response.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from neo4j import AsyncSession as GraphSession
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.consumers.attribution import AttributionConsumer
from app.consumers.erasure import ErasureConsumer
from app.consumers.identity import IdentityConsumer
from app.consumers.reanonymise import ReAnonymiseConsumer
from app.consumers.sdr import SdrConsumer
from app.schemas import EventIn
from tests.test_handoff import (
    BASE,
    S,
    T,
    consent,
    end_the_session,
    seed_activation,
    seed_person_with_a_path,
)


@pytest.fixture(autouse=True)
async def _scope_to_test_tenant(db_session: AsyncSession):
    from tests.conftest import as_tenant

    await as_tenant(db_session, T)
    yield


async def run_chain() -> None:
    await IdentityConsumer().run_once()
    await ReAnonymiseConsumer().run_once()
    await AttributionConsumer().run_once()
    await SdrConsumer().run_once()
    await ErasureConsumer().run_once()


async def drafts(db_session: AsyncSession) -> list[dict]:
    rows = await repository.read_events(
        db_session, tenant_id=T, session_id=S, type="followup.drafted", limit=20
    )
    return [row.payload for row in rows]


async def _client(db_session: AsyncSession, role: str = "operator") -> AsyncClient:
    from collections.abc import AsyncIterator

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
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={
            "Authorization": f"Bearer {issue_token(subject=user_id, tenant_id=T, role=role)}"
        },
    )


async def a_consented_visitor(
    db_session: AsyncSession, graph_session: GraphSession, *, tier: str = "T2"
) -> None:
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session, tier=tier)
    await end_the_session(db_session)


# ── the draft ─────────────────────────────────────────────────────────────────


async def test_the_draft_names_the_zones_that_visitor_actually_entered(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """The acceptance sentence, in one assertion: "referencing the exact
    zones/surfaces they engaged"."""
    await a_consented_visitor(db_session, graph_session)
    await run_chain()

    built = await drafts(db_session)
    assert len(built) == 1
    draft = built[0]

    assert draft["contact"]["email"] == "sam@example.com"
    assert "Sam" in draft["body"]
    # Pod is where this visitor spent longest — `seed_person_with_a_path` dwells
    # 40s at Entry and 120s at Pod.
    assert "Pod" in draft["body"]
    assert draft["grounded_in"]["zones_visited"] == ["Entry", "Pod"]
    assert draft["grounded_in"]["top_dwell_zone"] == "Pod"
    assert draft["basis"] == "deterministic"
    assert draft["sent"] is False


async def test_the_draft_is_built_from_the_complete_path_not_the_first_touch(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """A draft whose one specific detail is "you came in through the door" is
    worse than a generic one, so it waits for `session.ended`."""
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)

    # The `identified` stage exists and is deliberately ignored.
    await IdentityConsumer().run_once()
    await AttributionConsumer().run_once()
    await SdrConsumer().run_once()
    assert await drafts(db_session) == []

    await end_the_session(db_session)
    await run_chain()
    assert len(await drafts(db_session)) == 1


async def test_a_replay_does_not_fill_the_queue_with_copies(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    await a_consented_visitor(db_session, graph_session)
    await run_chain()

    await repository.reset_cursor(db_session, tenant_id=T, consumer="sdr")
    await db_session.commit()
    await SdrConsumer().run_once()

    assert len(await drafts(db_session)) == 1


async def test_the_draft_invents_nothing_when_there_is_nothing_to_say(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """A visitor with no surfaces engaged gets a draft that does not mention any.

    The failure this guards against is a template with "you tried our {surface}"
    in it, filled with something plausible.
    """
    await a_consented_visitor(db_session, graph_session)
    await run_chain()

    draft = (await drafts(db_session))[0]
    assert draft["grounded_in"]["surfaces_engaged"] == []
    assert "tried" not in draft["body"]


# ── the gate ──────────────────────────────────────────────────────────────────


async def test_a_t1_visitor_gets_no_follow_up(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """`consent-and-identity.md` §2 puts "personalised follow-up (SDR)" in T2's
    own column. T1 is "take my details" — a different sentence, chosen instead."""
    await a_consented_visitor(db_session, graph_session, tier="T1")
    await run_chain()

    assert await drafts(db_session) == []


async def test_an_anonymous_handoff_gets_no_follow_up(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    from app.graph import repository as graph_repo

    await seed_activation(graph_session)
    await graph_repo.upsert_session(
        graph_session,
        tenant_id=T,
        session_id=S,
        client="Acme",
        anonymous_handoffs=True,
    )
    await seed_person_with_a_path(graph_session, anon_id="P-777")
    await end_the_session(db_session)
    await run_chain()

    assert await drafts(db_session) == []


async def test_a_visitor_who_withdrew_before_the_draft_gets_none(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """Checked against the graph, not the handoff. The handoff keeps their name
    forever; whether they still consent is current state."""
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)
    await end_the_session(db_session)

    # The handoff is built, and then they change their mind before the SDR runs.
    await IdentityConsumer().run_once()
    await AttributionConsumer().run_once()
    await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(),
            tenant_id=T,
            session_id=S,
            type="consent.withdrawn",
            payload={"consent_id": "c_0001", "anon_id": "P-012"},
            occurred_at=BASE + dt.timedelta(minutes=10),
        ),
    )
    await db_session.commit()
    await ReAnonymiseConsumer().run_once()
    await SdrConsumer().run_once()

    assert await drafts(db_session) == []


# ── the review surface ────────────────────────────────────────────────────────


async def test_the_drafts_are_readable_and_nothing_can_send_them(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    await a_consented_visitor(db_session, graph_session)
    await run_chain()

    client = await _client(db_session)
    async with client:
        body = (await client.get("/v1/followups")).json()

    assert body["count"] == 1
    assert body["sendingSupported"] is False
    assert body["followups"][0]["sent"] is False
    assert "Pod" in body["followups"][0]["draft"]["body"]


async def test_a_reader_cannot_see_somebody_elses_letter(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """A reader may see the activation's numbers. These are a name beside a
    letter written about that person."""
    client = await _client(db_session, role="reader")
    async with client:
        assert (await client.get("/v1/followups")).status_code == 403


async def test_a_withdrawal_strips_the_letter_and_not_just_the_envelope(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """The body says "Hi Sam". Redacting the contact and leaving it is not
    redaction."""
    await a_consented_visitor(db_session, graph_session)
    await run_chain()

    await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(),
            tenant_id=T,
            session_id=S,
            type="consent.withdrawn",
            payload={"consent_id": "c_0001", "anon_id": "P-012"},
            occurred_at=BASE + dt.timedelta(minutes=30),
        ),
    )
    await db_session.commit()
    await run_chain()

    client = await _client(db_session)
    async with client:
        body = (await client.get("/v1/followups")).json()

    shown = body["followups"][0]
    assert shown["withdrawn"] is True
    assert "Sam" not in json.dumps(shown)
    assert shown["draft"]["body"] == "[withdrawn]"
    assert shown["draft"]["subject"] == "[withdrawn]"


async def test_an_erasure_takes_the_body_out_of_the_log(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """Read-time redaction hides it; an erasure has to remove it."""
    from sqlalchemy import select

    from app.models import EventLog
    from tests.test_erasure import request_erasure

    await a_consented_visitor(db_session, graph_session)
    await run_chain()
    assert "Sam" in json.dumps(await drafts(db_session))

    await request_erasure(db_session, consent_id="c_0001")
    await run_chain()

    rows = (
        (await db_session.execute(select(EventLog).order_by(EventLog.seq)))
        .scalars()
        .all()
    )
    assert "Sam" not in json.dumps([row.payload for row in rows])

    draft_row = next(row for row in rows if row.type == "followup.drafted")
    assert draft_row.redacted_at is not None
    assert draft_row.payload["body"] == "[erased]"
    # The measurements survive: they were never consent-gated, and they are what
    # makes the row still evidence that a draft existed and was never sent.
    assert draft_row.payload["grounded_in"]["zones_visited"] == ["Entry", "Pod"]
