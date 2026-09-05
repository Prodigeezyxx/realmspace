"""
Phase 3's acceptance criterion, as one test.

`roadmap.md`: *"when 5 people dwell at entrance 30s → ping Slack" fires live in
`< 3s`; a forced failure lands in the HITL queue and can be retried.*

Every part of that chain has its own tests — the tracker in `test_consumers.py`,
the evaluator in `test_rules.py`, the dispatcher in `test_dispatch.py`. This runs
them joined together, because the failures that matter here are the ones between
the pieces: an event shape one consumer emits and the next does not recognise, a
consumer order that makes a firing wait a poll interval, a zone id that agrees
with the graph but not with the rule.

Five people walk into the entrance zone and stay. Nobody tells the rules engine
anything; it reads the same log the tracker wrote to.
"""

from __future__ import annotations

import datetime as dt
import uuid

import httpx
import pytest
from neo4j import AsyncSession as GraphSession
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.config import get_settings
from app.consumers.dispatch import DispatchConsumer
from app.consumers.rules import RulesConsumer
from app.consumers.tracker import TrackerConsumer
from app.graph import repository as graph_repo
from app.schemas import EventIn

T = "t_test"
S = "s_acceptance"
BASE = dt.datetime(2026, 8, 12, 10, 0, 0, tzinfo=dt.timezone.utc)

ENTRY_POLY = [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]
FRAME_W, FRAME_H = 1000, 1000
#: centroid (250, 500) → 0.25, 0.5 — inside the entry polygon.
ENTRY_PX = [200, 400, 300, 600]


@pytest.fixture(autouse=True)
async def _scope_to_test_tenant(db_session: AsyncSession):
    from tests.conftest import as_tenant

    await as_tenant(db_session, T)
    yield


class Posted:
    def __init__(self, status: int = 200) -> None:
        self.calls: list[dict] = []
        self.status = status

    async def __call__(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return httpx.Response(
            self.status, request=httpx.Request("POST", str(url)), text="ok"
        )


@pytest.fixture
def slack(monkeypatch) -> Posted:
    monkeypatch.setattr(
        get_settings(), "slack_webhook_url", "https://hooks.slack.invalid/x"
    )
    recorder = Posted()
    monkeypatch.setattr(httpx.AsyncClient, "post", recorder)
    return recorder


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


async def arm_the_rule(db_session: AsyncSession, *, action: dict) -> None:
    """ADR-002's own example, word for word."""
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
        action=action,
        enabled=True,
        cooldown_sec=60,
    )
    await db_session.commit()


async def five_people_at_the_entrance(db_session: AsyncSession) -> None:
    """Five visitors, each detected across a stay long enough to count.

    Detections spread across the stay rather than one apiece, because that is
    what a camera produces and what the tracker's confirm and dropout windows are
    written against (`test_consumers.py` makes the same point at length).
    """
    for person in range(5):
        arrives = BASE + dt.timedelta(seconds=person * 2)
        moment = arrives
        end = arrives + dt.timedelta(seconds=45)
        while True:
            await repository.append_event(
                db_session,
                EventIn(
                    event_id=uuid.uuid4(),
                    tenant_id=T,
                    session_id=S,
                    type="perception.detection",
                    payload={
                        "person_id": f"P-{person:03d}",
                        "bbox": ENTRY_PX,
                        "confidence": 0.9,
                        "frame_width": FRAME_W,
                        "frame_height": FRAME_H,
                    },
                    occurred_at=moment,
                ),
            )
            if moment >= end:
                break
            moment = min(moment + dt.timedelta(seconds=5), end)

    # The doors shut. Without this the tracker never closes the visits still open
    # — no dwell is emitted, and the rule has nothing to fire on. It is the same
    # reason the tail of every activation was being lost before `session.ended`
    # existed.
    await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(),
            tenant_id=T,
            session_id=S,
            type="session.ended",
            payload={"endedBy": "u_test"},
            occurred_at=BASE + dt.timedelta(seconds=120),
        ),
    )
    await db_session.commit()


async def run_the_pipeline() -> None:
    """The consumers, in the order `consumers/run.py` registers them.

    Order is the point: the tracker produces the dwells the rule fires on, and
    the evaluator produces the firing the dispatcher acts on. Registered the
    other way round each link would wait a poll interval for the one before it —
    still correct, and spending most of the `< 3s` budget waiting on nothing.
    """
    await TrackerConsumer().run_once()
    await RulesConsumer().run_once()
    await DispatchConsumer().run_once()


async def events_of(db_session: AsyncSession, type: str) -> list:
    return await repository.read_events(
        db_session, tenant_id=T, session_id=S, type=type, limit=200
    )


async def test_five_people_dwelling_at_the_entrance_ping_slack(
    db_session: AsyncSession, graph_session: GraphSession, slack: Posted
):
    """The acceptance criterion, from raw detections to the Slack call.

    Nothing in here tells the evaluator that five people arrived. It reads
    `spatial.dwell` off the log — events the tracker derived from
    `perception.detection` — which is what makes the rules engine a consumer
    rather than a feature of the tracker.
    """
    await seed_entrance(graph_session)
    await arm_the_rule(
        db_session,
        action={"type": "slack", "channel": "#ops", "message": "5 at entrance"},
    )
    await five_people_at_the_entrance(db_session)

    await run_the_pipeline()

    dwells = await events_of(db_session, "spatial.dwell")
    assert len(dwells) == 5, "the tracker should close five visits"

    firings = await events_of(db_session, "rule.fired")
    assert len(firings) == 1
    assert firings[0].payload["ruleId"] == "r_entry_crowd"
    assert firings[0].payload["matched"] == {
        "observed": 5,
        "countedBy": "people",
        "windowSec": 300,
    }

    assert len(slack.calls) == 1
    assert slack.calls[0]["json"]["text"] == "5 at entrance"

    # And it cost something, which is the first time anything on this track has.
    costs = await events_of(db_session, "cost.metered")
    assert len(costs) == 1
    assert costs[0].payload["kind"] == "action_unit"


async def test_the_whole_chain_replays_to_the_same_output(
    db_session: AsyncSession, graph_session: GraphSession, slack: Posted
):
    """Rewind every cursor and run it again.

    This is the property the four ADR-002 corrections exist for, asserted at the
    only level where all of them are load-bearing at once. A wall-clock cooldown
    fires twice here; a random `rule.fired` id posts to Slack twice; an in-memory
    window produces a different set of dwells to fire on.
    """
    await seed_entrance(graph_session)
    await arm_the_rule(
        db_session,
        action={"type": "slack", "channel": "#ops", "message": "5 at entrance"},
    )
    await five_people_at_the_entrance(db_session)
    await run_the_pipeline()

    for consumer in ("tracker", "rules", "dispatch"):
        await repository.reset_cursor(
            db_session, consumer=consumer, tenant_id=T, to_seq=0
        )
    await db_session.commit()

    await run_the_pipeline()

    assert len(await events_of(db_session, "spatial.dwell")) == 5
    assert len(await events_of(db_session, "rule.fired")) == 1
    assert len(await events_of(db_session, "cost.metered")) == 1
    assert len(slack.calls) == 1


async def test_a_staff_prompt_reaches_the_room(
    db_session: AsyncSession, graph_session: GraphSession
):
    """The Next-Step surface, end to end.

    `rule.staff_prompt` lands on the log, which is what `/live` reads and what a
    tablet reconnecting mid-session catches up from.
    """
    await seed_entrance(graph_session)
    await arm_the_rule(
        db_session,
        action={
            "type": "staff_prompt",
            "message": "Greet the group at the entrance",
            "zoneId": "z_entry",
            "priority": "high",
        },
    )
    await five_people_at_the_entrance(db_session)

    await run_the_pipeline()

    prompts = await events_of(db_session, "rule.staff_prompt")
    assert len(prompts) == 1
    assert prompts[0].payload["message"] == "Greet the group at the entrance"
    assert prompts[0].payload["priority"] == "high"
    # Dated when the rule fired, not when the dispatcher ran, so the panel shows
    # the same thing in a replay as it did live.
    assert prompts[0].occurred_at == (await events_of(db_session, "rule.fired"))[
        0
    ].occurred_at


async def test_a_forced_failure_lands_in_the_hitl_queue(
    db_session: AsyncSession, graph_session: GraphSession, monkeypatch
):
    """The second half of the acceptance criterion.

    Slack is down. The firing still happened and is still on the log — the rule
    worked; the delivery did not — and the failure is in the queue `/ops` reads,
    with the traceback attached.
    """
    monkeypatch.setattr(
        get_settings(), "slack_webhook_url", "https://hooks.slack.invalid/x"
    )
    monkeypatch.setattr(httpx.AsyncClient, "post", Posted(status=503))

    await seed_entrance(graph_session)
    await arm_the_rule(
        db_session,
        action={"type": "slack", "channel": "#ops", "message": "5 at entrance"},
    )
    await five_people_at_the_entrance(db_session)

    await run_the_pipeline()

    assert len(await events_of(db_session, "rule.fired")) == 1

    parked = await repository.list_dead_letters(db_session, consumer="dispatch")
    assert len(parked) == 1
    assert "503" in parked[0].error

    # Nothing was billed for a message that never arrived.
    assert await events_of(db_session, "cost.metered") == []
