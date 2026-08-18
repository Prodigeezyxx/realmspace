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
from app.models import CrmLink
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
