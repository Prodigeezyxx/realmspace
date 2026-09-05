"""
A report a client can open with no account — and what a leaked URL is worth.

This is the only unauthenticated read path into tenant data in the application,
so most of this file is about what it *cannot* do. The positives exist to show
the refusals are not just a feature that never works.

The four properties worth stating plainly:

  - a token opens exactly one activation, and there is no parameter that could
    make it open another;
  - contact details are stripped by **classification** (`erasure.PII_TYPES`),
    not by a list of field names somebody has to keep current;
  - unknown, expired and revoked are one answer, so a stranger holding a guess
    learns nothing from which error they got;
  - the plaintext token exists for one response and is nowhere in the database.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from neo4j import AsyncSession as GraphSession
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import erasure, repository, share
from app.auth.models import AuthUser
from app.auth.tokens import issue_token
from app.db import get_session
from app.graph import repository as graph_repo
from app.main import app
from app.models import ReportShare
from app.schemas import EventIn
from tests.conftest import as_tenant

T = "t_test"
OTHER = "t_other"
S = "s_share"
S2 = "s_share_other"

BASE = dt.datetime(2026, 8, 29, 10, 0, 0, tzinfo=dt.timezone.utc)
ZONE = [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]


@pytest.fixture(autouse=True)
async def _scope_to_test_tenant(db_session: AsyncSession) -> AsyncIterator[None]:
    await as_tenant(db_session, T)
    yield


def _make_client(db_session: AsyncSession, token: str | None) -> AsyncClient:
    async def override_get_session() -> AsyncIterator[AsyncSession]:
        yield db_session
        await db_session.commit()

    app.dependency_overrides[get_session] = override_get_session
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=headers
    )


async def _user(db_session: AsyncSession, user_id: str, role: str, tenant: str) -> str:
    db_session.add(
        AuthUser(
            user_id=user_id,
            email=f"{user_id}@floats.demo",
            display_name=user_id,
            tenant_id=tenant,
            role=role,
        )
    )
    await db_session.commit()
    return issue_token(subject=user_id, tenant_id=tenant, role=role)


@pytest.fixture
async def operator(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    token = await _user(db_session, "u_op", "operator", T)
    async with _make_client(db_session, token) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def viewer(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    token = await _user(db_session, "u_view", "viewer", T)
    async with _make_client(db_session, token) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def outsider(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """An operator in a different organisation. Exists only to be unable to."""
    token = await _user(db_session, "u_other", "operator", OTHER)
    async with _make_client(db_session, token) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def anonymous(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """No credential at all — which is the whole point of a share link."""
    async with _make_client(db_session, None) as ac:
        yield ac
    app.dependency_overrides.clear()


async def seed_session(graph_session: GraphSession, session_id: str = S) -> None:
    await graph_repo.upsert_session(
        graph_session, tenant_id=T, session_id=session_id, venue="Share Hall"
    )
    await graph_repo.upsert_zone(
        graph_session, tenant_id=T, session_id=session_id,
        zone_id="z_entry", name="Entry", type="entry", polygon=ZONE,
    )


async def emit(
    db_session: AsyncSession, *, type: str, payload: dict, session_id: str = S
) -> None:
    await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(), tenant_id=T, session_id=session_id,
            type=type, payload=payload, occurred_at=BASE,
        ),
    )
    await db_session.commit()


async def mint(operator: AsyncClient, session_id: str = S, **body) -> str:
    res = await operator.post(f"/v1/sessions/{session_id}/share", json=body)
    assert res.status_code == 201, res.text
    return res.json()["token"]


# ── the link works ───────────────────────────────────────────────────────────


async def test_a_client_with_no_account_can_read_the_report(
    operator: AsyncClient, anonymous: AsyncClient, graph_session: GraphSession,
    db_session: AsyncSession,
):
    await seed_session(graph_session)
    await emit(db_session, type="spatial.zone_enter",
               payload={"anon_id": "P-1", "zone_id": "z_entry"})
    token = await mint(operator)

    config = await anonymous.get(f"/v1/share/{token}")
    events = await anonymous.get(f"/v1/share/{token}/events")
    graph = await anonymous.get(f"/v1/share/{token}/graph")

    assert config.status_code == 200
    assert config.json()["sessionId"] == S
    assert events.status_code == 200
    assert [e["type"] for e in events.json()] == ["spatial.zone_enter"]
    assert graph.status_code == 200


async def test_opening_it_is_counted_once(
    operator: AsyncClient, anonymous: AsyncClient, graph_session: GraphSession
):
    """So an operator can answer "has the client looked at it yet".

    Counted on the config read, which the page makes exactly once — the events
    endpoint is paged and would count one opening several times.
    """
    await seed_session(graph_session)
    token = await mint(operator)

    await anonymous.get(f"/v1/share/{token}")
    await anonymous.get(f"/v1/share/{token}/events")
    await anonymous.get(f"/v1/share/{token}/events")

    listed = (await operator.get(f"/v1/sessions/{S}/shares")).json()
    assert listed[0]["viewCount"] == 1


# ── what a leaked URL is worth ───────────────────────────────────────────────


async def test_contact_details_are_stripped_from_every_pii_type(
    operator: AsyncClient, anonymous: AsyncClient, graph_session: GraphSession,
    db_session: AsyncSession,
):
    """Asserted over `erasure.PII_TYPES`, not over a list of field names.

    That is the difference between a filter that stays correct and one that
    rots: a payload growing a new contact field is covered by the vocabulary the
    erasure job already maintains, and this test fails the day a *type* is added
    to the classification without the share path being considered.
    """
    await seed_session(graph_session)
    named = {"email": "sam@client.test", "name": "Sam", "company": "Client Co",
             "title": "Head of Brand"}
    for event_type in erasure.PII_TYPES:
        await emit(db_session, type=event_type,
                   payload={"anon_id": "P-1", "contact_id": "c_1", "contact": dict(named)})

    token = await mint(operator)
    body = (await anonymous.get(f"/v1/share/{token}/events")).json()

    seen = {e["type"] for e in body}
    assert seen == set(erasure.PII_TYPES), "every PII type should still be present"

    for event in body:
        contact = (event["payload"] or {}).get("contact") or {}
        for field in erasure.CONTACT_PII:
            assert field not in contact, f"{field} leaked on {event['type']}"

    # The whole response, as a string, must not contain any of it.
    raw = (await anonymous.get(f"/v1/share/{token}/events")).text
    for value in named.values():
        assert value not in raw


async def test_the_events_survive_so_the_counts_do(
    operator: AsyncClient, anonymous: AsyncClient, graph_session: GraphSession,
    db_session: AsyncSession,
):
    """Redacted, not removed.

    The scorecard counts `consent.captured` for `leadsCaptured`. Dropping the
    events would make the client's copy of the report disagree with the
    operator's, silently — which is worse than either number alone.
    """
    await seed_session(graph_session)
    for i in range(3):
        await emit(db_session, type="consent.captured",
                   payload={"anon_id": f"P-{i}", "contact_id": f"c_{i}", "tier": "T2",
                            "contact": {"email": f"p{i}@client.test", "name": "Sam"}})
    token = await mint(operator)

    body = (await anonymous.get(f"/v1/share/{token}/events")).json()
    assert len([e for e in body if e["type"] == "consent.captured"]) == 3


async def test_a_token_cannot_reach_another_activation(
    operator: AsyncClient, anonymous: AsyncClient, graph_session: GraphSession,
    db_session: AsyncSession,
):
    """There is no `session_id` parameter to pass, which is the design.

    A share principal that went through `require_reader` would have reached
    `GET /events?session_id=…` and read anything. The session comes off the row.
    """
    await seed_session(graph_session)
    await seed_session(graph_session, S2)
    await emit(db_session, type="spatial.dwell",
               payload={"anon_id": "P-9", "zone_id": "z_entry", "duration": 5},
               session_id=S2)
    token = await mint(operator)

    body = (await anonymous.get(f"/v1/share/{token}/events")).json()
    assert [e["sessionId"] for e in body] == []

    # And the obvious attempt to widen it changes nothing.
    widened = await anonymous.get(f"/v1/share/{token}/events", params={"session_id": S2})
    assert [e["sessionId"] for e in widened.json()] == []


async def test_a_share_token_is_not_a_credential(
    anonymous: AsyncClient, operator: AsyncClient, graph_session: GraphSession
):
    """It opens three routes and authenticates nothing.

    Presented as a bearer token to an ordinary endpoint it is simply not a
    token, which is what "not a Principal" buys.
    """
    await seed_session(graph_session)
    token = await mint(operator)

    for path in ("/events", f"/v1/sessions/{S}", "/v1/handoffs", "/v1/dead-letters"):
        res = await anonymous.get(path, headers={"Authorization": f"Bearer {token}"})
        assert res.status_code in (401, 403), f"{path} admitted a share token"


# ── refusals ─────────────────────────────────────────────────────────────────


async def test_unknown_expired_and_revoked_are_one_answer(
    operator: AsyncClient, anonymous: AsyncClient, graph_session: GraphSession,
    db_session: AsyncSession,
):
    """Distinguishing them tells a stranger which guess was close."""
    await seed_session(graph_session)

    revoked = await mint(operator)
    listed = (await operator.get(f"/v1/sessions/{S}/shares")).json()
    await operator.delete(f"/v1/sessions/{S}/shares/{listed[0]['id']}")

    expired = await mint(operator)
    await db_session.execute(
        ReportShare.__table__.update()
        .where(ReportShare.token_sha256 == share.hash_token(expired))
        .values(expires_at=BASE - dt.timedelta(days=1))
    )
    await db_session.commit()

    bodies = set()
    for token in ("never-existed", revoked, expired):
        res = await anonymous.get(f"/v1/share/{token}")
        assert res.status_code == 404
        bodies.add(res.json()["detail"])

    assert len(bodies) == 1, "the three failures must be indistinguishable"


async def test_the_token_is_never_stored_or_returned(
    operator: AsyncClient, graph_session: GraphSession, db_session: AsyncSession
):
    """A database dump contains no working link, and no endpoint hands one back."""
    await seed_session(graph_session)
    token = await mint(operator)

    row = (
        await db_session.execute(select(ReportShare).where(ReportShare.session_id == S))
    ).scalar_one()
    assert row.token_sha256 == share.hash_token(token)
    assert token not in row.token_sha256
    assert row.hint == token[-4:]

    listed = (await operator.get(f"/v1/sessions/{S}/shares")).json()
    assert "token" not in listed[0]
    assert token not in str(listed)


async def test_another_organisation_can_neither_list_nor_revoke(
    operator: AsyncClient, outsider: AsyncClient, graph_session: GraphSession
):
    """`report_share` is outside RLS (migration 0013), so this is written out.

    That is exactly why it is asserted: a policy would have enforced it, and a
    hand-written `WHERE` is the kind that has to be tested.
    """
    await seed_session(graph_session)
    await mint(operator)
    listed = (await operator.get(f"/v1/sessions/{S}/shares")).json()

    assert (await outsider.get(f"/v1/sessions/{S}/shares")).json() == []
    stolen = await outsider.delete(f"/v1/sessions/{S}/shares/{listed[0]['id']}")
    assert stolen.status_code == 404


async def test_a_viewer_cannot_mint_one(viewer: AsyncClient, graph_session: GraphSession):
    """Sharing is part of running an activation — `multi-tenant.md` §3."""
    await seed_session(graph_session)
    res = await viewer.post(f"/v1/sessions/{S}/share", json={})
    assert res.status_code == 403


async def test_a_link_cannot_be_asked_to_last_forever(
    operator: AsyncClient, graph_session: GraphSession, db_session: AsyncSession
):
    """`expires_at` is NOT NULL and the request is bounded.

    An unauthenticated read path that never dies is the thing an expiry exists
    to prevent, so "forever" is not expressible.
    """
    await seed_session(graph_session)
    res = await operator.post(f"/v1/sessions/{S}/share", json={"expiresInDays": 100_000})
    assert res.status_code == 422
