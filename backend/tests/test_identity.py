"""
Consent → identity → withdrawal, as executable claims.

The property everything else here serves is the one in `consent-and-identity.md`
§3: an `IDENTIFIED_AS` edge cannot exist without a live consent of the required
tier. The log is append-only, so the capture that created a link is on it
forever — which means the interesting test is not "does consent work" but "does
replaying a capture after a withdrawal put the link back". If it does, every
guarantee in `privacy.md` is decorative.

The second theme is what a withdrawal must *not* touch. The anonymous path was
never consent-gated, and deleting it would silently rewrite reports already
delivered about a person those reports never named.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from neo4j import AsyncSession as GraphSession
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.consumers.identity import IdentityConsumer, contact_id_for
from app.consumers.reanonymise import ReAnonymiseConsumer
from app.graph import repository as graph_repo
from app.schemas import EventIn

T = "t_test"
S = "s_identity"
BASE = dt.datetime(2026, 8, 13, 10, 0, 0, tzinfo=dt.timezone.utc)


@pytest.fixture(autouse=True)
async def _scope_to_test_tenant(db_session: AsyncSession):
    from tests.conftest import as_tenant

    await as_tenant(db_session, T)
    yield


async def seed_person(
    graph_session: GraphSession, *, anon_id: str = "P-012", session_id: str = S
) -> None:
    """A tracked person, as the graph writer would have left one."""
    await graph_repo.upsert_session(
        graph_session, tenant_id=T, session_id=session_id, venue="Test Hall"
    )
    await graph_repo.upsert_person(
        graph_session,
        tenant_id=T,
        session_id=session_id,
        anon_id=anon_id,
        first_seen=BASE.isoformat(),
        last_seen=BASE.isoformat(),
    )


async def capture(
    db_session: AsyncSession,
    *,
    consent_id: str = "c_0001",
    anon_id: str = "P-012",
    tier: str = "T1",
    contact: dict | None = None,
    session_id: str = S,
    at: dt.datetime = BASE,
) -> None:
    payload = {
        "consent_id": consent_id,
        "anon_id": anon_id,
        "tier": tier,
        "basis": "explicit_optin",
        "copy_version": "consent-en-2026-08",
        "captured_by": "kiosk-entrance",
        "source": "qr",
        "captured_at": at.isoformat(),
        "expires_at": None,
    }
    if contact:
        payload["contact"] = contact

    await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(),
            tenant_id=T,
            session_id=session_id,
            type="consent.captured",
            payload=payload,
            occurred_at=at,
        ),
    )
    await db_session.commit()


async def withdraw(
    db_session: AsyncSession,
    *,
    consent_id: str | None = "c_0001",
    contact_id: str | None = None,
    anon_id: str | None = None,
    reason: str = "visitor_request",
    at: dt.datetime = BASE + dt.timedelta(minutes=5),
) -> None:
    await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(),
            tenant_id=T,
            session_id=S,
            type="consent.withdrawn",
            payload={
                "consent_id": consent_id,
                "contact_id": contact_id,
                "anon_id": anon_id,
                "reason": reason,
                "withdrawn_at": at.isoformat(),
            },
            occurred_at=at,
        ),
    )
    await db_session.commit()


async def run_identity() -> None:
    await IdentityConsumer().run_once()


async def run_withdrawals() -> None:
    await ReAnonymiseConsumer().run_once()


async def events_of(db_session: AsyncSession, type: str) -> list:
    return await repository.read_events(
        db_session, tenant_id=T, session_id=S, type=type, limit=100
    )


# ── identification ────────────────────────────────────────────────────────────


async def test_a_consent_creates_the_contact_and_the_link(
    db_session: AsyncSession, graph_session: GraphSession
):
    await seed_person(graph_session)
    await capture(
        db_session, contact={"email": "sam@example.com", "name": "Sam Rivera"}
    )

    await run_identity()

    contact = await graph_repo.contact_for_anon(
        graph_session, tenant_id=T, session_id=S, anon_id="P-012"
    )
    assert contact is not None
    assert contact["email"] == "sam@example.com"

    resolved = await events_of(db_session, "identity.resolved")
    assert len(resolved) == 1
    assert resolved[0].payload["anon_id"] == "P-012"
    # The justification travels with the result, so a CRM adapter can re-check
    # what it is acting on without going looking for it.
    assert resolved[0].payload["consent_id"] == "c_0001"
    assert resolved[0].payload["tier"] == "T1"


async def test_the_consent_record_is_written_even_when_it_permits_nothing_yet(
    db_session: AsyncSession, graph_session: GraphSession
):
    """A T1 capture with no details still happened, and the record of what was
    shown is the thing that settles a later dispute."""
    await seed_person(graph_session)
    await capture(db_session)

    await run_identity()

    result = await graph_session.run(
        "MATCH (c:ConsentEvent {tenant_id: $t, id: 'c_0001'}) RETURN c",
        t=T,
    )
    consent = dict((await result.single())["c"])
    assert consent["copy_version"] == "consent-en-2026-08"
    assert consent["tier"] == "T1"


async def test_the_same_email_at_two_activations_is_one_contact(
    db_session: AsyncSession, graph_session: GraphSession
):
    """What attribution needs to exist at all: a visitor who consents at two
    events under the same address is one person, not two."""
    await seed_person(graph_session)
    await seed_person(graph_session, anon_id="P-900", session_id="s_identity_2")
    await capture(db_session, contact={"email": "Sam@Example.com "})
    await capture(
        db_session,
        consent_id="c_0002",
        anon_id="P-900",
        session_id="s_identity_2",
        contact={"email": "sam@example.com"},
    )

    await run_identity()

    first = await graph_repo.contact_for_anon(
        graph_session, tenant_id=T, session_id=S, anon_id="P-012"
    )
    second = await graph_repo.contact_for_anon(
        graph_session, tenant_id=T, session_id="s_identity_2", anon_id="P-900"
    )
    # Case and surrounding whitespace are not identity.
    assert first["id"] == second["id"]


async def test_two_captures_with_no_email_stay_two_people(
    db_session: AsyncSession, graph_session: GraphSession
):
    """Merging people we cannot identify would be inventing a fact.
    `consent-and-identity.md` §2 puts cross-activation re-identification behind
    T3, not behind a guess."""
    await seed_person(graph_session)
    await seed_person(graph_session, anon_id="P-900")
    await capture(db_session)
    await capture(db_session, consent_id="c_0002", anon_id="P-900")

    await run_identity()

    first = await graph_repo.contact_for_anon(
        graph_session, tenant_id=T, session_id=S, anon_id="P-012"
    )
    second = await graph_repo.contact_for_anon(
        graph_session, tenant_id=T, session_id=S, anon_id="P-900"
    )
    assert first["id"] != second["id"]


async def test_a_capture_for_a_person_the_graph_has_not_seen_is_parked_not_guessed(
    db_session: AsyncSession, graph_session: GraphSession
):
    """The graph writer is usually a poll behind, so this retries — and if the
    track really does not exist, an operator gets a dead letter naming it rather
    than a Contact linked to a person nobody ever detected."""
    await seed_person(graph_session)  # a different track
    await capture(db_session, anon_id="P-404")

    await run_identity()

    parked = await repository.list_dead_letters(db_session, consumer="identity")
    assert len(parked) == 1
    assert "P-404" in parked[0].error
    assert await events_of(db_session, "identity.resolved") == []


async def test_identifying_twice_is_one_identity_event(
    db_session: AsyncSession, graph_session: GraphSession
):
    await seed_person(graph_session)
    await capture(db_session, contact={"email": "sam@example.com"})

    await run_identity()
    await repository.reset_cursor(db_session, consumer="identity", tenant_id=T, to_seq=0)
    await db_session.commit()
    await run_identity()

    assert len(await events_of(db_session, "identity.resolved")) == 1


# ── withdrawal ────────────────────────────────────────────────────────────────


async def test_a_withdrawal_drops_the_link_and_redacts_the_contact(
    db_session: AsyncSession, graph_session: GraphSession
):
    await seed_person(graph_session)
    await capture(
        db_session, contact={"email": "sam@example.com", "name": "Sam Rivera"}
    )
    await run_identity()

    await withdraw(db_session)
    await run_withdrawals()

    assert (
        await graph_repo.contact_for_anon(
            graph_session, tenant_id=T, session_id=S, anon_id="P-012"
        )
        is None
    )
    result = await graph_session.run(
        "MATCH (c:Contact {tenant_id: $t}) RETURN c", t=T
    )
    contact = dict((await result.single())["c"])
    # The node stays as a tombstone — the ids in an already-pushed CRM record
    # still have to resolve to something that says "retracted" — but the PII is
    # gone from it. Setting a property to NULL in Neo4j removes it outright
    # rather than storing a null, which is a stronger erasure than the SET reads
    # like and worth asserting in the form it actually takes.
    assert "email" not in contact
    assert "name" not in contact
    assert contact["redacted_at"] is not None


async def test_a_withdrawal_leaves_the_anonymous_path_alone(
    db_session: AsyncSession, graph_session: GraphSession
):
    """The constraint that shapes the whole re-anonymiser. This data was never
    consent-gated, and deleting it would rewrite reports already delivered."""
    await seed_person(graph_session)
    await graph_repo.upsert_zone(
        graph_session,
        tenant_id=T,
        session_id=S,
        zone_id="z_entry",
        name="Entrance",
        type="entry",
        polygon=[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
    )
    await graph_repo.link_dwelled_in(
        graph_session,
        tenant_id=T,
        session_id=S,
        anon_id="P-012",
        zone_id="z_entry",
        duration=92.4,
        started_at=BASE.isoformat(),
        ended_at=(BASE + dt.timedelta(seconds=92)).isoformat(),
    )
    await capture(db_session, contact={"email": "sam@example.com"})
    await run_identity()

    await withdraw(db_session)
    await run_withdrawals()

    result = await graph_session.run(
        """
        MATCH (p:Person {tenant_id: $t, session_id: $s, anon_id: 'P-012'})
              -[d:DWELLED_IN]->(z:Zone)
        RETURN count(d) AS dwells
        """,
        t=T,
        s=S,
    )
    assert (await result.single())["dwells"] == 1


async def test_the_consent_record_survives_its_own_withdrawal(
    db_session: AsyncSession, graph_session: GraphSession
):
    """Stamped, not deleted. It is the evidence that permission was given and
    then taken back, and a deployment that erased it could not answer the
    question the withdrawal might later raise."""
    await seed_person(graph_session)
    await capture(db_session, contact={"email": "sam@example.com"})
    await run_identity()

    await withdraw(db_session)
    await run_withdrawals()

    result = await graph_session.run(
        "MATCH (c:ConsentEvent {tenant_id: $t, id: 'c_0001'}) RETURN c", t=T
    )
    consent = dict((await result.single())["c"])
    assert consent["withdrawn_at"] is not None
    assert consent["copy_version"] == "consent-en-2026-08"


async def test_a_withdrawal_asks_the_crm_to_retract(
    db_session: AsyncSession, graph_session: GraphSession
):
    """The local graph is ours; a record already pushed to HubSpot is not."""
    await seed_person(graph_session)
    await capture(db_session, contact={"email": "sam@example.com"})
    await run_identity()

    await withdraw(db_session)
    await run_withdrawals()

    retracts = await events_of(db_session, "crm.retract")
    assert len(retracts) == 1
    assert retracts[0].payload["reason"] == "consent_withdrawn"
    assert retracts[0].payload["contact_id"] == contact_id_for(
        T, email="sam@example.com", consent_id="c_0001"
    )
    assert retracts[0].payload["dedupe_key"]


async def test_an_erasure_request_says_so_to_the_crm(
    db_session: AsyncSession, graph_session: GraphSession
):
    await seed_person(graph_session)
    await capture(db_session, contact={"email": "sam@example.com"})
    await run_identity()

    await withdraw(db_session, reason="erasure_request")
    await run_withdrawals()

    assert (await events_of(db_session, "crm.retract"))[0].payload[
        "reason"
    ] == "erasure_request"


async def test_a_withdrawal_before_any_identification_asks_nothing_of_the_crm(
    db_session: AsyncSession, graph_session: GraphSession
):
    """Nothing was pushed, so there is nothing to undo — and a retract for a
    contact that never existed is noise an adapter would have to learn to
    ignore."""
    await seed_person(graph_session)
    await withdraw(db_session, anon_id="P-012", consent_id=None)

    await run_withdrawals()

    assert await events_of(db_session, "crm.retract") == []


async def test_a_withdrawal_can_be_made_by_the_track_alone(
    db_session: AsyncSession, graph_session: GraphSession
):
    """A visitor at a kiosk mid-session knows nothing but the screen in front of
    them. The withdrawal has to work from whichever identifier they have."""
    await seed_person(graph_session)
    await capture(db_session, contact={"email": "sam@example.com"})
    await run_identity()

    await withdraw(db_session, consent_id=None, anon_id="P-012")
    await run_withdrawals()

    assert (
        await graph_repo.contact_for_anon(
            graph_session, tenant_id=T, session_id=S, anon_id="P-012"
        )
        is None
    )


# ── the property the append-only log makes non-obvious ────────────────────────


async def test_replaying_the_capture_after_a_withdrawal_does_not_re_identify(
    db_session: AsyncSession, graph_session: GraphSession
):
    """The one that matters.

    The capture is on the log forever — it is append-only. So the question is not
    whether consent works, it is whether re-reading a consent that was later
    withdrawn puts the link back. If it does, every guarantee in `privacy.md` is
    decorative: a cursor rewind, a crash, or an operator replaying a session
    would silently re-identify somebody who asked not to be.
    """
    await seed_person(graph_session)
    await capture(db_session, contact={"email": "sam@example.com"})
    await run_identity()
    await withdraw(db_session)
    await run_withdrawals()

    # Rewind identity and let it read the capture again.
    await repository.reset_cursor(db_session, consumer="identity", tenant_id=T, to_seq=0)
    await db_session.commit()
    await run_identity()

    assert (
        await graph_repo.contact_for_anon(
            graph_session, tenant_id=T, session_id=S, anon_id="P-012"
        )
        is None
    ), "a replayed capture re-identified somebody who had withdrawn"
    # And nothing was parked over it: the refusal is a decision, not a failure.
    assert await repository.list_dead_letters(db_session, consumer="identity") == []


async def test_a_withdrawal_replayed_is_still_one_retraction(
    db_session: AsyncSession, graph_session: GraphSession
):
    await seed_person(graph_session)
    await capture(db_session, contact={"email": "sam@example.com"})
    await run_identity()
    await withdraw(db_session)
    await run_withdrawals()

    await repository.reset_cursor(
        db_session, consumer="reanonymise", tenant_id=T, to_seq=0
    )
    await db_session.commit()
    await run_withdrawals()

    assert len(await events_of(db_session, "crm.retract")) == 1
