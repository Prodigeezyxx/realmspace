"""
Dead letters, as executable claims.

The load-bearing one is `test_a_permanently_failing_event_does_not_stall_the_pipeline`
— the chaos test the acceptance criterion names. Everything else here is detail;
that one is the property the whole design exists for: **one poison event must not
take the activation down with it.** A consumer that retried forever would wedge
every event behind it, and on a booth floor that means the report silently stops
at the moment something went wrong.

Runs against real Postgres. The consumers used here are deliberate fakes, because
the point is to control *when* a handler fails, which no real consumer will do on
request.
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
from app.consumers.base import Consumer, retry_delay
from app.consumers.broadcast import BroadcastConsumer
from app.consumers.graph_writer import GraphWriterConsumer
from app.consumers.tracker import TrackerConsumer
from app.db import get_session
from app.main import app
from app.models import EventLog
from app.schemas import EventIn
from tests.conftest import as_tenant

T = "t_test"
OTHER = "t_test_other"
S = "s_dead"
BASE = dt.datetime(2026, 8, 4, 10, 0, 0, tzinfo=dt.timezone.utc)

FLAKY = "spatial.zone_enter"


@pytest.fixture(autouse=True)
async def _scope_to_test_tenant(db_session: AsyncSession) -> AsyncIterator[None]:
    await as_tenant(db_session, T)
    yield


async def append(
    db_session: AsyncSession, *, type: str = FLAKY, tenant_id: str = T, payload: dict | None = None
) -> EventLog:
    row, _ = await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(),
            tenant_id=tenant_id,
            session_id=S,
            type=type,
            payload=payload or {"anon_id": "P-001", "zone_id": "z_a"},
            occurred_at=BASE,
        ),
    )
    await db_session.commit()
    return row


class FailsNTimes(Consumer):
    """Fails its first `fail_for` events-handled, then works. A transient fault."""

    name = "test_flaky"
    handles = (FLAKY,)
    retryable = True

    def __init__(self, fail_for: int) -> None:
        super().__init__()
        self.fail_for = fail_for
        self.calls = 0

    async def handle(self, event: EventLog) -> None:
        self.calls += 1
        if self.calls <= self.fail_for:
            raise RuntimeError(f"deliberate failure {self.calls}")


class AlwaysFails(Consumer):
    name = "test_poison"
    handles = (FLAKY,)
    retryable = True

    def __init__(self) -> None:
        super().__init__()
        self.seen: list[int] = []

    async def handle(self, event: EventLog) -> None:
        self.seen.append(event.seq)
        raise RuntimeError("this one never works")


# ── 2.7: the retry mechanism ──────────────────────────────────────────────────


def test_backoff_doubles_and_then_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exponential, but bounded — and the bound is the part that matters.

    The tracker is on the real-time path with a <500ms budget from detection to
    dashboard. An unbounded doubling would hold a whole batch behind one slow
    failure, and it would do it exactly when the room is busiest, which is when
    failures are likeliest. Runs without sleeping: the delay is a pure function.
    """
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("CONSUMER_RETRY_DELAY_SECONDS", "1")
    monkeypatch.setenv("CONSUMER_RETRY_MAX_DELAY_SECONDS", "4")

    assert [retry_delay(n) for n in (1, 2, 3, 4, 5)] == [1, 2, 4, 4, 4]
    get_settings.cache_clear()


async def test_a_transient_failure_is_retried_and_never_parked(
    db_session: AsyncSession
) -> None:
    """Two failures then success: the event goes through, nothing reaches the queue.

    This is the case retrying exists for — a database blip, a graph write racing
    a restart. Parking it would put a human in the loop for something that fixed
    itself.
    """
    await append(db_session)

    consumer = FailsNTimes(fail_for=2)
    await consumer.run_once()

    assert consumer.calls == 3  # two failures, then the one that worked
    assert await repository.list_dead_letters(db_session, consumer=consumer.name) == []


async def test_a_permanently_failing_event_does_not_stall_the_pipeline(
    db_session: AsyncSession
) -> None:
    """The chaos test. One poison event must not take everything behind it down.

    Three events, the consumer fails on all of them. Each is parked and the
    cursor moves past it, so the *later* events are still reached. A consumer
    that retried forever would stop at the first one and the activation would
    quietly stop being measured from that moment on — with nothing in the data
    to say when.
    """
    first = await append(db_session)
    second = await append(db_session)
    third = await append(db_session)

    consumer = AlwaysFails()
    await consumer.run_once()

    # It reached all three rather than looping on the first. `seen` holds every
    # attempt, so each seq appears once per retry — what matters is that the
    # later ones appear at all.
    assert sorted(set(consumer.seen)) == [first.seq, second.seq, third.seq]

    parked = await repository.list_dead_letters(db_session, consumer=consumer.name)
    assert sorted(p.event_seq for p in parked) == [first.seq, second.seq, third.seq]

    # And the cursor is past them, so the next poll starts after the wreckage.
    cursor = await repository.get_cursor(db_session, consumer=consumer.name, tenant_id=T)
    assert cursor == third.seq


async def test_parking_the_same_event_twice_updates_one_row(
    db_session: AsyncSession
) -> None:
    """A queue that lists one failure three times is a queue nobody reads.

    An event can park more than once — a cursor rewind, a replay, a restart. The
    attempts accumulate on a single row and the newest traceback wins, because
    the newest is the one worth reading.
    """
    event = await append(db_session)
    consumer = AlwaysFails()

    await consumer.run_once()
    await repository.reset_cursor(db_session, consumer=consumer.name, tenant_id=T)
    await db_session.commit()
    await consumer.run_once()

    parked = await repository.list_dead_letters(db_session, consumer=consumer.name)
    assert len(parked) == 1
    assert parked[0].event_seq == event.seq
    # Two passes × max_attempts each.
    assert parked[0].attempts > 3


async def test_a_resolved_failure_is_not_silently_reopened(
    db_session: AsyncSession
) -> None:
    """A recurrence after a human dealt with it is new information.

    Reopening the old row would erase the fact that somebody had already looked
    at this and signed it off, which is exactly what an operator needs to know
    when it comes back.
    """
    await append(db_session)
    consumer = AlwaysFails()
    await consumer.run_once()

    parked = await repository.list_dead_letters(db_session, consumer=consumer.name)
    parked[0].resolved_at = dt.datetime.now(dt.timezone.utc)
    await db_session.commit()

    await repository.reset_cursor(db_session, consumer=consumer.name, tenant_id=T)
    await db_session.commit()
    await consumer.run_once()

    everything = await repository.list_dead_letters(
        db_session, consumer=consumer.name, unresolved_only=False
    )
    assert len(everything) == 2  # the closed one, plus a fresh entry


# ── 2.10: the review queue ────────────────────────────────────────────────────


def test_only_stateless_consumers_offer_retry() -> None:
    """The declaration lives on each consumer, with its reason.

    Idempotency is not the question — every consumer here is idempotent, which
    is what makes replay safe. This is narrower: whether one event, out of its
    sequence, still means the same thing.
    """
    assert GraphWriterConsumer.retryable is True
    # Carries per-person position state built from earlier events.
    assert TrackerConsumer.retryable is False
    # Stateless, but its output is a push to whoever is connected *now*.
    assert BroadcastConsumer.retryable is False


async def _client(db_session: AsyncSession, role: str, tenant: str = T) -> AsyncClient:
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


async def _park_one(db_session: AsyncSession, consumer: str = "graph_writer") -> int:
    """Put a real failure in the queue and return its id."""
    event = await append(db_session, payload={"broken": True})
    row = await repository.record_dead_letter(
        db_session,
        consumer=consumer,
        tenant_id=T,
        event_seq=event.seq,
        error="KeyError: 'anon_id'",
        attempts=3,
    )
    await db_session.commit()
    return row.id


async def test_the_queue_shows_what_failed_not_just_that_it_did(
    db_session: AsyncSession
) -> None:
    """`seq=418 — KeyError` tells an operator nothing they can act on."""
    await _park_one(db_session)
    async with await _client(db_session, "analyst") as ac:
        body = (await ac.get("/v1/dead-letters")).json()

    assert len(body) == 1
    assert body[0]["eventType"] == FLAKY
    assert body[0]["eventPayload"] == {"broken": True}
    assert body[0]["retryable"] is True
    app.dependency_overrides.clear()


async def test_retrying_a_stateless_consumer_resolves_the_row(
    db_session: AsyncSession, graph_session
) -> None:
    """The acceptance criterion: a forced failure lands in the queue and is retried."""
    event = await append(
        db_session, payload={"anon_id": "P-001", "zone_id": "z_a", "at": BASE.isoformat()}
    )
    row = await repository.record_dead_letter(
        db_session,
        consumer="graph_writer",
        tenant_id=T,
        event_seq=event.seq,
        error="transient",
        attempts=3,
    )
    await db_session.commit()

    async with await _client(db_session, "operator") as ac:
        res = await ac.post(f"/v1/dead-letters/{row.id}/retry")

    assert res.status_code == 200, res.text
    assert res.json()["resolved"] is True
    assert await repository.list_dead_letters(db_session) == []
    app.dependency_overrides.clear()


async def test_a_stateful_consumer_refuses_retry_and_says_why(
    db_session: AsyncSession
) -> None:
    """Retrying a tracker detection alone would give a confident wrong answer.

    Worse than the failure it was meant to fix, so the route refuses rather than
    letting an operator make the data quietly wrong.
    """
    dl_id = await _park_one(db_session, consumer="tracker")
    async with await _client(db_session, "operator") as ac:
        res = await ac.post(f"/v1/dead-letters/{dl_id}/retry")

    assert res.status_code == 409
    assert "position" in res.text or "before this one" in res.text
    app.dependency_overrides.clear()


async def test_an_unknown_consumer_404s_rather_than_killing_the_request(
    db_session: AsyncSession
) -> None:
    """`run.build()` raises SystemExit on an unknown name.

    SystemExit inherits from BaseException, so FastAPI would not catch it and
    the caller would get a dropped connection instead of an answer. The route
    resolves consumers itself for exactly this reason.
    """
    dl_id = await _park_one(db_session, consumer="a_consumer_that_was_deleted")
    async with await _client(db_session, "operator") as ac:
        res = await ac.post(f"/v1/dead-letters/{dl_id}/retry")

    assert res.status_code == 404
    app.dependency_overrides.clear()


async def test_a_failed_retry_records_the_attempt_instead_of_erroring(
    db_session: AsyncSession, graph_session
) -> None:
    """"We tried and it still does not work" is a result worth keeping.

    Otherwise an operator clicks the same button repeatedly with nothing to show
    for it.
    """
    dl_id = await _park_one(db_session)  # payload {"broken": True} — still broken
    async with await _client(db_session, "operator") as ac:
        res = await ac.post(f"/v1/dead-letters/{dl_id}/retry")

    assert res.status_code == 200
    assert res.json()["resolved"] is False
    assert res.json()["error"]

    still_open = await repository.list_dead_letters(db_session)
    assert len(still_open) == 1
    assert still_open[0].attempts == 4  # 3 from the original run, +1 for the retry
    app.dependency_overrides.clear()


async def test_dismissing_closes_what_cannot_be_retried(
    db_session: AsyncSession
) -> None:
    """Without dismiss, the tracker's failures would fill a queue nobody can clear
    — and an un-clearable queue stops being read at all."""
    dl_id = await _park_one(db_session, consumer="tracker")
    async with await _client(db_session, "operator") as ac:
        res = await ac.post(f"/v1/dead-letters/{dl_id}/resolve")

    assert res.status_code == 200
    assert await repository.list_dead_letters(db_session) == []
    app.dependency_overrides.clear()


async def test_a_viewer_cannot_retry(db_session: AsyncSession) -> None:
    """Retry re-executes a handler with real side effects."""
    dl_id = await _park_one(db_session)
    async with await _client(db_session, "viewer") as ac:
        assert (await ac.post(f"/v1/dead-letters/{dl_id}/retry")).status_code == 403
    app.dependency_overrides.clear()


async def test_another_tenants_tracebacks_are_invisible(
    db_session: AsyncSession
) -> None:
    """The `error` column holds a traceback, and a traceback can quote the payload
    it choked on. This is the most leak-prone table in the system.

    Nothing in the router filters by tenant — row-level security does it. That is
    a property of the policy rather than of this code, so it gets its own test.
    """
    await _park_one(db_session)

    async with await _client(db_session, "operator", tenant=OTHER) as ac:
        body = (await ac.get("/v1/dead-letters")).json()

    assert body == []
    app.dependency_overrides.clear()
