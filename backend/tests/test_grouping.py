"""
Group visits, and the one thing this detector must not do.

**`test_a_queue_is_not_a_group` is the whole file.** Three strangers waiting at a
popular zone stand within a metre of each other for minutes. A detector built on
distance-and-time calls that a family, calls every busy zone one giant group, and
produces a figure an operator learns to ignore within a day — the same failure
`detection_rate` drift was refused for, where a signal that fires on the common
case is worse than none because it looks like coverage.

Everything else here exists to show the two tests that replace it actually work:

- **Co-movement** — the pair's shared midpoint travelled a real distance while
  they stayed together. Walking the floor together, rather than standing in the
  same spot.
- **Joint arrival and departure** — they entered a zone within seconds of each
  other and left within seconds of each other. This catches the family who sit
  at one table and never move, which co-movement alone would miss, and a queue
  fails it because its members join at different times and are served in order.

Runs against real Postgres, in `t_test`.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.config import get_settings
from app.consumers.grouping import GroupingConsumer
from app.schemas import EventIn
from tests.conftest import as_tenant

T = "t_test"
S = "s_group"

BASE = dt.datetime(2026, 8, 21, 10, 0, 0, tzinfo=dt.timezone.utc)

FRAME_W, FRAME_H = 1000, 1000

#: Comfortably over `group_confirm_samples` (20).
SAMPLES = 30


@pytest.fixture(autouse=True)
async def _scope_to_test_tenant(db_session: AsyncSession) -> AsyncIterator[None]:
    await as_tenant(db_session, T)
    yield


async def emit(db_session: AsyncSession, *, type: str, payload: dict, at: float) -> None:
    await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(),
            tenant_id=T,
            session_id=S,
            type=type,
            payload=payload,
            occurred_at=BASE + dt.timedelta(seconds=at),
        ),
    )


async def see(db_session: AsyncSession, anon: str, nx: float, ny: float, at: float) -> None:
    """One detection of `anon` at a normalized position.

    Written in normalized coordinates and scaled up, because that is the space
    the detector reasons in and a test that did the arithmetic the other way
    round would be checking its own conversion.
    """
    half = 20
    cx, cy = nx * FRAME_W, ny * FRAME_H
    await emit(
        db_session,
        type="perception.detection",
        payload={
            "anon_id": anon,
            "bbox": [cx - half, cy - half, cx + half, cy + half],
            "confidence": 0.9,
            "frame_id": int(at * 10),
            "frame_width": FRAME_W,
            "frame_height": FRAME_H,
        },
        at=at,
    )


async def walk(
    db_session: AsyncSession, *offsets: tuple[str, float], start: float = 0.1,
    at0: float = 0.0, steps: int = SAMPLES, distance: float = 0.6,
) -> None:
    """People crossing the floor left to right, each `offset` beside the path.

    **Interleaved by time**, not one whole track then the next. That is what the
    log actually looks like — a camera reports everybody it can see in each
    frame — and writing it the other way round means nobody is ever observed
    while anybody else is current, so no pair is ever formed. Worth stating
    because the first version of this file did exactly that and every positive
    test failed while every negative one passed, which reads like a working
    detector that finds nothing.
    """
    for i in range(steps):
        x = start + distance * (i / (steps - 1))
        for anon, offset in offsets:
            await see(db_session, anon, x, 0.5 + offset, at0 + i)


async def stand(
    db_session: AsyncSession, *places: tuple[str, float, float], at0: float = 0.0,
    steps: int = SAMPLES,
) -> None:
    """People who do not move. Interleaved, for the reason above."""
    for i in range(steps):
        for anon, x, y in places:
            await see(db_session, anon, x, y, at0 + i)


async def groups(db_session: AsyncSession) -> list[dict]:
    await db_session.commit()
    rows = await repository.read_events(
        db_session, tenant_id=T, session_id=S, type="spatial.group", limit=100
    )
    return [r.payload for r in rows]


async def run(db_session: AsyncSession) -> None:
    await db_session.commit()
    await GroupingConsumer().run_once()


# ── the one that matters ──────────────────────────────────────────────────────


async def test_a_queue_is_not_a_group(db_session: AsyncSession) -> None:
    """**The test this whole design exists to pass.**

    Four strangers at a popular zone, shoulder to shoulder for the entire time,
    never moving — and joining and leaving in the order a queue does. A detector
    built on "close together for N seconds" reports one group of four. This must
    report nothing.
    """
    queue = ("P-001", "P-002", "P-003", "P-004")
    await stand(db_session, *[(a, 0.50 + i * 0.02, 0.50) for i, a in enumerate(queue)])
    for i, anon in enumerate(queue):
        # Served in order: each arrives and leaves well apart from the next.
        await emit(
            db_session, type="spatial.zone_enter",
            payload={"anon_id": anon, "zone_id": "z_bar"}, at=i * 40,
        )
        await emit(
            db_session, type="spatial.zone_exit",
            payload={"anon_id": anon, "zone_id": "z_bar", "reason": "move"},
            at=200 + i * 40,
        )

    await run(db_session)
    assert await groups(db_session) == []


# ── the two tests that replace it ─────────────────────────────────────────────


async def test_two_people_walking_the_floor_together_are_a_group(
    db_session: AsyncSession,
) -> None:
    """Co-movement: they stayed together while the scene changed."""
    await walk(db_session, ("P-001", 0.00), ("P-002", 0.03))

    await run(db_session)

    found = await groups(db_session)
    assert len(found) == 1
    assert found[0]["status"] == "formed"
    assert found[0]["members"] == ["P-001", "P-002"]
    assert found[0]["size"] == 2
    assert found[0]["cohesion"] > 0.9


async def test_a_family_who_never_move_are_a_group(db_session: AsyncSession) -> None:
    """Joint arrival and departure: the case co-movement alone would miss.

    Three people at one table for the whole session. No travel at all — so this
    passes only because they arrived within seconds of each other and left
    within seconds of each other, which is exactly what the queue above does
    not do.
    """
    family = ("P-101", "P-102", "P-103")
    places = [(a, 0.30 + i * 0.02, 0.30) for i, a in enumerate(family)]

    # Arrive together.
    for i, anon in enumerate(family):
        await emit(
            db_session, type="spatial.zone_enter",
            payload={"anon_id": anon, "zone_id": "z_table"}, at=i * 2,
        )
    # Sit.
    await stand(db_session, *places, steps=15)
    # Get up together.
    for i, anon in enumerate(family):
        await emit(
            db_session, type="spatial.zone_exit",
            payload={"anon_id": anon, "zone_id": "z_table", "reason": "move"},
            at=15 + i * 2,
        )
    # Still in frame afterwards, standing by the table. The trailing detections
    # are not padding: a group is announced while processing a *detection*, so
    # a departure that is the last thing in the log is a departure nothing has
    # had a chance to notice yet. The next frame is what notices.
    await stand(db_session, *places, at0=22, steps=15)

    await run(db_session)

    found = await groups(db_session)
    assert len(found) == 1
    assert found[0]["members"] == ["P-101", "P-102", "P-103"]
    assert found[0]["size"] == 3


async def test_arriving_together_is_not_enough_on_its_own(
    db_session: AsyncSession,
) -> None:
    """A door admits people in clumps.

    Both halves of the joint test are required, and this is why: two strangers
    who came through the entrance in the same handful of seconds and then left
    at their own separate times are two strangers.
    """
    pair = ("P-201", "P-202")
    await stand(db_session, *[(a, 0.70 + i * 0.02, 0.70) for i, a in enumerate(pair)])
    for i, anon in enumerate(pair):
        await emit(
            db_session, type="spatial.zone_enter",
            payload={"anon_id": anon, "zone_id": "z_entry"}, at=i * 2,
        )
        await emit(
            db_session, type="spatial.zone_exit",
            payload={"anon_id": anon, "zone_id": "z_entry", "reason": "move"},
            at=100 + i * 300,
        )

    await run(db_session)
    assert await groups(db_session) == []


async def test_two_strangers_crossing_paths_are_not_a_group(
    db_session: AsyncSession,
) -> None:
    """They are close for a moment and going opposite ways.

    Guards the cohesion floor: a couple of frames of proximity in the middle of
    two unrelated journeys must not accumulate into a claim.
    """
    for i in range(SAMPLES):
        t = i / (SAMPLES - 1)
        await see(db_session, "P-301", 0.1 + 0.8 * t, 0.5, i)
        await see(db_session, "P-302", 0.9 - 0.8 * t, 0.5, i)

    await run(db_session)
    assert await groups(db_session) == []


async def test_someone_walking_alone_is_not_a_group(db_session: AsyncSession) -> None:
    await walk(db_session, ("P-401", 0.0))
    await run(db_session)
    assert await groups(db_session) == []


# ── the life of a group ───────────────────────────────────────────────────────


async def test_a_third_person_joining_keeps_the_same_group(
    db_session: AsyncSession,
) -> None:
    """A group that gains somebody is the same group with one more person.

    Minting a new id would give a family two histories and leave the first
    looking as though it dissolved the moment a friend caught up.
    """
    await walk(db_session, ("P-001", 0.00), ("P-002", 0.03))
    # A third falls in alongside for the second leg.
    await walk(
        db_session, ("P-001", 0.00), ("P-002", 0.03), ("P-003", -0.03),
        start=0.7, at0=SAMPLES,
    )

    await run(db_session)

    found = await groups(db_session)
    assert [g["status"] for g in found] == ["formed", "changed"]
    assert found[0]["members"] == ["P-001", "P-002"]
    assert found[1]["members"] == ["P-001", "P-002", "P-003"]
    assert found[0]["group_id"] == found[1]["group_id"]


async def test_a_group_dissolves_when_it_ends(db_session: AsyncSession) -> None:
    """`session.ended`, the same close-out gap it was added to fill in Phase 2.

    A group announced and never withdrawn is a group still on the screen at
    midnight.
    """
    await walk(db_session, ("P-001", 0.00), ("P-002", 0.03))
    await emit(db_session, type="session.ended", payload={}, at=SAMPLES + 5)

    await run(db_session)

    found = await groups(db_session)
    assert [g["status"] for g in found] == ["formed", "dissolved"]
    assert found[0]["group_id"] == found[1]["group_id"]


async def test_the_zone_is_named_only_when_the_members_agree(
    db_session: AsyncSession,
) -> None:
    """A group straddling a boundary is genuinely not in a zone.

    Naming one would put a pair into a zone one of them is standing outside.
    """
    await emit(
        db_session, type="spatial.zone_enter",
        payload={"anon_id": "P-001", "zone_id": "z_left"}, at=0,
    )
    await emit(
        db_session, type="spatial.zone_enter",
        payload={"anon_id": "P-002", "zone_id": "z_right"}, at=0,
    )
    await walk(db_session, ("P-001", 0.00), ("P-002", 0.03), at0=1)

    await run(db_session)
    assert (await groups(db_session))[0]["zone_id"] is None


# ── replay ────────────────────────────────────────────────────────────────────


async def test_a_replay_reproduces_the_same_events(db_session: AsyncSession) -> None:
    """Rewind the cursor and run the whole log again.

    The derived id makes the second pass a no-op at the bus. `on_replay` clears
    the state first, because idempotency protects against reprocessing the same
    input and not against processing it in a different one — the trap the
    tracker's own `on_replay` records.
    """
    await walk(db_session, ("P-001", 0.00), ("P-002", 0.03))
    await emit(db_session, type="session.ended", payload={}, at=SAMPLES + 5)

    await run(db_session)
    first = await groups(db_session)
    assert len(first) == 2

    await repository.reset_cursor(db_session, tenant_id=T, consumer="grouping", to_seq=0)
    # Committed before the consumer runs, or it blocks on the row lock this
    # session still holds.
    await db_session.commit()

    consumer = GroupingConsumer()
    await consumer.on_replay(T)
    await consumer.run_once()

    assert await groups(db_session) == first


async def test_the_sample_floor_is_respected(db_session: AsyncSession) -> None:
    """Two people together for five frames are two people who passed.

    Same reasoning as `drift_min_samples`: below a floor there is nothing to be
    confident about, and a claim built there is a guess wearing an event type.
    """
    settings = get_settings()
    assert settings.group_confirm_samples == 20

    await walk(db_session, ("P-001", 0.00), ("P-002", 0.03), steps=5)

    await run(db_session)
    assert await groups(db_session) == []
