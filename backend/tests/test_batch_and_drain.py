"""
Batch ingest and the drain hook, as executable claims.

Both ported from the `postgres-track`, which had them and we did not. The two
tests that matter are the ones about the *differences*: a batch is all-or-nothing
so a mixed-tenant one cannot half-apply, and drain runs the **live** consumer
rather than a fresh instance, because a fresh tracker would advance the real
cursor while emitting whatever an empty state implies.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.auth.models import AuthUser
from app.auth.tokens import issue_token
from app.consumers import run as consumer_run
from app.consumers.base import Consumer
from app.db import get_session
from app.main import app
from app.models import EventLog
from tests.conftest import as_tenant

T = "t_test"
OTHER = "t_test_other"
S = "s_batch"
BASE = dt.datetime(2026, 8, 4, 12, 0, 0, tzinfo=dt.timezone.utc)


@pytest.fixture(autouse=True)
async def _scope(db_session: AsyncSession) -> AsyncIterator[None]:
    await as_tenant(db_session, T)
    yield
    consumer_run.clear_running()
    app.dependency_overrides.clear()


async def _client(db_session: AsyncSession, role: str = "operator", tenant: str = T):
    user_id = f"u_{role}_{tenant}"
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

    async def override() -> AsyncIterator[AsyncSession]:
        yield db_session
        await db_session.commit()

    app.dependency_overrides[get_session] = override
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={
            "Authorization": f"Bearer {issue_token(subject=user_id, tenant_id=tenant, role=role)}"
        },
    )


def event(tenant_id: str = T, type: str = "spatial.zone_enter") -> dict:
    return {
        "eventId": str(uuid.uuid4()),
        "tenantId": tenant_id,
        "sessionId": S,
        "type": type,
        "payload": {"anon_id": "P-001", "zone_id": "z_a"},
        "occurredAt": BASE.isoformat(),
    }


# ── batch ingest ──────────────────────────────────────────────────────────────


async def test_a_batch_lands_in_order(db_session: AsyncSession) -> None:
    """One round trip for many events, results in the order they were sent.

    The producers that need this are the ones a per-event POST punishes: an RFID
    bridge reading a bank of antennas, or the camera client flushing its buffer
    after the wifi came back.
    """
    body = {"events": [event(), event(), event()]}
    async with await _client(db_session) as ac:
        res = await ac.post("/events/batch", json=body)

    assert res.status_code == 200, res.text
    out = res.json()
    assert [e["eventId"] for e in out] == [e["eventId"] for e in body["events"]]
    assert [e["seq"] for e in out] == sorted(e["seq"] for e in out)


async def test_a_batch_is_idempotent_like_a_single_post(
    db_session: AsyncSession
) -> None:
    """Resending after a timeout must not double the log.

    This is what makes the all-or-nothing rule affordable: a rejected batch can
    simply be corrected and resent, because the events that had already landed
    are no-ops the second time.
    """
    body = {"events": [event(), event()]}
    async with await _client(db_session) as ac:
        first = (await ac.post("/events/batch", json=body)).json()
        second = (await ac.post("/events/batch", json=body)).json()

    assert [e["seq"] for e in first] == [e["seq"] for e in second]
    rows = await repository.read_events(db_session, tenant_id=T, since_seq=0, limit=100)
    assert len(rows) == 2


async def test_a_mixed_tenant_batch_writes_nothing_at_all(
    db_session: AsyncSession
) -> None:
    """All or nothing. A batch that half-applied would be the worst outcome.

    A producer told "409 of your 500 landed" has no reasonable next move. One
    told "none of them did" can fix the batch and resend, and idempotency makes
    the resend free. The check runs before any write for exactly this reason.
    """
    body = {"events": [event(), event(tenant_id=OTHER), event()]}
    async with await _client(db_session) as ac:
        res = await ac.post("/events/batch", json=body)

    assert res.status_code == 403
    assert await repository.read_events(db_session, tenant_id=T, since_seq=0, limit=10) == []


async def test_an_oversized_batch_is_refused(db_session: AsyncSession) -> None:
    """Uncapped, one request could hold a transaction open indefinitely — and the
    producers that most want batching are the ones that would send ten thousand."""
    async with await _client(db_session) as ac:
        res = await ac.post("/events/batch", json={"events": [event() for _ in range(501)]})
    assert res.status_code == 422


async def test_an_empty_batch_is_refused(db_session: AsyncSession) -> None:
    async with await _client(db_session) as ac:
        assert (await ac.post("/events/batch", json={"events": []})).status_code == 422


# ── drain ─────────────────────────────────────────────────────────────────────


class Counting(Consumer):
    name = "graph_writer"  # a real registered name, so the 404 guard passes
    handles = ()

    def __init__(self) -> None:
        super().__init__()
        self.seen: list[int] = []

    async def handle(self, event: EventLog) -> None:
        self.seen.append(event.seq)


async def test_drain_processes_the_backlog_now(db_session: AsyncSession) -> None:
    """The companion to the review queue: fix the cause, then make it catch up."""
    async with await _client(db_session) as ac:
        await ac.post("/events/batch", json={"events": [event(), event()]})

        live = Counting()
        consumer_run.register_running(live)
        res = await ac.post("/v1/consumers/graph_writer/drain")

    assert res.status_code == 200, res.text
    assert res.json()["processed"] == 2
    assert len(live.seen) == 2


async def test_drain_uses_the_running_instance_not_a_fresh_one(
    db_session: AsyncSession
) -> None:
    """The difference that matters against the other track's version.

    The tracker's output depends on state built from the stream. Draining a
    newly-constructed instance would advance the *real* cursor while emitting
    whatever an empty state implies — a quieter version of the bug the HITL
    retry rules exist to prevent. So the route must reach the instance that is
    actually running, which this proves by asserting the events landed on the
    object the test registered.
    """
    async with await _client(db_session) as ac:
        await ac.post("/events/batch", json={"events": [event()]})

        live = Counting()
        consumer_run.register_running(live)
        await ac.post("/v1/consumers/graph_writer/drain")

    assert live.seen, "drain built its own consumer instead of using the live one"


async def test_drain_says_so_when_consumers_are_not_running_here(
    db_session: AsyncSession
) -> None:
    """None is a real answer — consumers are off under test, and a deployment
    could run the API and the consumers as separate processes. Substituting a
    fresh instance would be worse than saying nothing is here."""
    consumer_run.clear_running()
    async with await _client(db_session) as ac:
        res = await ac.post("/v1/consumers/graph_writer/drain")

    assert res.status_code == 503
    assert "not running in this process" in res.text


async def test_draining_an_unknown_consumer_is_a_404(db_session: AsyncSession) -> None:
    async with await _client(db_session) as ac:
        assert (await ac.post("/v1/consumers/nonsense/drain")).status_code == 404


async def test_a_viewer_cannot_drain(db_session: AsyncSession) -> None:
    """Drain runs handlers with real side effects."""
    consumer_run.register_running(Counting())
    async with await _client(db_session, role="viewer") as ac:
        assert (await ac.post("/v1/consumers/graph_writer/drain")).status_code == 403


async def test_drain_only_touches_your_own_tenant(db_session: AsyncSession) -> None:
    """Theirs takes tenantId as a query parameter. Here it comes from the
    credential, so there is no parameter to point at somebody else's backlog."""
    async with await _client(db_session) as ac:
        await ac.post("/events/batch", json={"events": [event(), event()]})

    live = Counting()
    consumer_run.register_running(live)

    async with await _client(db_session, tenant=OTHER) as other:
        res = await other.post("/v1/consumers/graph_writer/drain")

    assert res.status_code == 200
    assert res.json()["tenantId"] == OTHER
    assert res.json()["processed"] == 0  # the other tenant has no backlog
    assert live.seen == []
