"""
A consented visitor becomes a lead, and the lead leaves the building.

Two properties carry this file.

The first is the **two-stage pairing**: a lead is emitted once while the visitor
is still on the floor and again when the doors shut, carrying the same
`dedupe_key` so a destination upserts one lead, and different `event_id`s so the
bus does not swallow the second. Get either half backwards and the failure is
silent — the complete path never leaves the log, or the client's CRM grows two
copies of every lead.

The second is that a **withdrawal stops the flow at every point**, including on
a replay. The `identity.resolved` that started it stays on the log forever, so
re-reading it must produce nothing.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid

import httpx
import pytest
from neo4j import AsyncSession as GraphSession
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.actions.webhook import sign
from app.config import get_settings
from app.consumers.attribution import AttributionConsumer
from app.consumers.handoff_delivery import HandoffDeliveryConsumer
from app.consumers.identity import IdentityConsumer
from app.consumers.reanonymise import ReAnonymiseConsumer
from app.graph import repository as graph_repo
from app.schemas import EventIn

T = "t_test"
S = "s_handoff"
BASE = dt.datetime(2026, 8, 13, 10, 0, 0, tzinfo=dt.timezone.utc)

HANDOFF_URL = "https://receiver.invalid/leads"
SECRET = "s3cret"

ENTRY_POLY = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]


@pytest.fixture(autouse=True)
async def _scope_to_test_tenant(db_session: AsyncSession):
    from tests.conftest import as_tenant

    await as_tenant(db_session, T)
    yield


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "handoff_webhook_url", HANDOFF_URL)
    monkeypatch.setattr(settings, "webhook_signing_secret", SECRET)
    yield


class Posted:
    def __init__(self, status: int = 200) -> None:
        self.calls: list[dict] = []
        self.status = status

    async def __call__(self, url, **kwargs):
        self.calls.append({"url": str(url), **kwargs})
        return httpx.Response(
            self.status, request=httpx.Request("POST", str(url)), text="ok"
        )


@pytest.fixture
def posted(monkeypatch) -> Posted:
    recorder = Posted()
    monkeypatch.setattr(httpx.AsyncClient, "post", recorder)
    return recorder


# ── the activation, as the earlier consumers would have left it ───────────────


async def seed_activation(graph_session: GraphSession) -> None:
    await graph_repo.upsert_session(
        graph_session,
        tenant_id=T,
        session_id=S,
        client="Acme",
        campaign="Pavilion No.7",
        venue="Eko Convention Centre",
        city="Lagos",
        engaged_threshold_seconds=60.0,
        activation_cost=1000.0,
        attribution_model="influenced",
        attribution_window_days=30,
    )
    for zone_id, name, order in (("z_entry", "Entry", 1), ("z_pod", "Pod", 2)):
        await graph_repo.upsert_zone(
            graph_session,
            tenant_id=T,
            session_id=S,
            zone_id=zone_id,
            name=name,
            type="feature",
            polygon=ENTRY_POLY,
            weight=1.0,
            funnel_order=order,
        )
    await graph_repo.upsert_surface(
        graph_session,
        tenant_id=T,
        session_id=S,
        surface_id="s_mirror",
        label="AR Mirror",
        type="screen",
    )


async def seed_person_with_a_path(
    graph_session: GraphSession, *, anon_id: str = "P-012"
) -> None:
    await graph_repo.upsert_person(
        graph_session,
        tenant_id=T,
        session_id=S,
        anon_id=anon_id,
        first_seen=BASE.isoformat(),
        last_seen=(BASE + dt.timedelta(minutes=5)).isoformat(),
    )
    await graph_repo.link_dwelled_in(
        graph_session,
        tenant_id=T,
        session_id=S,
        anon_id=anon_id,
        zone_id="z_entry",
        duration=40.0,
        started_at=BASE.isoformat(),
        ended_at=(BASE + dt.timedelta(seconds=40)).isoformat(),
    )
    await graph_repo.link_dwelled_in(
        graph_session,
        tenant_id=T,
        session_id=S,
        anon_id=anon_id,
        zone_id="z_pod",
        duration=120.0,
        started_at=(BASE + dt.timedelta(minutes=1)).isoformat(),
        ended_at=(BASE + dt.timedelta(minutes=3)).isoformat(),
    )


async def consent(
    db_session: AsyncSession,
    *,
    anon_id: str = "P-012",
    consent_id: str = "c_0001",
    email: str | None = "sam@example.com",
) -> None:
    payload = {
        "consent_id": consent_id,
        "anon_id": anon_id,
        "tier": "T2",
        "basis": "explicit_optin",
        "copy_version": "consent-en-2026-08",
        "captured_by": "kiosk-entrance",
        "source": "qr",
        "captured_at": BASE.isoformat(),
        "expires_at": None,
    }
    if email:
        payload["contact"] = {"email": email, "name": "Sam Rivera"}

    await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(),
            tenant_id=T,
            session_id=S,
            type="consent.captured",
            payload=payload,
            occurred_at=BASE,
        ),
    )
    await db_session.commit()


async def end_the_session(db_session: AsyncSession) -> None:
    await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(),
            tenant_id=T,
            session_id=S,
            type="session.ended",
            payload={"endedBy": "u_test"},
            occurred_at=BASE + dt.timedelta(hours=1),
        ),
    )
    await db_session.commit()


async def run_chain() -> None:
    """The consumers in the order `run.py` registers them."""
    await IdentityConsumer().run_once()
    await ReAnonymiseConsumer().run_once()
    await AttributionConsumer().run_once()
    await HandoffDeliveryConsumer().run_once()


async def handoffs(db_session: AsyncSession) -> list:
    return await repository.read_events(
        db_session, tenant_id=T, session_id=S, type="handoff.lead", limit=50
    )


# ── building the lead ─────────────────────────────────────────────────────────


async def test_a_consented_visitor_becomes_a_lead_with_their_path(
    db_session: AsyncSession, graph_session: GraphSession, posted: Posted
):
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)

    await run_chain()

    leads = await handoffs(db_session)
    assert len(leads) == 1

    lead = leads[0].payload
    assert lead["schema"] == "realmspace.lead_handoff/v1"
    assert lead["contact"]["email"] == "sam@example.com"
    # The part no CRM could have known.
    assert lead["spatial_intent"]["zones_visited"] == ["Entry", "Pod"]
    assert lead["spatial_intent"]["top_dwell_zone"] == "Pod"
    assert lead["spatial_intent"]["dwell_seconds_total"] == 160.0
    # And the permission that allowed any of it to be sent.
    assert lead["consent"]["tier"] == "T2"
    assert lead["consent"]["copy_version"] == "consent-en-2026-08"


async def test_the_roi_context_is_the_operators_settings_not_defaults(
    db_session: AsyncSession, graph_session: GraphSession, posted: Posted
):
    """The model and window have to be the ones agreed before doors opened —
    roi-framework.md §3's whole argument is that choosing them afterwards is an
    argument rather than a measurement."""
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)

    await run_chain()

    roi = (await handoffs(db_session))[0].payload["roi_context"]
    assert roi["attribution_model"] == "influenced"
    assert roi["attribution_window_days"] == 30


async def test_the_cost_share_is_blank_until_the_denominator_is_known(
    db_session: AsyncSession, graph_session: GraphSession, posted: Posted
):
    """How many leads the activation produced is not known while the doors are
    open, and a share against a partial count changes every time somebody else
    scans a badge."""
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)
    await run_chain()

    early = (await handoffs(db_session))[0].payload
    assert early["stage"] == "identified"
    assert early["roi_context"]["activation_cost_share"] is None

    await end_the_session(db_session)
    await run_chain()

    final = [h.payload for h in await handoffs(db_session) if h.payload["stage"] == "final"]
    assert len(final) == 1
    # 1000.0 activation cost, one lead.
    assert final[0]["roi_context"]["activation_cost_share"] == 1000.0


# ── the two-stage pairing ─────────────────────────────────────────────────────


async def test_both_stages_carry_one_dedupe_key_and_two_event_ids(
    db_session: AsyncSession, graph_session: GraphSession, posted: Posted
):
    """The whole trick, asserted directly.

    Same dedupe_key, so a destination's upsert updates the lead rather than
    adding a second. Different event_ids, so the bus does not swallow the final
    handoff as a duplicate of the early one — which would lose the complete path
    with nothing failing anywhere.
    """
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)
    await run_chain()
    await end_the_session(db_session)
    await run_chain()

    leads = await handoffs(db_session)
    assert sorted(lead.payload["stage"] for lead in leads) == ["final", "identified"]
    assert len({lead.payload["dedupe_key"] for lead in leads}) == 1
    assert len({lead.event_id for lead in leads}) == 2


async def test_the_dedupe_key_is_the_email_where_there_is_one(
    db_session: AsyncSession, graph_session: GraphSession, posted: Posted
):
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)

    await run_chain()

    assert (await handoffs(db_session))[0].payload["dedupe_key"] == (
        f"{T}:sam@example.com"
    )


async def test_replaying_the_chain_produces_no_extra_leads(
    db_session: AsyncSession, graph_session: GraphSession, posted: Posted
):
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)
    await end_the_session(db_session)
    await run_chain()

    before = len(await handoffs(db_session))
    for consumer in ("identity", "attribution", "handoff_delivery"):
        await repository.reset_cursor(
            db_session, consumer=consumer, tenant_id=T, to_seq=0
        )
    await db_session.commit()
    await run_chain()

    assert len(await handoffs(db_session)) == before


# ── withdrawal ────────────────────────────────────────────────────────────────


async def test_a_withdrawn_visitor_produces_no_further_lead(
    db_session: AsyncSession, graph_session: GraphSession, posted: Posted
):
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)
    await run_chain()

    await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(),
            tenant_id=T,
            session_id=S,
            type="consent.withdrawn",
            payload={
                "consent_id": "c_0001",
                "contact_id": None,
                "anon_id": "P-012",
                "reason": "visitor_request",
                "withdrawn_at": (BASE + dt.timedelta(minutes=10)).isoformat(),
            },
            occurred_at=BASE + dt.timedelta(minutes=10),
        ),
    )
    await db_session.commit()
    await end_the_session(db_session)

    await run_chain()

    # The early handoff stays on the log — it happened, and crm.retract is what
    # undoes what it pushed. What must not appear is a *new* one.
    assert [h.payload["stage"] for h in await handoffs(db_session)] == ["identified"]


async def test_replaying_an_identification_after_a_withdrawal_builds_nothing(
    db_session: AsyncSession, graph_session: GraphSession, posted: Posted
):
    """The append-only counterpart of the identity consumer's own replay test.
    The identity.resolved that started this is on the log forever."""
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)
    await run_chain()

    await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(),
            tenant_id=T,
            session_id=S,
            type="consent.withdrawn",
            payload={
                "consent_id": "c_0001",
                "contact_id": None,
                "anon_id": "P-012",
                "reason": "visitor_request",
                "withdrawn_at": (BASE + dt.timedelta(minutes=10)).isoformat(),
            },
            occurred_at=BASE + dt.timedelta(minutes=10),
        ),
    )
    await db_session.commit()
    await run_chain()

    before = len(await handoffs(db_session))
    await repository.reset_cursor(
        db_session, consumer="attribution", tenant_id=T, to_seq=0
    )
    await db_session.commit()
    await AttributionConsumer().run_once()

    assert len(await handoffs(db_session)) == before
    assert await repository.list_dead_letters(db_session, consumer="attribution") == []


# ── delivery ──────────────────────────────────────────────────────────────────


async def test_the_lead_is_posted_signed(
    db_session: AsyncSession, graph_session: GraphSession, posted: Posted
):
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)

    await run_chain()

    assert len(posted.calls) == 1
    call = posted.calls[0]
    assert call["url"] == HANDOFF_URL
    # Signed over the exact bytes sent, verified the way a receiver would.
    assert call["headers"]["X-Realmspace-Signature"] == sign(SECRET, call["content"])
    assert json.loads(call["content"])["schema"] == "realmspace.lead_handoff/v1"
    # The receiver's upsert key travels in a header too, so a destination that
    # never parses the body can still dedupe.
    assert call["headers"]["X-Realmspace-Dedupe-Key"] == f"{T}:sam@example.com"


async def test_a_replayed_handoff_is_posted_once(
    db_session: AsyncSession, graph_session: GraphSession, posted: Posted
):
    """The claim, not the receiver, is what guarantees this."""
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)
    await run_chain()

    await repository.reset_cursor(
        db_session, consumer="handoff_delivery", tenant_id=T, to_seq=0
    )
    await db_session.commit()
    await HandoffDeliveryConsumer().run_once()

    assert len(posted.calls) == 1


async def test_a_deployment_with_no_destination_still_builds_leads(
    db_session: AsyncSession, graph_session: GraphSession, monkeypatch, posted: Posted
):
    """Nothing configured is not a failure. The handoffs are on the log, and a
    destination added next week reads them from seq 0 — parking them in `/ops`
    instead would fill the queue with leads nobody asked to deliver."""
    monkeypatch.setattr(get_settings(), "handoff_webhook_url", None)
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)

    await run_chain()

    assert len(await handoffs(db_session)) == 1
    assert posted.calls == []
    assert await repository.list_dead_letters(db_session, consumer="handoff_delivery") == []


async def test_a_failing_receiver_lands_in_the_hitl_queue_as_a_handoff(
    db_session: AsyncSession, graph_session: GraphSession, monkeypatch
):
    monkeypatch.setattr(httpx.AsyncClient, "post", Posted(status=503))
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)

    await run_chain()

    parked = await repository.list_dead_letters(db_session, consumer="handoff_delivery")
    assert len(parked) == 1
    assert "503" in parked[0].error


async def test_a_stranded_lead_is_labelled_apart_from_a_stuck_slack_post(
    db_session: AsyncSession, graph_session: GraphSession, posted: Posted
):
    """Migration 0006's whole purpose: an operator on `/ops` seeing a row whose
    rule_id column holds a session id needs to be told which kind it is."""
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)
    await run_chain()

    row = await repository.get_dispatch(
        db_session,
        tenant_id=T,
        fired_event_id=(await handoffs(db_session))[0].event_id,
        action_type="handoff_webhook",
    )
    assert row.kind == "handoff"
    assert row.rule_id == S


async def test_delivering_a_lead_meters_an_action(
    db_session: AsyncSession, graph_session: GraphSession, posted: Posted
):
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)

    await run_chain()

    costs = await repository.read_events(
        db_session, tenant_id=T, session_id=S, type="cost.metered", limit=10
    )
    assert len(costs) == 1
    assert costs[0].payload["kind"] == "action_unit"
