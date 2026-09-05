"""
A zone against the capacity its operator set — the crowding half of the floor
orchestrator, which is measurement rather than prediction.

`Zone.capacity` has been declared in `data-model.md` ("optional, for crowding
alerts"), accepted by `ZoneConfig`, stored by `upsert_zone` and offered by the
wizard's zone editor since Phase 1, and read by nothing. These are the claims
that make it mean something.

Most of the file is again about refusals, because that is where the design is:

  - one event per **crossing**, not per arrival into a full room;
  - a zone with **no capacity** produces nothing at any count;
  - the answer is judged on **event time**, so a batch replayed after an outage
    gives the same verdict as the run it is a copy of;
  - a replay produces the crossings again and no duplicates.

The tracker's output is seeded directly rather than run, the way `test_touch.py`
does it: this consumer's subject is what it decides about a zone, not whether
the tracker can find a person.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator

import pytest
from neo4j import AsyncSession as GraphSession
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.consumers.occupancy import (
    OCCUPANCY,
    OccupancyConsumer,
    STATUS_CLEARED,
    STATUS_OVER,
    occupants_at,
)
from app.consumers.rules import RulesConsumer
from app.graph import repository as graph_repo
from app.schemas import EventIn
from tests.conftest import as_tenant

T = "t_test"
S = "s_occupancy"

BASE = dt.datetime(2026, 9, 2, 10, 0, 0, tzinfo=dt.timezone.utc)
POLY = [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]

ZONE = "z_entry"
FIRED = "rule.fired"


@pytest.fixture(autouse=True)
async def _scope_to_test_tenant(db_session: AsyncSession) -> AsyncIterator[None]:
    await as_tenant(db_session, T)
    yield


async def seed(graph_session: GraphSession, *, capacity: int | None = 3) -> None:
    """An activation with one zone. `capacity=None` is the operator who left the
    field blank, which is most of them."""
    await graph_repo.upsert_session(
        graph_session, tenant_id=T, session_id=S, venue="Crowd Hall"
    )
    await graph_repo.upsert_zone(
        graph_session,
        tenant_id=T,
        session_id=S,
        zone_id=ZONE,
        name="Entry Arch",
        type="entry",
        polygon=POLY,
        capacity=capacity,
    )


async def enter(
    db_session: AsyncSession, anon_id: str, *, at: dt.datetime, zone_id: str = ZONE
) -> None:
    """One `spatial.zone_enter`, snake_case as the tracker actually writes it —
    the mistake `test_rules.py` records having made, where a whole green file
    proved things about events no producer emits."""
    await _emit(
        db_session,
        type="spatial.zone_enter",
        payload={"anon_id": anon_id, "zone_id": zone_id, "at": at.isoformat()},
        at=at,
    )


async def leave(
    db_session: AsyncSession, anon_id: str, *, at: dt.datetime, zone_id: str = ZONE
) -> None:
    await _emit(
        db_session,
        type="spatial.zone_exit",
        payload={"anon_id": anon_id, "zone_id": zone_id, "at": at.isoformat()},
        at=at,
    )


async def _emit(
    db_session: AsyncSession, *, type: str, payload: dict, at: dt.datetime
) -> None:
    await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(),
            tenant_id=T,
            session_id=S,
            type=type,
            payload=payload,
            occurred_at=at,
        ),
    )
    await db_session.commit()


async def crossings(db_session: AsyncSession) -> list[dict]:
    rows = await repository.read_events(
        db_session, tenant_id=T, session_id=S, type=OCCUPANCY, limit=50
    )
    return [row.payload for row in rows]


def at(seconds: int) -> dt.datetime:
    return BASE + dt.timedelta(seconds=seconds)


# ── the crossing ────────────────────────────────────────────────────────────


async def test_reaching_capacity_emits_once(
    graph_session: GraphSession, db_session: AsyncSession
) -> None:
    """Three into a three-person zone is the crossing. At capacity counts as
    over: a zone an operator called a three-person zone is full at three."""
    await seed(graph_session, capacity=3)
    await enter(db_session, "cam-1/P-001", at=at(0))
    await enter(db_session, "cam-1/P-002", at=at(5))
    await enter(db_session, "cam-1/P-003", at=at(10))

    await OccupancyConsumer().run_once()

    said = await crossings(db_session)
    assert len(said) == 1
    assert said[0]["status"] == STATUS_OVER
    assert said[0]["occupancy"] == 3
    assert said[0]["capacity"] == 3
    assert said[0]["zone_id"] == ZONE
    # The name travels with the crossing, as `surface_label` travels with a tap:
    # a staff prompt naming `z_entry` is one somebody has to translate on the
    # floor.
    assert said[0]["zone_name"] == "Entry Arch"


async def test_a_full_zone_does_not_re_announce_itself(
    graph_session: GraphSession, db_session: AsyncSession
) -> None:
    """The load-bearing refusal. An event per arrival into a room already full
    is `gaze.py`'s rule — a signal that fires on the common case looks like
    coverage — and downstream it is a staff prompt raised nine times in a minute,
    which teaches the floor to stop reading the panel."""
    await seed(graph_session, capacity=3)
    for i, anon in enumerate(["P-001", "P-002", "P-003", "P-004", "P-005", "P-006"]):
        await enter(db_session, f"cam-1/{anon}", at=at(i * 5))

    await OccupancyConsumer().run_once()

    said = await crossings(db_session)
    assert [s["status"] for s in said] == [STATUS_OVER]
    assert said[0]["occupancy"] == 3


async def test_dropping_below_capacity_clears(
    graph_session: GraphSession, db_session: AsyncSession
) -> None:
    """And the pair is a pair: one over, one cleared, so a rule can act on
    either end. Deliberately no hysteresis band — somebody stepping in and out
    of a full zone produces two real crossings, and boundary flicker is dealt
    with once, in the tracker's confirm window."""
    await seed(graph_session, capacity=2)
    await enter(db_session, "cam-1/P-001", at=at(0))
    await enter(db_session, "cam-1/P-002", at=at(5))
    await leave(db_session, "cam-1/P-002", at=at(20))
    await enter(db_session, "cam-1/P-003", at=at(30))

    await OccupancyConsumer().run_once()

    said = await crossings(db_session)
    assert [s["status"] for s in said] == [STATUS_OVER, STATUS_CLEARED, STATUS_OVER]
    assert [s["occupancy"] for s in said] == [2, 1, 2]


async def test_a_zone_with_no_capacity_says_nothing_at_any_count(
    graph_session: GraphSession, db_session: AsyncSession
) -> None:
    """Not a default, at any count. A threshold nobody set is not a threshold,
    and supplying one is the invention `roi-framework.md` rules out for revenue
    and P3's cost tile refused for spend."""
    await seed(graph_session, capacity=None)
    for i in range(12):
        await enter(db_session, f"cam-1/P-{i:03d}", at=at(i * 5))

    await OccupancyConsumer().run_once()

    assert await crossings(db_session) == []


async def test_a_zone_the_operator_removed_says_nothing(
    graph_session: GraphSession, db_session: AsyncSession
) -> None:
    """`graph_writer` declines an event for a zone nobody configured rather than
    conjuring one. This declines a step earlier, and the transitions stay on the
    log either way."""
    await seed(graph_session, capacity=1)
    await enter(db_session, "cam-1/P-001", at=at(0), zone_id="z_deleted")

    await OccupancyConsumer().run_once()

    assert await crossings(db_session) == []


async def test_a_duplicate_arrival_crosses_nothing(
    graph_session: GraphSession, db_session: AsyncSession
) -> None:
    """Somebody already inside entering again is not a person. The occupant set
    is a set for this reason, and the count *before* the event is that set with
    this one transition undone."""
    await seed(graph_session, capacity=2)
    await enter(db_session, "cam-1/P-001", at=at(0))
    await enter(db_session, "cam-1/P-001", at=at(5))

    await OccupancyConsumer().run_once()

    assert await crossings(db_session) == []


# ── event time, and replay ──────────────────────────────────────────────────


async def test_occupancy_is_judged_on_event_time_not_log_order(
    graph_session: GraphSession, db_session: AsyncSession
) -> None:
    """The 2026-08-25 walk's defect 7, in this consumer's shape: the scorecard
    read 1 where three visitors overlapped, because a running tally over the log
    answers "how many at once" only if the log happens to be sorted by when
    things happened. Here the third arrival is appended first and happened last,
    so a seq-ordered reader crosses the line on the wrong event — or, with the
    capacity one higher, never crosses it at all."""
    await seed(graph_session, capacity=3)
    # Buffered through an outage and replayed out of order: appended first,
    # happened last.
    await enter(db_session, "cam-1/P-003", at=at(30))
    await enter(db_session, "cam-1/P-001", at=at(0))
    await enter(db_session, "cam-1/P-002", at=at(15))

    await OccupancyConsumer().run_once()

    said = await crossings(db_session)
    assert len(said) == 1
    assert said[0]["occupancy"] == 3
    # The crossing is stamped when the room filled, not when the log caught up.
    assert said[0]["at"] == at(30).isoformat()


async def test_replay_produces_the_same_crossings_and_no_duplicates(
    graph_session: GraphSession, db_session: AsyncSession
) -> None:
    """The property the whole design is arranged around. The occupant set is
    recomputed from the log rather than accumulated, so a rewound cursor
    re-derives the same `event_id`s and the bus swallows them — where an
    in-memory tally would fire again from a different seq and mint an id nothing
    could dedupe."""
    await seed(graph_session, capacity=2)
    await enter(db_session, "cam-1/P-001", at=at(0))
    await enter(db_session, "cam-1/P-002", at=at(5))
    await leave(db_session, "cam-1/P-001", at=at(20))

    consumer = OccupancyConsumer()
    await consumer.run_once()
    first = await crossings(db_session)

    await repository.reset_cursor(db_session, consumer=consumer.name, tenant_id=T)
    await db_session.commit()
    await consumer.run_once()

    assert await crossings(db_session) == first
    assert [s["status"] for s in first] == [STATUS_OVER, STATUS_CLEARED]


# ── one definition of who is in the room ────────────────────────────────────


async def test_touch_reads_occupancy_through_the_same_function(
    db_session: AsyncSession,
) -> None:
    """Not a formality. Two readers of "who is in this zone" could disagree
    about whether the room is empty — one attributing a tap to somebody `/live`
    says is not there — and neither answer would be obviously the wrong one. The
    same argument `consumers/zones.py` makes for being a port of
    `zone-detect.ts` rather than a second implementation."""
    from app.consumers import touch

    assert touch.occupants_at is occupants_at


async def test_the_shared_reader_counts_who_is_inside_at_a_moment(
    db_session: AsyncSession,
) -> None:
    """Read whole rather than windowed, so somebody who has been standing there
    since before any window still counts."""
    await enter(db_session, "cam-1/P-001", at=at(0))
    await enter(db_session, "cam-1/P-002", at=at(600))
    await leave(db_session, "cam-1/P-001", at=at(900))

    assert await occupants_at(
        db_session, tenant_id=T, session_id=S, zone_id=ZONE, at=at(700)
    ) == {"cam-1/P-001", "cam-1/P-002"}
    assert await occupants_at(
        db_session, tenant_id=T, session_id=S, zone_id=ZONE, at=at(1000)
    ) == {"cam-1/P-002"}


# ── the reason it exists: a rule an operator can arm ────────────────────────


async def crowding_rule(db_session: AsyncSession, **overrides) -> None:
    """The preset `agents/presets.ts::capacityRules` builds, stored directly so
    the evaluator is under test rather than the composer."""
    condition = overrides.pop(
        "condition", {"type": "any", "payloadEquals": {"status": STATUS_OVER}}
    )
    await repository.upsert_rule(
        db_session,
        tenant_id=T,
        rule_id="r_entry_full",
        name="Entry at capacity",
        trigger_type=OCCUPANCY,
        trigger_zone_id=ZONE,
        condition=condition,
        action={"type": "staff_prompt", "message": "Entry Arch is at capacity"},
        enabled=True,
        cooldown_sec=60,
        **overrides,
    )
    await db_session.commit()


async def firings(db_session: AsyncSession) -> list[dict]:
    rows = await repository.read_events(
        db_session, tenant_id=T, session_id=S, type=FIRED, limit=10
    )
    return [row.payload for row in rows]


async def test_a_crowding_rule_fires_on_a_crossing(
    graph_session: GraphSession, db_session: AsyncSession
) -> None:
    """The point of the phase bullet: prompting through the Phase 3 rules
    engine. `triggerType` is open by ADR-002 and `_matches_scope` reads
    `zone_id` off the payload, so a crowding rule became writable the moment
    something produced the event — and was unwritable before it, because
    `spatial.dwell` is emitted when somebody *leaves*."""
    await seed(graph_session, capacity=2)
    await crowding_rule(db_session)

    await enter(db_session, "cam-1/P-001", at=at(0))
    await enter(db_session, "cam-1/P-002", at=at(5))

    await OccupancyConsumer().run_once()
    await RulesConsumer().run_once()

    fired = await firings(db_session)
    assert len(fired) == 1
    assert fired[0]["ruleId"] == "r_entry_full"


async def test_the_prompt_is_not_raised_again_when_the_zone_clears(
    graph_session: GraphSession, db_session: AsyncSession
) -> None:
    """`payloadEquals`, and the reason it had to exist. Both ends of a crossing
    are one event type, so a rule armed on the type alone raises "Entry Arch is
    at capacity" at the moment it stops being true. Nothing had noticed because
    `spatial.group` has carried the same shape since Phase 6 and no rule had
    been armed on it."""
    await seed(graph_session, capacity=2)
    await crowding_rule(db_session)

    await enter(db_session, "cam-1/P-001", at=at(0))
    await enter(db_session, "cam-1/P-002", at=at(5))
    await leave(db_session, "cam-1/P-002", at=at(120))

    await OccupancyConsumer().run_once()
    await RulesConsumer().run_once()

    said = await crossings(db_session)
    assert [s["status"] for s in said] == [STATUS_OVER, STATUS_CLEARED]
    # Both are on the log; only one is a thing to tell the floor.
    assert len(await firings(db_session)) == 1


async def test_without_the_filter_it_would_have_fired_on_both(
    graph_session: GraphSession, db_session: AsyncSession
) -> None:
    """The wrong answer, asserted, so the test above has one available. Without
    this the previous test passes against an evaluator that ignores
    `payloadEquals` entirely — it would just never see the second crossing."""
    await seed(graph_session, capacity=2)
    await crowding_rule(db_session, condition={"type": "any"})

    await enter(db_session, "cam-1/P-001", at=at(0))
    await enter(db_session, "cam-1/P-002", at=at(5))
    await leave(db_session, "cam-1/P-002", at=at(120))

    await OccupancyConsumer().run_once()
    await RulesConsumer().run_once()

    assert len(await firings(db_session)) == 2
