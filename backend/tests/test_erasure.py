"""
The erasure job — the one thing that outranks the append-only log.

`consent-and-identity.md` §5 asks for "a tenant-scoped erasure job that removes a
Contact and all PII edges, keeping only anonymised aggregates". Withdrawal
already does the graph and the CRMs. What nothing did was the log, and the log is
where the email actually is — inside `consent.captured`'s contact object and
inside every `handoff.lead` built from it.

Four properties carry this file.

**The email is gone from the raw log.** Not redacted at read time, not hidden
behind an API — gone from the stored payloads, which is the only claim worth
making to somebody exercising Article 17. The test greps the rows.

**It refuses to run before the retraction has landed.** Erasing the Contact first
would delete the record `reanonymise.py` reads to build `crm.retract`, so no
retraction would ever be emitted and the copy in the client's CRM would stay
there. That failure reports success, which is what makes it the one worth a test
of its own.

**The anonymous path survives untouched.** The person's zones, dwells and every
aggregate derived from them are identical before and after. They were never
consent-gated, and deleting them would rewrite reports already delivered about
somebody those reports never named.

**A track id is only meaningful inside its session.** `P-012` at two activations
is two people, and an erasure for one must not touch the other. That is the worst
bug this could have, because it is data loss that looks like compliance.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid

import httpx
import pytest
from neo4j import AsyncSession as GraphSession
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import erasure as erasure_policy
from app import repository, secrets
from app.consumers.attribution import AttributionConsumer
from app.consumers.crm_delivery import CrmDeliveryConsumer
from app.consumers.crm_retract import CrmRetractConsumer
from app.consumers.erasure import ErasureConsumer
from app.consumers.identity import IdentityConsumer
from app.consumers.reanonymise import ReAnonymiseConsumer
from app.graph import repository as graph_repo
from app.models import EventLog
from app.schemas import EventIn
from tests.crm_transport import stub_transport
from tests.test_crm_hubspot import FakeHubSpot
from tests.test_handoff import (
    BASE,
    S,
    T,
    consent,
    end_the_session,
    seed_activation,
    seed_person_with_a_path,
)

EMAIL = "sam@example.com"


@pytest.fixture(autouse=True)
async def _scope_to_test_tenant(db_session: AsyncSession):
    from tests.conftest import as_tenant

    await as_tenant(db_session, T)
    yield


@pytest.fixture
def hubspot_api(monkeypatch) -> FakeHubSpot:
    fake = FakeHubSpot()
    stub_transport(monkeypatch, fake.handle)
    return fake


@pytest.fixture
async def connected(db_session: AsyncSession, encryption_key: str) -> None:
    await repository.upsert_integration(
        db_session,
        tenant_id=T,
        provider="hubspot",
        secret_ct=secrets.encrypt("pat-na1-token", tenant_id=T, provider="hubspot"),
        secret_hint="oken",
        field_map={},
    )
    await db_session.commit()


async def run_chain() -> None:
    """The lead chain plus the erasure, in the order `run.py` registers them."""
    await IdentityConsumer().run_once()
    await ReAnonymiseConsumer().run_once()
    await AttributionConsumer().run_once()
    await CrmDeliveryConsumer().run_once()
    await CrmRetractConsumer().run_once()
    await ErasureConsumer().run_once()


async def request_erasure(
    db_session: AsyncSession,
    *,
    contact_id: str | None = None,
    consent_id: str | None = "c_0001",
    anon_id: str | None = None,
    session_id: str = S,
    at_minutes: int = 20,
) -> None:
    """What `POST /v1/erasure` appends: the withdrawal, then the request."""
    at = BASE + dt.timedelta(minutes=at_minutes)
    subject = contact_id or consent_id or anon_id
    for type, payload in (
        (
            "consent.withdrawn",
            {
                "consent_id": consent_id,
                "contact_id": contact_id,
                "anon_id": anon_id,
                "reason": "erasure_request",
                "withdrawn_at": at.isoformat(),
            },
        ),
        (
            "erasure.requested",
            {
                "consent_id": consent_id,
                "contact_id": contact_id,
                "anon_id": anon_id,
                "requested_by": "dpo@acme.example",
                "note": "ticket GDPR-41",
                "requested_at": at.isoformat(),
            },
        ),
    ):
        await repository.append_event(
            db_session,
            EventIn(
                event_id=uuid.uuid5(uuid.NAMESPACE_URL, f"{type}:{subject}"),
                tenant_id=T,
                session_id=session_id,
                type=type,
                payload=payload,
                occurred_at=at,
            ),
        )
    await db_session.commit()


async def raw_log(db_session: AsyncSession) -> list[EventLog]:
    """Every row, read straight out of the table — no redaction on the way."""
    return list(
        (await db_session.execute(select(EventLog).order_by(EventLog.seq)))
        .scalars()
        .all()
    )


async def completions(db_session: AsyncSession) -> list[dict]:
    rows = await repository.read_events(
        db_session, tenant_id=T, type="erasure.completed", limit=10
    )
    return [row.payload for row in rows]


# ── the policy, without a database ────────────────────────────────────────────


def test_a_track_id_is_only_meaningful_inside_its_session() -> None:
    """`P-012` at two activations is two people. Getting this wrong is data loss
    that looks like compliance."""
    subject = erasure_policy.Subject(tracks={("s_one", "P-012")})

    assert subject.covers(
        type="consent.captured", session_id="s_one", payload={"anon_id": "P-012"}
    )
    assert not subject.covers(
        type="consent.captured", session_id="s_two", payload={"anon_id": "P-012"}
    )


def test_the_subject_closes_over_the_log_from_whichever_end_it_starts() -> None:
    """A request names a consent, a contact or a track, and the chain runs both
    ways — the capture knows the consent and the track, the identification joins
    the track to the contact, the handoff carries the dedupe key."""
    events = [
        ("consent.captured", "s1", {"consent_id": "c1", "anon_id": "P-1"}),
        ("identity.resolved", "s1", {"anon_id": "P-1", "contact_id": "ct_1"}),
        ("handoff.lead", "s1", {"contact": {"id": "ct_1"}, "dedupe_key": "t:sam@x"}),
        # A different person entirely, sharing nothing.
        ("consent.captured", "s1", {"consent_id": "c2", "anon_id": "P-2"}),
    ]

    from_consent = erasure_policy.resolve(
        events, erasure_policy.Subject(consent_ids={"c1"})
    )
    from_contact = erasure_policy.resolve(
        events, erasure_policy.Subject(contact_ids={"ct_1"})
    )

    for subject in (from_consent, from_contact):
        assert subject.contact_ids == {"ct_1"}
        assert subject.consent_ids == {"c1"}
        assert subject.tracks == {("s1", "P-1")}
        assert subject.dedupe_keys == {"t:sam@x"}


def test_redacting_keeps_the_contact_key_and_loses_what_is_in_it() -> None:
    """An absent `contact` means "nobody was named here", which is what an
    anonymous handoff says. An erased lead is not that."""
    cleaned = erasure_policy.redact(
        "handoff.lead",
        {
            "contact": {"id": "ct_1", "email": EMAIL, "name": "Sam"},
            "dedupe_key": f"{T}:{EMAIL}",
            "spatial_intent": {"dwell_seconds_total": 160.0},
        },
    )
    assert cleaned["contact"] == {"id": "ct_1"}
    assert EMAIL not in json.dumps(cleaned)
    # The path is untouched: it was never consent-gated.
    assert cleaned["spatial_intent"] == {"dwell_seconds_total": 160.0}


def test_an_event_with_nothing_to_remove_is_left_alone() -> None:
    """`None` is what keeps `redacted_at` off the rows an erasure only looked at,
    so "what did this touch" stays an honest answer."""
    assert erasure_policy.redact("identity.resolved", {"contact_id": "ct_1"}) is None
    assert erasure_policy.redact("handoff.lead", {"contact": {"id": "ct_1"}}) is None


# ── end to end ────────────────────────────────────────────────────────────────


async def test_the_email_is_gone_from_the_raw_log(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """The only claim worth making to somebody exercising Article 17."""
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)
    await end_the_session(db_session)
    await run_chain()

    before = json.dumps([row.payload for row in await raw_log(db_session)])
    assert EMAIL in before

    await request_erasure(db_session)
    await run_chain()

    rows = await raw_log(db_session)
    assert EMAIL not in json.dumps([row.payload for row in rows])
    assert "Sam Rivera" not in json.dumps([row.payload for row in rows])

    # Only the rows that carried a name are stamped, not everything scanned.
    stamped = {row.type for row in rows if row.redacted_at is not None}
    assert stamped == {"consent.captured", "handoff.lead"}


async def test_what_is_kept_is_the_evidence_the_erasure_was_lawful(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """The consent, its tier, its basis, the copy the person read and the
    withdrawal all survive. None of it names anybody, and together they are what
    a disputed erasure would be settled by."""
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)
    await end_the_session(db_session)
    await run_chain()
    await request_erasure(db_session)
    await run_chain()

    captured = next(
        row for row in await raw_log(db_session) if row.type == "consent.captured"
    )
    assert captured.payload["tier"] == "T2"
    assert captured.payload["basis"] == "explicit_optin"
    assert captured.payload["copy_version"] == "consent-en-2026-08"
    assert captured.payload["consent_id"] == "c_0001"
    # The contact key stays; there is nothing in it.
    assert captured.payload["contact"] == {}

    receipts = await completions(db_session)
    assert len(receipts) == 1
    assert receipts[0]["events_redacted"] >= 2
    assert receipts[0]["contacts_erased"] == 1
    assert EMAIL not in json.dumps(receipts[0])


async def test_the_anonymous_path_is_untouched(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """`privacy.md` has the anonymous path running with no consent at all;
    deleting it would rewrite reports already delivered."""
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)
    await end_the_session(db_session)
    await run_chain()

    before = await graph_repo.dwell_by_zone(graph_session, tenant_id=T, session_id=S)

    await request_erasure(db_session)
    await run_chain()

    assert await graph_repo.dwell_by_zone(graph_session, tenant_id=T, session_id=S) == before
    assert await graph_repo.person_exists(
        graph_session, tenant_id=T, session_id=S, anon_id="P-012"
    )


async def test_the_contact_is_gone_from_the_graph(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """Where the withdrawal leaves a tombstone, the erasure removes the node."""
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)
    await run_chain()

    assert await graph_repo.contacts_in_session(
        graph_session, tenant_id=T, session_id=S
    )

    await request_erasure(db_session)
    await run_chain()

    result = await graph_session.run(
        "MATCH (ct:Contact {tenant_id: $t}) RETURN count(ct) AS n", t=T
    )
    assert (await result.single())["n"] == 0

    # The consent survives it, stamped — see graph_repo.erase_contact.
    result = await graph_session.run(
        "MATCH (c:ConsentEvent {tenant_id: $t}) RETURN c.withdrawn_at AS at", t=T
    )
    assert (await result.single())["at"] is not None


async def test_it_refuses_until_the_crm_copy_has_been_retracted(
    connected: None,
    hubspot_api: FakeHubSpot,
    db_session: AsyncSession,
    graph_session: GraphSession,
) -> None:
    """The failure this ordering exists to prevent reports success and leaves the
    data in the client's CRM."""
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)
    await run_chain()
    assert len(hubspot_api.upserts) == 1

    # HubSpot is down, so the retraction cannot land.
    hubspot_api.overrides = {"contacts/": 503}
    await request_erasure(db_session)
    await ReAnonymiseConsumer().run_once()
    await CrmRetractConsumer().run_once()

    with pytest.raises(RuntimeError, match="not been retracted"):
        await ErasureConsumer().handle(
            next(
                row
                for row in await raw_log(db_session)
                if row.type == "erasure.requested"
            )
        )

    # Nothing was erased while it was refusing.
    assert EMAIL in json.dumps([row.payload for row in await raw_log(db_session)])

    # HubSpot comes back. The parked retraction is re-run — `crm_retract` is
    # retryable from `/ops` precisely so a person's withdrawal is one click
    # rather than a cursor rewind; rewinding is how a test says the same thing.
    hubspot_api.overrides = {}
    await repository.reset_cursor(db_session, tenant_id=T, consumer="crm_retract")
    await db_session.commit()
    await CrmRetractConsumer().run_once()
    await ErasureConsumer().run_once()

    assert EMAIL not in json.dumps([row.payload for row in await raw_log(db_session)])
    assert hubspot_api.deletes


async def test_it_refuses_while_the_withdrawal_has_not_been_carried_out(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """Erasing first would delete the record the re-anonymiser reads to build
    `crm.retract`, so no retraction would ever be emitted."""
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)
    await run_chain()

    await request_erasure(db_session)
    # Deliberately skipping the re-anonymiser.
    with pytest.raises(RuntimeError, match="withdrawal has not been carried out"):
        await ErasureConsumer().handle(
            next(
                row
                for row in await raw_log(db_session)
                if row.type == "erasure.requested"
            )
        )


async def test_erasing_one_visitor_leaves_the_same_track_at_another_activation(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """The bug worth a test: `P-012` in two sessions is two people."""
    other = "s_other_activation"
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)

    await graph_repo.upsert_session(
        graph_session, tenant_id=T, session_id=other, client="Acme"
    )
    await graph_repo.upsert_person(
        graph_session,
        tenant_id=T,
        session_id=other,
        anon_id="P-012",
        first_seen=BASE.isoformat(),
        last_seen=BASE.isoformat(),
    )
    await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(),
            tenant_id=T,
            session_id=other,
            type="consent.captured",
            payload={
                "consent_id": "c_other",
                "anon_id": "P-012",
                "tier": "T2",
                "basis": "explicit_optin",
                "copy_version": "consent-en-2026-08",
                "captured_by": "kiosk",
                "source": "qr",
                "captured_at": BASE.isoformat(),
                "contact": {"email": "other@example.com", "name": "Other Person"},
            },
            occurred_at=BASE,
        ),
    )
    await db_session.commit()
    await run_chain()

    await request_erasure(db_session, consent_id="c_0001")
    await run_chain()

    remaining = json.dumps([row.payload for row in await raw_log(db_session)])
    assert EMAIL not in remaining
    assert "other@example.com" in remaining


async def test_a_replayed_erasure_completes_quietly(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """A rewound cursor finds the work done and says so once, not twice."""
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)
    await end_the_session(db_session)
    await run_chain()
    await request_erasure(db_session)
    await run_chain()

    stamped_before = {
        row.seq: row.redacted_at
        for row in await raw_log(db_session)
        if row.redacted_at is not None
    }

    await repository.reset_cursor(db_session, tenant_id=T, consumer="erasure")
    await db_session.commit()
    await ErasureConsumer().run_once()

    assert len(await completions(db_session)) == 1
    stamped_after = {
        row.seq: row.redacted_at
        for row in await raw_log(db_session)
        if row.redacted_at is not None
    }
    # Nothing was rewritten a second time, so no timestamp moved.
    assert stamped_after == stamped_before


# ── the endpoint ──────────────────────────────────────────────────────────────


async def _client(db_session: AsyncSession, role: str = "admin") -> httpx.AsyncClient:
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
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={
            "Authorization": f"Bearer {issue_token(subject=user_id, tenant_id=T, role=role)}"
        },
    )


async def test_the_endpoint_appends_the_withdrawal_and_the_request(
    db_session: AsyncSession,
) -> None:
    """An erasure asks for the existing withdrawal path by name. Taking its own
    route to the CRMs would be a second implementation of the thing a person's
    rights depend on."""
    client = await _client(db_session)
    async with client:
        response = await client.post(
            "/v1/erasure",
            json={
                "sessionId": S,
                "consentId": "c_0001",
                "requestedBy": "dpo@acme.example",
                "note": "ticket GDPR-41",
            },
        )

    assert response.status_code == 201, response.text
    assert response.json()["type"] == "erasure.requested"

    withdrawals = await repository.read_events(
        db_session, tenant_id=T, session_id=S, type="consent.withdrawn", limit=10
    )
    assert [w.payload["reason"] for w in withdrawals] == ["erasure_request"]


async def test_asking_twice_is_one_right_exercised_twice(
    db_session: AsyncSession,
) -> None:
    """A timestamped id would put a second request on the log for the consumer
    to redo against a person already erased."""
    client = await _client(db_session)
    async with client:
        body = {
            "sessionId": S,
            "consentId": "c_0001",
            "requestedBy": "dpo@acme.example",
        }
        first = await client.post("/v1/erasure", json=body)
        second = await client.post("/v1/erasure", json=body)

    assert first.status_code == 201
    assert second.json()["seq"] == first.json()["seq"]

    requests = await repository.read_events(
        db_session, tenant_id=T, session_id=S, type="erasure.requested", limit=10
    )
    assert len(requests) == 1


async def test_an_erasure_naming_nobody_is_refused(db_session: AsyncSession) -> None:
    client = await _client(db_session)
    async with client:
        response = await client.post(
            "/v1/erasure",
            json={"sessionId": S, "requestedBy": "dpo@acme.example"},
        )
    assert response.status_code == 422
    assert "contactId" in response.json()["detail"]


async def test_an_operator_cannot_erase(db_session: AsyncSession) -> None:
    """The most irreversible act in the system — it rewrites the append-only
    log. `multi-tenant.md` §RBAC puts that with Admin."""
    client = await _client(db_session, role="operator")
    async with client:
        response = await client.post(
            "/v1/erasure",
            json={
                "sessionId": S,
                "consentId": "c_0001",
                "requestedBy": "someone@acme.example",
            },
        )
    assert response.status_code == 403
