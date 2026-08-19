"""
The rest of bring-your-own: the automation hooks, and the pull API.

`integrations.md` §5 lists five connectors. The signed webhook shipped with the
first half of Phase 4; these are the other three.

**Zapier and Make** are destinations in the same registry the CRMs are in, and
that is the claim worth testing: one lead reaches a hook and a CRM independently,
each claimed and retried on its own, with no second delivery path to keep in
step. The body is signed with the same function the rule action uses, over the
exact bytes that go on the wire — a signature computed over a different
serialisation than the one sent is the bug that only shows up at the receiver.

**The pull API** is what a client behind a firewall actually wants: no inbound
port, no endpoint to keep up. The properties are the cursor (no gap, no
duplicate) and the redaction (a withdrawn lead does not go out again with their
name on it).
"""

from __future__ import annotations

import hashlib
import hmac
import json
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
from app.crm import zapier
from app.crm.base import AdapterError
from app.graph import repository as graph_repo
from app.models import CrmLink, RuleDispatch
from app.schemas import EventIn
from tests.crm_transport import stub_transport
from tests.test_handoff import (
    BASE,
    S,
    T,
    consent,
    end_the_session,
    seed_activation,
    seed_person_with_a_path,
)

HOOK = "https://hooks.zapier.com/hooks/catch/1234/abcdef/"
SIGNING_SECRET = "hook-signing-secret"
EMAIL = "sam@example.com"


@pytest.fixture(autouse=True)
async def _scope_to_test_tenant(db_session: AsyncSession):
    from tests.conftest import as_tenant

    await as_tenant(db_session, T)
    yield


@pytest.fixture(autouse=True)
def _signing_secret(monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "webhook_signing_secret", SIGNING_SECRET)
    yield


class FakeHook:
    """A Zap catch hook. Records what arrived, including the raw bytes."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status = 200

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status >= 400:
            return httpx.Response(self.status, json={"message": "no"})
        return httpx.Response(200, json={"status": "success"})

    @property
    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests]


@pytest.fixture
def hook(monkeypatch) -> FakeHook:
    fake = FakeHook()
    stub_transport(monkeypatch, fake.handle)
    return fake


@pytest.fixture
async def connected(db_session: AsyncSession, encryption_key: str) -> None:
    await repository.upsert_integration(
        db_session,
        tenant_id=T,
        provider="zapier",
        secret_ct=secrets.encrypt(HOOK, tenant_id=T, provider="zapier"),
        secret_hint="def/",
        field_map={},
    )
    await db_session.commit()


@pytest.fixture
async def hubspot_only(db_session: AsyncSession, encryption_key: str) -> None:
    """A tenant with a CRM and no hook — the other half of the capability test."""
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
    await IdentityConsumer().run_once()
    await ReAnonymiseConsumer().run_once()
    await AttributionConsumer().run_once()
    await CrmDeliveryConsumer().run_once()
    await CrmRetractConsumer().run_once()


def adapter(secret: str = HOOK):
    return zapier.ZapierAdapter.for_tenant(secret=secret, field_map={})


# ── the credential ────────────────────────────────────────────────────────────


def test_a_plain_http_hook_is_refused() -> None:
    """A lead with somebody's name in it does not go out over plain http."""
    with pytest.raises(AdapterError, match="https"):
        adapter("http://hooks.zapier.com/hooks/catch/1/a/")


def test_a_bare_url_and_a_document_are_both_accepted() -> None:
    """The bare form is what Zapier's UI gives an admin to paste; the document
    exists for a tenant who wants their own signing key."""
    assert adapter(HOOK)._credential["url"] == HOOK

    with_key = adapter(json.dumps({"url": HOOK, "signing_secret": "theirs"}))
    assert with_key._credential["signing_secret"] == "theirs"


async def test_it_refuses_to_post_a_lead_unsigned(monkeypatch, hook: FakeHook) -> None:
    """The receiver cannot tell an unsigned delivery from a forged one, and
    cannot see the config that downgraded it."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "webhook_signing_secret", "")
    with pytest.raises(AdapterError, match="unsigned"):
        await adapter().upsert({"contact": {"id": "ct_1"}, "dedupe_key": "t:x"})
    assert hook.requests == []


# ── the signature ─────────────────────────────────────────────────────────────


async def test_the_signature_covers_the_bytes_that_were_actually_sent(
    hook: FakeHook,
) -> None:
    """A signature computed over a different serialisation than the one sent is
    the bug that only shows up at the receiver."""
    handoff = {
        "contact": {"id": "ct_1", "email": EMAIL},
        "dedupe_key": f"{T}:{EMAIL}",
        "spatial_intent": {"zones_visited": ["Entry"]},
    }
    await adapter().upsert(handoff)

    request = hook.requests[0]
    expected = hmac.new(
        SIGNING_SECRET.encode(), request.content, hashlib.sha256
    ).hexdigest()
    assert request.headers["X-Realmspace-Signature"].endswith(expected)
    assert request.headers["X-Realmspace-Dedupe-Key"] == f"{T}:{EMAIL}"


async def test_the_external_id_is_our_contact_id_and_not_the_dedupe_key(
    hook: FakeHook,
) -> None:
    """`crm_link.external_id` is deliberately never redacted, so a dedupe key
    there would leave a live email in our database after an erasure had removed
    every other copy."""
    external_id = await adapter().upsert(
        {"contact": {"id": "ct_1", "email": EMAIL}, "dedupe_key": f"{T}:{EMAIL}"}
    )
    assert external_id == "ct_1"
    assert EMAIL not in external_id


async def test_a_retraction_says_what_it_can_and_cannot_promise(
    hook: FakeHook,
) -> None:
    """We do not know what the client's Zap wrote to, and saying otherwise would
    be claiming an erasure we cannot perform."""
    detail = await adapter().retract("ct_1")

    assert hook.bodies[0] == {
        "action": "retract",
        "contact_id": "ct_1",
        "reason": "consent_withdrawn",
    }
    assert "not a confirmed erasure downstream" in detail


# ── as a destination among destinations ───────────────────────────────────────


async def test_a_lead_reaches_the_hook_and_a_withdrawal_retracts_it(
    connected: None,
    hook: FakeHook,
    db_session: AsyncSession,
    graph_session: GraphSession,
) -> None:
    """The whole reason it is in the registry: the claim, the retry and the row
    on `/ops` come for free, and nothing about the delivery path is new."""
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)
    await run_chain()

    delivered = hook.bodies[0]
    assert delivered["contact"]["email"] == EMAIL
    assert delivered["spatial_intent"]["zones_visited"] == ["Entry", "Pod"]

    link = (
        await db_session.execute(select(CrmLink).where(CrmLink.provider == "zapier"))
    ).scalar_one()
    assert link.external_id == delivered["contact"]["id"]

    await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(),
            tenant_id=T,
            session_id=S,
            type="consent.withdrawn",
            payload={"consent_id": "c_0001", "anon_id": "P-012"},
            occurred_at=BASE,
        ),
    )
    await db_session.commit()
    await run_chain()

    assert hook.bodies[-1]["action"] == "retract"
    await db_session.refresh(link)
    assert link.retracted_at is not None
    assert EMAIL not in link.dedupe_key


# ── the pull API ──────────────────────────────────────────────────────────────


async def _client(db_session: AsyncSession, role: str = "reader") -> httpx.AsyncClient:
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


async def seed_two_leads(db_session: AsyncSession, graph_session: GraphSession) -> None:
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)
    await end_the_session(db_session)
    await IdentityConsumer().run_once()
    await AttributionConsumer().run_once()


async def test_the_cursor_pages_with_no_gap_and_no_duplicate(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """Seq, not a page number — a page number shifts under a client the moment a
    new handoff lands."""
    await seed_two_leads(db_session, graph_session)
    client = await _client(db_session)

    async with client:
        first = (await client.get("/v1/handoffs", params={"limit": 1})).json()
        assert first["count"] == 1
        assert first["more"] is True

        second = (
            await client.get(
                "/v1/handoffs", params={"limit": 1, "since": first["nextSince"]}
            )
        ).json()
        assert second["count"] == 1
        assert second["more"] is True

        exhausted = (
            await client.get("/v1/handoffs", params={"since": second["nextSince"]})
        ).json()

    seqs = [first["handoffs"][0]["seq"], second["handoffs"][0]["seq"]]
    assert seqs == sorted(set(seqs))
    assert exhausted["count"] == 0
    # An empty poll is a no-op, not a rewind.
    assert exhausted["nextSince"] == second["nextSince"]


async def test_a_withdrawn_lead_does_not_go_out_again_with_their_name(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """A withdrawal does not rewrite the log — only an erasure does — so the row
    is still there with the email in it, and this is what stops it leaving."""
    await seed_two_leads(db_session, graph_session)
    await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(),
            tenant_id=T,
            session_id=S,
            type="consent.withdrawn",
            payload={"consent_id": "c_0001", "anon_id": "P-012"},
            occurred_at=BASE,
        ),
    )
    await db_session.commit()

    client = await _client(db_session)
    async with client:
        body = (await client.get("/v1/handoffs")).json()

    assert body["count"] >= 1
    for entry in body["handoffs"]:
        assert entry["withdrawn"] is True
        assert EMAIL not in json.dumps(entry)
        # The touch, its timestamps and its consent basis survive.
        assert entry["handoff"]["consent"]["tier"] == "T2"
        assert entry["handoff"]["spatial_intent"]["zones_visited"] == ["Entry", "Pod"]


async def test_the_csv_is_one_row_per_handoff(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """Both stages of one visitor are separate rows: this is the log, and folding
    them into one lead is what the ledger is for."""
    await seed_two_leads(db_session, graph_session)
    client = await _client(db_session)
    async with client:
        response = await client.get("/v1/handoffs", params={"format": "csv"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")

    lines = [line for line in response.text.splitlines() if line.strip()]
    assert lines[0].startswith("seq,occurred_at,session_id,stage")
    assert len(lines) == 3  # header + identified + final
    assert EMAIL in response.text
    assert "Entry > Pod" in response.text


async def test_an_anonymous_handoff_reaches_the_hook_and_no_crm(
    connected: None,
    hook: FakeHook,
    db_session: AsyncSession,
    graph_session: GraphSession,
) -> None:
    """The point of the `anonymousHandoffs` flag, from the client's side.

    A review found that the first version delivered these to nothing a tenant had
    configured — `crm_delivery` returned before claiming for any handoff with no
    contact — so an operator who turned the flag on and connected a Zap hook got
    silence and no `/ops` row saying why. Destinations declare whether they take
    one instead: a hook does, a CRM does not, and a CRM's abstention is not a
    limitation but the absence of anything to create.
    """
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

    delivered = [b for b in hook.bodies if b.get("action") != "healthcheck"]
    assert len(delivered) == 1
    assert delivered[0]["anon_id"] == "P-777"
    assert "contact" not in delivered[0]
    assert delivered[0]["spatial_intent"]["zones_visited"] == ["Entry", "Pod"]

    # Claimed like any other delivery, so a hook that is down strands it on /ops.
    claims = (
        await db_session.execute(
            select(RuleDispatch).where(RuleDispatch.action_type == "crm:zapier")
        )
    ).scalars().all()
    assert [c.status for c in claims] == ["delivered"]

    # And no `crm_link`: there is no contact, so there is nothing a retraction
    # could ever name.
    links = (
        await db_session.execute(select(CrmLink).where(CrmLink.provider == "zapier"))
    ).scalars().all()
    assert links == []


async def test_a_crm_is_offered_no_anonymous_handoff_at_all(
    hubspot_only: None,
    db_session: AsyncSession,
    graph_session: GraphSession,
) -> None:
    """A tenant with only a CRM connected writes no dispatch rows for them.

    Several hundred rows a day recording that nothing was sent about somebody no
    CRM was going to hear about is the noise the capability check exists to keep
    off `/ops`.
    """
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

    claims = (
        await db_session.execute(select(RuleDispatch).where(RuleDispatch.kind == "handoff"))
    ).scalars().all()
    assert claims == []


async def test_a_withdrawal_past_the_first_page_still_redacts(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """`read_events` caps at MAX_LIMIT and returns the *oldest* rows from the
    cursor, so a single read hands back the first thousand withdrawals a tenant
    ever recorded and drops the rest.

    The dropped ones are the recent ones — the people most likely to still be in
    the page being exported. Reading one page would send their name to a client's
    own systems, which is the one thing this endpoint must not do.

    A thousand withdrawals is slow to seed honestly, so the page size is lowered
    for the test rather than the volume raised: what is being proved is that the
    read does not stop at a page boundary.
    """
    from app import repository as repo
    from app.routers import handoffs as handoffs_router

    await seed_two_leads(db_session, graph_session)

    # Two pages' worth at the reduced size, with the one that matters last.
    for index in range(3):
        await repository.append_event(
            db_session,
            EventIn(
                event_id=uuid.uuid4(),
                tenant_id=T,
                session_id=S,
                type="consent.withdrawn",
                payload={"consent_id": f"c_noise_{index}", "reason": "visitor_request"},
                occurred_at=BASE,
            ),
        )
    await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(),
            tenant_id=T,
            session_id=S,
            type="consent.withdrawn",
            payload={"anon_id": "P-012", "reason": "visitor_request"},
            occurred_at=BASE,
        ),
    )
    await db_session.commit()

    original = repo.MAX_LIMIT
    try:
        handoffs_router.repository.MAX_LIMIT = 2
        client = await _client(db_session)
        async with client:
            body = (await client.get("/v1/handoffs")).json()
    finally:
        handoffs_router.repository.MAX_LIMIT = original

    assert body["count"] >= 1
    for entry in body["handoffs"]:
        assert entry["withdrawn"] is True
        assert EMAIL not in json.dumps(entry)


async def test_an_anonymous_handoff_needs_no_consent_tier(
    connected: None,
    hook: FakeHook,
    db_session: AsyncSession,
    graph_session: GraphSession,
) -> None:
    """The T2 gate applies to handoffs that name somebody, and only those.

    An anonymous handoff carries no `consent` block, so a tier check reads it as
    "no consent" and refuses — which looks right and is exactly wrong. Consent is
    what permits acting on a *person*; a contactless row of zone dwells names
    nobody, and `privacy.md` has that path running with no consent from the
    beginning. Gating it would make an operator's aggregate reach depend on
    permission from the people it deliberately does not identify.
    """
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

    delivered = [b for b in hook.bodies if b.get("action") != "healthcheck"]
    assert [b["anon_id"] for b in delivered] == ["P-777"]
    assert "consent" not in delivered[0]
