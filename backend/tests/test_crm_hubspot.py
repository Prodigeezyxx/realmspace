"""
The lead lands in HubSpot, and a withdrawal takes it back out.

Phase 4's acceptance names this file's subject directly: *"a LeadHandoff lands
in HubSpot with spatial_intent fields, a withdrawal retracts it"*.

Two properties carry it.

**One visitor is one contact.** Both handoff stages push the same person, and
`integrations.md` §2 says what happens if that is got wrong — "a create-only
implementation will duplicate every lead in the client's CRM". The upsert is
HubSpot's own, keyed on email.

**A withdrawal reaches the copy that already left.** `crm.retract` had a
producer and no reader until now; the test that matters is that the record is
removed, that a replay does not remove it twice, and that the row we keep to
prove it happened no longer names the person.

HubSpot is stubbed at the transport, so these run with no network and no token.
The stub asserts on what was actually sent — the endpoint, the `idProperty`, the
properties — because that is where a field map lands in the wrong place.
"""

from __future__ import annotations

import datetime as dt
import uuid

import httpx
import pytest
from neo4j import AsyncSession as GraphSession
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository, secrets
from app.consumers.attribution import AttributionConsumer
from app.consumers.crm_delivery import CrmDeliveryConsumer
from app.consumers.crm_retract import CrmRetractConsumer
from app.consumers.identity import IdentityConsumer
from app.consumers.reanonymise import ReAnonymiseConsumer
from app.crm import hubspot
from app.crm.base import AdapterError
from app.models import CrmLink, RuleDispatch
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

FIELD_MAP = {
    "spatial_intent.lead_score": "rs_lead_score",
    "spatial_intent.zones_visited": "rs_zones",
    "spatial_intent.dwell_seconds_total": "rs_dwell",
    "consent.tier": "rs_consent_tier",
    "spatial_intent.nothing_here": "rs_absent",
}


@pytest.fixture(autouse=True)
async def _scope_to_test_tenant(db_session: AsyncSession):
    from tests.conftest import as_tenant

    await as_tenant(db_session, T)
    yield


class FakeHubSpot:
    """HubSpot at the wire. Records requests, returns what it is told to."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        #: status → for every call, unless a path-specific override matches.
        self.status = 200
        self.overrides: dict[str, int] = {}
        self.contact_id = "51234"

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)

        status = self.status
        for fragment, override in self.overrides.items():
            if fragment in request.url.path:
                status = override

        if status >= 400:
            return httpx.Response(status, json={"message": "no"})
        if request.method == "DELETE":
            return httpx.Response(204)
        if "batch/upsert" in request.url.path:
            return httpx.Response(200, json={"results": [{"id": self.contact_id}]})
        return httpx.Response(200, json={"results": []})

    @property
    def upserts(self) -> list[httpx.Request]:
        return [r for r in self.requests if "batch/upsert" in r.url.path]

    @property
    def deletes(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "DELETE"]


@pytest.fixture
def hubspot_api(monkeypatch) -> FakeHubSpot:
    """Swap the module's `httpx` for one whose clients talk to the stub.

    Patched at the module rather than at `httpx.AsyncClient`, so nothing else in
    the suite — the webhook delivery, the rule actions — sees a changed httpx.
    """
    fake = FakeHubSpot()

    class _Httpx:
        HTTPError = httpx.HTTPError

        @staticmethod
        def AsyncClient(**kwargs):  # noqa: N802 — mirrors httpx's own name
            return httpx.AsyncClient(
                transport=httpx.MockTransport(fake.handle), **kwargs
            )

    monkeypatch.setattr(hubspot, "httpx", _Httpx)
    return fake


@pytest.fixture
async def connected(db_session: AsyncSession, encryption_key: str) -> None:
    """A tenant with HubSpot connected, as an admin would have left it."""
    await repository.upsert_integration(
        db_session,
        tenant_id=T,
        provider="hubspot",
        secret_ct=secrets.encrypt("pat-na1-token", tenant_id=T, provider="hubspot"),
        secret_hint="oken",
        field_map=FIELD_MAP,
    )
    await db_session.commit()


async def run_chain() -> None:
    """The lead chain, in the order `run.py` registers it."""
    await IdentityConsumer().run_once()
    await ReAnonymiseConsumer().run_once()
    await AttributionConsumer().run_once()
    await CrmDeliveryConsumer().run_once()
    await CrmRetractConsumer().run_once()


async def withdraw(db_session: AsyncSession, *, at_minutes: int = 10) -> None:
    await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(),
            tenant_id=T,
            session_id=S,
            type="consent.withdrawn",
            payload={
                "consent_id": "c_0001",
                "anon_id": "P-012",
                "reason": "visitor_request",
                "withdrawn_at": (
                    BASE + dt.timedelta(minutes=at_minutes)
                ).isoformat(),
            },
            occurred_at=BASE + dt.timedelta(minutes=at_minutes),
        ),
    )
    await db_session.commit()


async def links(db_session: AsyncSession) -> list[CrmLink]:
    return list(
        (await db_session.execute(select(CrmLink).order_by(CrmLink.id))).scalars().all()
    )


async def dispatches(db_session: AsyncSession) -> list[RuleDispatch]:
    return list(
        (
            await db_session.execute(select(RuleDispatch).order_by(RuleDispatch.id))
        )
        .scalars()
        .all()
    )


# ── mapping ───────────────────────────────────────────────────────────────────


def test_the_spatial_fields_go_where_the_tenant_mapped_them() -> None:
    adapter = hubspot.HubSpotAdapter.for_tenant(
        secret="pat-na1-token", field_map=FIELD_MAP
    )
    payload = adapter.map(
        {
            "contact": {
                "email": "sam@example.com",
                "name": "Sam Rivera",
                "company": "Acme",
                "title": "Head of Brand",
            },
            "spatial_intent": {
                "lead_score": 0.72,
                "zones_visited": ["Entry", "Pod"],
                "dwell_seconds_total": 160.0,
            },
            "consent": {"tier": "T2"},
            "dedupe_key": f"{T}:sam@example.com",
        }
    )

    assert payload is not None
    assert payload["idProperty"] == "email"
    assert payload["id"] == "sam@example.com"

    props = payload["properties"]
    # Stock properties every portal has, sent without configuration.
    assert props["email"] == "sam@example.com"
    assert props["firstname"] == "Sam Rivera"
    assert props["jobtitle"] == "Head of Brand"
    # The part no CRM could have known, under the names the tenant chose.
    assert props["rs_lead_score"] == 0.72
    assert props["rs_dwell"] == 160.0
    assert props["rs_consent_tier"] == "T2"
    # A list would be rejected wholesale, taking the rest of the contact with it.
    assert props["rs_zones"] == "Entry;Pod"
    # Mapped, but absent from this handoff: dropped rather than sent as null.
    assert "rs_absent" not in props


def test_an_unmapped_spatial_field_is_not_guessed_at() -> None:
    """A property name we invented would fail the whole request against a portal
    that never created it."""
    adapter = hubspot.HubSpotAdapter.for_tenant(secret="t", field_map={})
    payload = adapter.map(
        {
            "contact": {"email": "sam@example.com"},
            "spatial_intent": {"lead_score": 0.72},
        }
    )
    assert payload is not None
    assert payload["properties"] == {"email": "sam@example.com"}


def test_a_handoff_with_no_email_is_declined_not_invented() -> None:
    """`integrations.md` §2 allows an anonymous handoff. HubSpot has nothing to
    key one on, and a contact built from an anon_id is a person who does not
    exist and whom nobody can ever contact."""
    adapter = hubspot.HubSpotAdapter.for_tenant(secret="t", field_map={})
    assert adapter.map({"contact": {}, "dedupe_key": f"{T}:P-012"}) is None


# ── the calls ─────────────────────────────────────────────────────────────────


async def test_a_rate_limit_is_raised_for_the_consumer_to_retry(
    hubspot_api: FakeHubSpot,
) -> None:
    """integrations.md §8's first named failure. `base.Consumer` owns the
    backoff; a second retry policy in here would disagree with it."""
    hubspot_api.status = 429
    adapter = hubspot.HubSpotAdapter.for_tenant(secret="t", field_map={})

    with pytest.raises(AdapterError) as caught:
        await adapter.upsert({"id": "sam@example.com", "idProperty": "email"})
    assert caught.value.retryable is True
    assert "rate-limited" in str(caught.value)


async def test_a_dead_token_says_so_and_is_not_retryable(
    hubspot_api: FakeHubSpot,
) -> None:
    hubspot_api.status = 401
    adapter = hubspot.HubSpotAdapter.for_tenant(secret="t", field_map={})

    with pytest.raises(AdapterError) as caught:
        await adapter.upsert({"id": "sam@example.com", "idProperty": "email"})
    assert caught.value.retryable is False
    assert "expired, revoked, or missing a scope" in str(caught.value)


async def test_retracting_something_already_gone_succeeds_quietly(
    hubspot_api: FakeHubSpot,
) -> None:
    """The re-anonymiser is retryable and a withdrawal can be replayed, so
    "already gone" is the expected second outcome, not an error."""
    hubspot_api.status = 404
    adapter = hubspot.HubSpotAdapter.for_tenant(secret="t", field_map={})
    assert "already gone" in await adapter.retract("51234")


async def test_retract_says_what_it_actually_did(hubspot_api: FakeHubSpot) -> None:
    """HubSpot's delete is a recycling bin, not an erasure, and the dispatch row
    says so rather than letting "retracted" imply more than happened."""
    adapter = hubspot.HubSpotAdapter.for_tenant(secret="t", field_map={})
    detail = await adapter.retract("51234")
    assert "recycling bin" in detail and "90 days" in detail


# ── delivery, end to end ──────────────────────────────────────────────────────


async def test_a_consented_visitor_lands_in_hubspot_with_their_path(
    db_session: AsyncSession,
    graph_session: GraphSession,
    connected: None,
    hubspot_api: FakeHubSpot,
) -> None:
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)

    await run_chain()

    assert len(hubspot_api.upserts) == 1
    sent = hubspot_api.upserts[0].read().decode()
    assert "batch/upsert" in hubspot_api.upserts[0].url.path
    assert "sam@example.com" in sent
    # The spatial intent, which is the whole reason a booth writes to a CRM.
    assert "rs_lead_score" in sent
    assert hubspot_api.upserts[0].headers["authorization"] == "Bearer pat-na1-token"

    # And the receipt a withdrawal will read.
    rows = await links(db_session)
    assert len(rows) == 1
    assert rows[0].provider == "hubspot"
    assert rows[0].external_id == "51234"
    assert rows[0].retracted_at is None


async def test_both_stages_are_one_contact_and_two_claims(
    db_session: AsyncSession,
    graph_session: GraphSession,
    connected: None,
    hubspot_api: FakeHubSpot,
) -> None:
    """The pairing `consumers/attribution.py` built, seen from the CRM end.

    Two handoffs go out — one while the visitor is on the floor, one with the
    complete path — and both are upserts on the same email, so HubSpot holds one
    contact. Two dispatch claims, because each handoff is a separate outbound
    call that can fail and be resolved on its own.
    """
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)
    await run_chain()
    await end_the_session(db_session)
    await run_chain()

    assert len(hubspot_api.upserts) == 2
    assert all(
        "sam@example.com" in r.read().decode() for r in hubspot_api.upserts
    )

    claims = [d for d in await dispatches(db_session) if d.action_type == "crm:hubspot"]
    assert len(claims) == 2
    assert {c.status for c in claims} == {"delivered"}
    assert {c.kind for c in claims} == {"handoff"}

    # One person, one link — the same claim the shared dedupe_key makes at the
    # CRM end, kept on this side too.
    assert len(await links(db_session)) == 1


async def test_a_replay_does_not_push_twice(
    db_session: AsyncSession,
    graph_session: GraphSession,
    connected: None,
    hubspot_api: FakeHubSpot,
) -> None:
    """The claim, not the receiver, is what makes this safe."""
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)
    await run_chain()

    consumer = CrmDeliveryConsumer()
    await repository.reset_cursor(db_session, consumer=consumer.name, tenant_id=T)
    await db_session.commit()
    await consumer.run_once()

    assert len(hubspot_api.upserts) == 1


async def test_nothing_connected_is_not_a_failure(
    db_session: AsyncSession,
    graph_session: GraphSession,
    hubspot_api: FakeHubSpot,
    encryption_key: str,
) -> None:
    """The handoff is on the log; a CRM connected next week reads it from seq 0.
    Parking it would fill `/ops` with leads nobody asked to deliver."""
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)

    await run_chain()

    assert hubspot_api.requests == []
    assert await dispatches(db_session) == []
    assert await links(db_session) == []


async def test_an_anonymous_handoff_closes_its_claim_rather_than_stranding(
    db_session: AsyncSession,
    graph_session: GraphSession,
    connected: None,
    hubspot_api: FakeHubSpot,
) -> None:
    """A lead with no email cannot reach HubSpot and never will. Parking it puts
    a row on `/ops` that no human action could resolve."""
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session, email=None)

    await run_chain()

    assert hubspot_api.upserts == []
    claims = [d for d in await dispatches(db_session) if d.action_type == "crm:hubspot"]
    assert len(claims) == 1
    assert claims[0].status == "delivered"
    assert "no email" in (claims[0].detail or "")
    assert await links(db_session) == []


async def test_a_failed_push_is_recorded_and_raised(
    db_session: AsyncSession,
    graph_session: GraphSession,
    connected: None,
    hubspot_api: FakeHubSpot,
) -> None:
    """A lead that did not arrive must be visible on `/ops`, not swallowed."""
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)
    hubspot_api.status = 500

    await IdentityConsumer().run_once()
    await AttributionConsumer().run_once()
    await CrmDeliveryConsumer().run_once()

    claims = [d for d in await dispatches(db_session) if d.action_type == "crm:hubspot"]
    assert len(claims) == 1
    assert claims[0].status == "failed"
    assert "500" in (claims[0].detail or "")
    assert await links(db_session) == []


# ── withdrawal ────────────────────────────────────────────────────────────────


async def test_a_withdrawal_removes_the_contact_from_hubspot(
    db_session: AsyncSession,
    graph_session: GraphSession,
    connected: None,
    hubspot_api: FakeHubSpot,
) -> None:
    """The half of `consent-and-identity.md` §5 that leaves this building, and
    the reader `crm.retract` did not have until now."""
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)
    await run_chain()

    await withdraw(db_session)
    await run_chain()

    assert [r.url.path for r in hubspot_api.deletes] == [
        f"/crm/objects/{hubspot.API_VERSION}/contacts/51234"
    ]

    row = (await links(db_session))[0]
    assert row.retracted_at is not None
    assert "recycling bin" in (row.retract_detail or "")
    # The row stays and stops naming them — the ledger's rule, applied to the
    # receipt: "did you stop using their data" is answered by this being here
    # and being empty of them.
    assert row.dedupe_key == f"{T}:[withdrawn]"
    assert row.external_id == "51234"

    verdict = [
        d for d in await dispatches(db_session)
        if d.action_type == "crm_retract:hubspot"
    ]
    assert len(verdict) == 1
    assert verdict[0].status == "delivered"
    # A stuck lead and a stuck withdrawal are different urgencies on /ops.
    assert verdict[0].kind == "retract"


async def test_a_replayed_withdrawal_does_not_call_hubspot_twice(
    db_session: AsyncSession,
    graph_session: GraphSession,
    connected: None,
    hubspot_api: FakeHubSpot,
) -> None:
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)
    await run_chain()
    await withdraw(db_session)
    await run_chain()

    consumer = CrmRetractConsumer()
    await repository.reset_cursor(db_session, consumer=consumer.name, tenant_id=T)
    await db_session.commit()
    await consumer.run_once()

    assert len(hubspot_api.deletes) == 1


async def test_a_withdrawal_for_somebody_never_pushed_does_nothing(
    db_session: AsyncSession,
    graph_session: GraphSession,
    connected: None,
    hubspot_api: FakeHubSpot,
) -> None:
    """A visitor who consented and changed their mind before any delivery. There
    is no pushed record to undo, and no dispatch to show a human."""
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)
    await IdentityConsumer().run_once()

    await withdraw(db_session)
    await ReAnonymiseConsumer().run_once()
    await CrmRetractConsumer().run_once()

    assert hubspot_api.deletes == []
    assert [
        d for d in await dispatches(db_session)
        if d.action_type.startswith("crm_retract")
    ] == []


async def test_a_revoked_integration_can_still_retract(
    db_session: AsyncSession,
    graph_session: GraphSession,
    connected: None,
    hubspot_api: FakeHubSpot,
) -> None:
    """A withdrawal arriving the day after an admin disconnects HubSpot still has
    to authenticate to HubSpot — which is why revoking keeps the ciphertext."""
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)
    await run_chain()

    await repository.revoke_integration(db_session, tenant_id=T, provider="hubspot")
    await db_session.commit()

    await withdraw(db_session)
    await run_chain()

    assert len(hubspot_api.deletes) == 1
    assert (await links(db_session))[0].retracted_at is not None

    # ...but no new lead goes to a disconnected CRM.
    upserts_before = len(hubspot_api.upserts)
    await end_the_session(db_session)
    await run_chain()
    assert len(hubspot_api.upserts) == upserts_before
