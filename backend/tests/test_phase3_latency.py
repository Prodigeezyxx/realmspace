"""
Phase 3's other half of the acceptance criterion: the `< 3s`.

`test_phase3_acceptance.py` proves the chain is *correct* — five people dwell,
Slack gets pinged, a forced failure parks in the queue. It proves nothing about
time, and it cannot: it calls `run_once()` three times in a row, in process
order, with no poll interval anywhere. Under that arrangement the answer is
always "fast", including in a build where the real deployment takes twenty
seconds.

So this file measures the thing the roadmap actually claims. Three differences
from the acceptance test, each of them the point:

- **The consumers run their real loop.** `run_forever()`, at the configured
  `consumer_busy_interval_seconds` / `consumer_idle_interval_seconds`, started
  as concurrent tasks the way `main.py`'s lifespan starts them. The poll
  interval between links is most of the budget, so a test that skips it is
  measuring the handlers and calling it the SLA.
- **Events arrive over HTTP.** `POST /v1/events`, so append, RLS scoping and
  commit are on the clock rather than a direct `repository.append_event`.
- **The stopwatch starts at an ingest response and stops in the Slack client.**
  Not at the top of the test — the seeding, the rule insert and the four hundred
  detections are setup, not latency.

What is being timed is the last link of the causal chain: the `session.ended`
that closes the five visits is the event that makes the rule true, so the budget
runs from its 201 to the moment the Slack handler posts. Everything before it is
a room filling up over two minutes of event time.

## What this does not cover

The consumers here are in one process on one machine, against a local Postgres
and a Slack that answers instantly. A real deployment adds network to the
webhook and, on the edge box, a smaller machine. This measures the part we
control and the part that regressed silently before: the number of poll
intervals a detection waits through.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from neo4j import AsyncSession as GraphSession
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.auth.models import AuthUser
from app.auth.tokens import issue_token
from app.config import get_settings
from app.consumers import run as consumer_run
from app.db import get_session
from app.graph import repository as graph_repo
from app.main import app
from app.schemas import EventIn

T = "t_test"
S = "s_latency"
BASE = dt.datetime(2026, 8, 12, 10, 0, 0, tzinfo=dt.timezone.utc)

ENTRY_POLY = [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]
FRAME_W, FRAME_H = 1000, 1000
ENTRY_PX = [200, 400, 300, 600]

SLACK_URL = "https://hooks.slack.invalid/latency"

#: The budget, from `roadmap.md`'s Phase 3 acceptance line and
#: `event-bus-spec.md` §4.
BUDGET_SECONDS = 3.0

#: How long the test is willing to wait before calling it a failure. Deliberately
#: well past the budget: a run that takes four seconds should fail with the
#: measured number in the message, not time out with "the Slack call never came"
#: — those are different diagnoses and the second one hides the first.
PATIENCE_SECONDS = 20.0


class SlackStopwatch:
    """Records the Slack post and the moment it happened."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.at: float | None = None


@pytest.fixture
def slack(monkeypatch) -> SlackStopwatch:
    """Intercept the Slack post, and *only* the Slack post.

    `httpx.AsyncClient.post` is patched at the class level, which is also what
    this test's own ingest client goes through — so anything that is not the
    webhook is handed straight back to the real implementation. Patching
    somewhere narrower would avoid that, and would also mean the fake sits
    somewhere `app/actions/slack.py` does not actually call: it builds its own
    client inside `deliver()`.

    A `def`, not a callable object, because a plain function is a descriptor and
    an instance is not: assigning an instance to the class attribute means
    `client.post(url)` arrives with no `self` at all, and the real client can
    never be reached to delegate to.
    """
    monkeypatch.setattr(get_settings(), "slack_webhook_url", SLACK_URL)
    stopwatch = SlackStopwatch()
    original = httpx.AsyncClient.post

    async def post(self, url, **kwargs):
        if not str(url).startswith(SLACK_URL):
            return await original(self, url, **kwargs)
        stopwatch.calls.append({"url": str(url), **kwargs})
        stopwatch.at = time.monotonic()
        return httpx.Response(200, request=httpx.Request("POST", str(url)), text="ok")

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    return stopwatch


@pytest.fixture
async def producer(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """An authenticated client for `t_test`.

    The shared `client` fixture is for `t_floats`, and the graph fixtures only
    wipe the `t_test*` tenants — an activation seeded under `t_floats` would
    leave zone and session nodes behind in the dev Neo4j instance, which is the
    one thing `conftest.py` is careful never to do.
    """
    from tests.conftest import as_tenant

    await as_tenant(db_session, T)
    db_session.add(
        AuthUser(
            user_id="u_latency",
            email="latency@floats.demo",
            display_name="Latency",
            tenant_id=T,
            role="admin",
        )
    )
    await db_session.commit()
    token = issue_token(subject="u_latency", tenant_id=T, role="admin")

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        yield db_session
        await db_session.commit()

    app.dependency_overrides[get_session] = override_get_session
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def pipeline() -> AsyncIterator[None]:
    """Every consumer `main.py` runs, running the way it runs in production.

    `build_all()` rather than a hand-picked three: the graph writer and the
    broadcast consumer are not on the path to Slack, but they are on the same
    event loop competing for the same database, and leaving them out would
    measure a deployment nobody runs.
    """
    consumers = consumer_run.build_all()
    tasks = [asyncio.create_task(c.run_forever()) for c in consumers]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def post_event(
    producer: AsyncClient, *, type: str, payload: dict, occurred_at: dt.datetime
) -> httpx.Response:
    event = EventIn(
        event_id=uuid.uuid4(),
        tenant_id=T,
        session_id=S,
        type=type,
        payload=payload,
        occurred_at=occurred_at,
    )
    response = await producer.post(
        "/v1/events", json=event.model_dump(by_alias=True, mode="json")
    )
    assert response.status_code == 201, response.text
    return response


async def seed_entrance(graph_session: GraphSession) -> None:
    await graph_repo.upsert_session(
        graph_session, tenant_id=T, session_id=S, venue="Test Hall"
    )
    await graph_repo.upsert_zone(
        graph_session,
        tenant_id=T,
        session_id=S,
        zone_id="z_entry",
        name="Entrance",
        type="entry",
        polygon=ENTRY_POLY,
    )


async def arm_the_rule(db_session: AsyncSession) -> None:
    """ADR-002's example, the same one the acceptance test arms."""
    await repository.upsert_rule(
        db_session,
        tenant_id=T,
        rule_id="r_entry_crowd",
        name="Entrance crowding → ping ops",
        trigger_type="spatial.dwell",
        trigger_zone_id="z_entry",
        condition={
            "type": "threshold",
            "count": 5,
            "windowSec": 300,
            "zoneId": "z_entry",
            "minDwellSec": 30,
        },
        action={"type": "slack", "channel": "#ops", "message": "5 at entrance"},
        enabled=True,
        cooldown_sec=60,
    )
    await db_session.commit()


async def fill_the_entrance(producer: AsyncClient) -> None:
    """Five visitors, detected across a stay long enough to count.

    Same shape as the acceptance test's, and for the same reason — the tracker's
    confirm and dropout windows are written against a stream of detections, not
    one per person. These go over HTTP, so this is also the ingest path carrying
    a realistic burst rather than a single row.
    """
    for person in range(5):
        arrives = BASE + dt.timedelta(seconds=person * 2)
        moment = arrives
        end = arrives + dt.timedelta(seconds=45)
        while True:
            await post_event(
                producer,
                type="perception.detection",
                payload={
                    "person_id": f"P-{person:03d}",
                    "bbox": ENTRY_PX,
                    "confidence": 0.9,
                    "frame_width": FRAME_W,
                    "frame_height": FRAME_H,
                },
                occurred_at=moment,
            )
            if moment >= end:
                break
            moment = min(moment + dt.timedelta(seconds=5), end)


async def wait_for_slack(slack: SlackStopwatch) -> None:
    deadline = time.monotonic() + PATIENCE_SECONDS
    while not slack.calls:
        if time.monotonic() > deadline:
            pytest.fail(
                f"no Slack post within {PATIENCE_SECONDS}s — this is not a latency "
                "failure, it is the chain not completing at all; run "
                "test_phase3_acceptance.py to find out where it stops"
            )
        await asyncio.sleep(0.02)


async def test_the_slack_ping_lands_inside_the_three_second_budget(
    producer: AsyncClient,
    db_session: AsyncSession,
    graph_session: GraphSession,
    slack: SlackStopwatch,
    pipeline: None,
):
    """`roadmap.md`: *fires live in `< 3s`*. Measured, not asserted.

    The clock starts when the log has accepted the event that makes the rule
    true and stops inside the Slack handler. In between are four consumers, each
    of which may cost up to one poll interval, plus whatever the handlers take.
    """
    await seed_entrance(graph_session)
    await arm_the_rule(db_session)
    await fill_the_entrance(producer)

    # The doors shut. This is the event that closes the five visits, so it is
    # the one the budget is measured from — the detections before it describe a
    # room filling up over two minutes, which is not latency.
    await post_event(
        producer,
        type="session.ended",
        payload={"endedBy": "u_latency"},
        occurred_at=BASE + dt.timedelta(seconds=120),
    )
    started = time.monotonic()

    await wait_for_slack(slack)

    elapsed = slack.at - started
    print(f"\ndetection → Slack: {elapsed * 1000:.0f}ms (budget {BUDGET_SECONDS * 1000:.0f}ms)")

    assert len(slack.calls) == 1
    assert slack.calls[0]["json"]["text"] == "5 at entrance"
    assert elapsed < BUDGET_SECONDS, (
        f"the firing took {elapsed:.2f}s, over the {BUDGET_SECONDS}s in "
        "roadmap.md's Phase 3 acceptance. Each consumer hop costs up to one "
        "consumer_busy_interval_seconds; check that count first."
    )
