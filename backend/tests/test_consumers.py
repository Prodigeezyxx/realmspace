"""
The tracker and graph writer, as executable claims.

The load-bearing one is test_replay_is_a_no_op. Everything else here could pass
with random event ids; that one cannot. It is the test that proves items 1, 2
and 3 actually lock together — a crash mid-batch redelivers events, and the
system has to absorb that without double-counting a single dwell.

Runs against real Postgres and real Neo4j. Consumers are driven with run_once()
rather than the background loop, so nothing races a sleep.
"""

from __future__ import annotations

import datetime as dt
import time
import uuid

import pytest
from neo4j import AsyncSession as GraphSession
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.consumers.graph_writer import GraphWriterConsumer
from app.consumers.ids import derive_event_id
from app.consumers import zones as zones_geom
from app.consumers.tracker import TrackerConsumer
from app.graph import repository as graph_repo
from app.schemas import EventIn

T = "t_test"
OTHER = "t_test_other"
S = "s_consumers"

BASE = dt.datetime(2026, 7, 31, 10, 0, 0, tzinfo=dt.timezone.utc)

# Two non-overlapping halves of the frame, in normalized coords.
LEFT_POLY = [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]
RIGHT_POLY = [[0.5, 0.0], [1.0, 0.0], [1.0, 1.0], [0.5, 1.0]]

FRAME_W, FRAME_H = 1000, 1000
LEFT_PX = [200, 400, 300, 600]    # centroid (250, 500) → 0.25 → left zone
RIGHT_PX = [700, 400, 800, 600]   # centroid (750, 500) → 0.75 → right zone
# centroid (450, 500) → 0.45: inside the left zone, 0.05 clear of the right one,
# which is inside the 0.08 pass-by radius.
NEAR_PX = [400, 400, 500, 600]


@pytest.fixture(autouse=True)
async def _scope_to_test_tenant(db_session: AsyncSession):
    """These tests work in `t_test`, not the default `t_floats`.

    Row-level security means a session only sees and writes the tenant it has
    declared, so saying so is now mandatory rather than cosmetic.
    """
    from tests.conftest import as_tenant

    await as_tenant(db_session, T)
    yield


async def seed_zones(graph_session: GraphSession, tenant_id: str = T) -> None:
    await graph_repo.upsert_session(
        graph_session, tenant_id=tenant_id, session_id=S, venue="Test Hall"
    )
    await graph_repo.upsert_zone(
        graph_session, tenant_id=tenant_id, session_id=S,
        zone_id="z_left", name="Entrance", type="entry", polygon=LEFT_POLY,
    )
    await graph_repo.upsert_zone(
        graph_session, tenant_id=tenant_id, session_id=S,
        zone_id="z_right", name="Lounge", type="lounge", polygon=RIGHT_POLY,
    )


async def detect(
    db_session: AsyncSession,
    *,
    bbox: list[int],
    at: dt.datetime,
    anon_id: str = "P-001",
    tenant_id: str = T,
) -> None:
    """Write one perception.detection into the log, as perception will."""
    await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(),
            tenant_id=tenant_id,
            session_id=S,
            type="perception.detection",
            payload={
                "person_id": anon_id,
                "bbox": bbox,
                "confidence": 0.9,
                "frame_width": FRAME_W,
                "frame_height": FRAME_H,
            },
            occurred_at=at,
        ),
    )
    await db_session.commit()


async def stay(
    db_session: AsyncSession,
    *,
    bbox: list[int],
    at: dt.datetime,
    seconds: float = 0,
    anon_id: str = "P-001",
    tenant_id: str = T,
    step: float = 5.0,
) -> None:
    """Detections spread across a stay, the way a camera actually produces them.

    A single detection per zone was enough before the session-hygiene work; it
    is not now, and that is the point rather than an inconvenience. A zone must
    be held for `tracker_zone_confirm_seconds` to be believed, and a track that
    goes quiet for `tracker_dropout_seconds` has its visit closed. Both are true
    of the real signal at 20fps and neither is true of one lonely detection, so
    the tests feed the shape production sees.
    """
    t = at
    end = at + dt.timedelta(seconds=seconds)
    while True:
        await detect(db_session, bbox=bbox, at=t, anon_id=anon_id, tenant_id=tenant_id)
        if t >= end:
            break
        t = min(t + dt.timedelta(seconds=step), end)


async def session_ended(db_session: AsyncSession, *, at: dt.datetime) -> None:
    """The operator closing the activation, as the dashboard now emits it."""
    await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(),
            tenant_id=T,
            session_id=S,
            type="session.ended",
            payload={"endedBy": "u_test"},
            occurred_at=at,
        ),
    )
    await db_session.commit()


async def types_in_log(db_session: AsyncSession, tenant_id: str = T) -> list[str]:
    rows = await repository.read_events(
        db_session, tenant_id=tenant_id, since_seq=0, limit=100
    )
    return [r.type for r in rows]


# ── derived event ids ─────────────────────────────────────────────────────────
#
# These run without a database, so they fail fast and legibly. The pipeline
# tests below also depend on derive_event_id, but they fail with an opaque
# "nothing was produced" because the consumer catches the error and dead-letters
# it — these say what is actually wrong.


def test_derived_id_is_stable() -> None:
    """Same inputs, same id — across calls, processes, restarts, machines.

    This is the property the whole replay guarantee rests on.
    """
    a = derive_event_id("tracker", "t_test", "s", "spatial.dwell", "P-001", "z_left")
    b = derive_event_id("tracker", "t_test", "s", "spatial.dwell", "P-001", "z_left")
    assert a == b
    assert isinstance(a, uuid.UUID)


def test_derived_id_differs_on_different_input() -> None:
    """Different causes must not collide, or two real events become one and a
    genuine dwell silently disappears."""
    base = ("tracker", "t_test", "s", "spatial.dwell", "P-001")
    assert derive_event_id(*base, "z_left") != derive_event_id(*base, "z_right")
    assert derive_event_id(*base, "z_left") != derive_event_id(
        "tracker", "t_other", "s", "spatial.dwell", "P-001", "z_left"
    )


def test_derived_id_accepts_ints() -> None:
    """Callers pass a mix of strings and ints (seq numbers, timestamps)."""
    assert derive_event_id("tracker", 42) == derive_event_id("tracker", 42)


# ── tracker ───────────────────────────────────────────────────────────────────


async def test_entering_a_zone_emits_zone_enter(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    await seed_zones(graph_session)
    await stay(db_session, bbox=LEFT_PX, at=BASE, seconds=5)

    await TrackerConsumer().run_once()

    kinds = await types_in_log(db_session)
    assert kinds.count("spatial.zone_enter") == 1
    assert "spatial.zone_exit" not in kinds


async def test_moving_between_zones_emits_exit_dwell_enter(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """The full transition, with dwell carrying the duration of the stay."""
    await seed_zones(graph_session)
    await stay(db_session, bbox=LEFT_PX, at=BASE, seconds=45)
    await stay(db_session, bbox=RIGHT_PX, at=BASE + dt.timedelta(seconds=45), seconds=5)

    await TrackerConsumer().run_once()

    kinds = await types_in_log(db_session)
    assert kinds.count("spatial.zone_enter") == 2   # left, then right
    assert kinds.count("spatial.zone_exit") == 1
    assert kinds.count("spatial.dwell") == 1

    rows = await repository.read_events(
        db_session, tenant_id=T, since_seq=0, limit=100, type="spatial.dwell"
    )
    dwell = rows[0].payload
    assert dwell["zone_id"] == "z_left"
    assert dwell["duration"] == pytest.approx(45.0)
    # 45s is past the 30s threshold from agents/definitions/dwell.ts
    assert dwell["exceeded_threshold"] is True


async def test_staying_still_emits_nothing_further(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """A person who hasn't moved zones is not news. Without this the tracker
    would emit an enter per frame — 20+ per second per person."""
    await seed_zones(graph_session)
    for i in range(5):
        await detect(db_session, bbox=LEFT_PX, at=BASE + dt.timedelta(seconds=i))

    await TrackerConsumer().run_once()

    kinds = await types_in_log(db_session)
    assert kinds.count("spatial.zone_enter") == 1


# ── session hygiene ───────────────────────────────────────────────────────────
#
# Ported from the postgres-track's spatial-deriver.ts, whose header cites CHI '26:
# 71% of raw sessions are invalid without these. Every failure they prevent looks
# like ordinary data downstream, which is why each needs a test that would catch
# the heuristic being removed.


async def test_boundary_flicker_does_not_become_visits(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """Someone loitering on a zone edge is one visitor, not twenty visits.

    Frames wobbling across the boundary in runs of two, 100ms apart — every run
    far inside the confirm window. Untreated this is the single worst corruption
    of the report: each wobble is a complete, plausible enter/exit/dwell triple,
    so one person standing in a doorway inflates footfall, halves average dwell,
    and leaves nothing in the data to suggest anything went wrong.

    The runs of two matter. With single alternating frames no zone is ever seen
    twice in a row, so nothing confirms even with the window switched off and the
    test would pass against the very bug it exists to catch. Pairs are the
    weakest pattern that *does* confirm without a window — which makes this fail
    the moment `tracker_zone_confirm_seconds` goes to zero.
    """
    await seed_zones(graph_session)
    for i in range(12):
        await detect(
            db_session,
            bbox=LEFT_PX if (i // 2) % 2 == 0 else RIGHT_PX,
            at=BASE + dt.timedelta(milliseconds=100 * i),
        )

    await TrackerConsumer().run_once()

    kinds = await types_in_log(db_session)
    assert kinds.count("spatial.zone_enter") == 0
    assert kinds.count("spatial.dwell") == 0


async def test_a_confirmed_dwell_is_timed_from_the_real_crossing(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """The confirm window must not be charged to the visitor.

    If dwell were measured from the moment we *believed* the entry rather than
    from the crossing itself, every visit in every report would be short by the
    confirm window. Small per visit, systematic across a session, and always in
    the direction that understates the client's results.
    """
    await seed_zones(graph_session)
    await stay(db_session, bbox=LEFT_PX, at=BASE, seconds=30, step=1)
    await stay(db_session, bbox=RIGHT_PX, at=BASE + dt.timedelta(seconds=30), seconds=2)

    await TrackerConsumer().run_once()

    rows = await repository.read_events(
        db_session, tenant_id=T, since_seq=0, limit=100, type="spatial.dwell"
    )
    # Exactly 30 — not 29.4, which is what timing from the confirmation gives.
    assert rows[0].payload["duration"] == pytest.approx(30.0)


async def test_clipping_a_corner_is_not_a_dwell(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """A sub-second stay is noise. The exit still fires — they did leave."""
    await seed_zones(graph_session)
    # Held just long enough to confirm, then gone: a real crossing, but not a
    # visit anybody would defend calling one.
    await stay(db_session, bbox=LEFT_PX, at=BASE, seconds=0.7, step=0.7)
    await stay(db_session, bbox=RIGHT_PX, at=BASE + dt.timedelta(seconds=0.7), seconds=2)

    await TrackerConsumer().run_once()

    kinds = await types_in_log(db_session)
    assert kinds.count("spatial.zone_exit") == 1
    assert kinds.count("spatial.dwell") == 0


async def test_walking_out_of_frame_still_closes_the_visit(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """The under-reporting bug: a track that vanishes inside a zone.

    Before the dropout sweep this emitted no exit and no dwell at all, so the
    visit was simply lost — and the visits likeliest to end this way are the long
    ones at the far end of the booth. The activation therefore under-reported
    exactly the engagement it most wanted to prove.

    The visit is closed at the last sighting, not at the moment we noticed, and
    marked so a consumer can weigh it knowingly.
    """
    await seed_zones(graph_session)
    await stay(db_session, bbox=LEFT_PX, at=BASE, seconds=60, step=5)
    # Gone. Somebody else keeps the session alive so the sweep has a clock.
    await stay(
        db_session,
        bbox=RIGHT_PX,
        at=BASE + dt.timedelta(seconds=120),
        seconds=5,
        anon_id="P-002",
    )

    await TrackerConsumer().run_once()

    rows = await repository.read_events(
        db_session, tenant_id=T, since_seq=0, limit=200, type="spatial.dwell"
    )
    dropout = [r for r in rows if r.payload["anon_id"] == "P-001"]
    assert len(dropout) == 1
    assert dropout[0].payload["reason"] == "dropout"
    # 60s — the stay up to the last sighting, not the 120s until we noticed.
    assert dropout[0].payload["duration"] == pytest.approx(60.0)


async def test_a_person_still_being_seen_is_never_dropped_out(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """The sweep must not close a visit that is plainly still happening."""
    await seed_zones(graph_session)
    await stay(db_session, bbox=LEFT_PX, at=BASE, seconds=120, step=5)

    await TrackerConsumer().run_once()

    kinds = await types_in_log(db_session)
    assert kinds.count("spatial.zone_exit") == 0
    assert kinds.count("spatial.dwell") == 0


# ── pass-by (roi-framework.md §2, Reach) ──────────────────────────────────────
#
# The negative signal: came close, chose not to engage. It is the only metric in
# the catalogue that can make an activation look *worse*, which is exactly why a
# client believes the rest of them.


def test_distance_to_a_segment_is_clamped_to_its_ends() -> None:
    """Past the end of an edge, the distance is to the corner — not to the
    imaginary continuation of the line.

    Without the clamp, somebody standing well beyond the corner of a stand
    measures as though the wall carried on forever, and reads as adjacent to a
    zone they are nowhere near.
    """
    # Segment along y=0 from x=0 to x=1. A point past the right end at (2, 0)
    # is 1.0 from the corner, not 0.0 from the extended line.
    assert zones_geom.dist_to_segment(2.0, 0.0, 0.0, 0.0, 1.0, 0.0) == pytest.approx(1.0)
    # Directly above the middle: perpendicular distance.
    assert zones_geom.dist_to_segment(0.5, 0.3, 0.0, 0.0, 1.0, 0.0) == pytest.approx(0.3)


def test_distance_to_a_polygon_is_zero_inside_it() -> None:
    """Inside is not "negative depth" — pass-by does not ask how far in."""
    assert zones_geom.dist_to_polygon(0.25, 0.5, LEFT_POLY) == 0.0
    # The right half starts at x=0.5, so x=0.45 is 0.05 clear of it.
    assert zones_geom.dist_to_polygon(0.45, 0.5, RIGHT_POLY) == pytest.approx(0.05)


async def test_coming_close_without_entering_emits_a_passby(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """The signal itself: near z_right the whole time, never in it."""
    await seed_zones(graph_session)
    # centroid x = 450/1000 = 0.45 → inside z_left, 0.05 from z_right, which is
    # inside the 0.08 pass-by radius.
    await stay(db_session, bbox=NEAR_PX, at=BASE, seconds=30)
    # Somebody else keeps the session's clock moving so the sweep can run.
    await stay(
        db_session,
        bbox=LEFT_PX,
        at=BASE + dt.timedelta(seconds=90),
        seconds=5,
        anon_id="P-002",
    )

    await TrackerConsumer().run_once()

    rows = await repository.read_events(
        db_session, tenant_id=T, since_seq=0, limit=200, type="spatial.passby"
    )
    assert len(rows) == 1
    assert rows[0].payload["anon_id"] == "P-001"
    assert rows[0].payload["adjacent_zone_id"] == "z_right"
    assert rows[0].payload["closest_dist"] == pytest.approx(0.05, abs=0.001)


async def test_someone_who_never_enters_any_zone_is_still_finalised(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """The case the dropout sweep used to exclude by construction.

    The sweep once skipped any track with no confirmed zone — but a pass-by
    subject never confirms one, because not going in is the entire signal. So
    the guard excluded precisely the people it now exists to report. Here the
    only zone is the right half and the visitor loiters outside it, in no zone
    at all: under the old rule they produced nothing whatsoever.
    """
    await graph_repo.upsert_session(
        graph_session, tenant_id=T, session_id=S, venue="Test Hall"
    )
    await graph_repo.upsert_zone(
        graph_session, tenant_id=T, session_id=S,
        zone_id="z_right", name="Lounge", type="lounge", polygon=RIGHT_POLY,
    )

    await stay(db_session, bbox=NEAR_PX, at=BASE, seconds=30)
    await stay(
        db_session,
        bbox=LEFT_PX,
        at=BASE + dt.timedelta(seconds=90),
        seconds=5,
        anon_id="P-002",
    )

    await TrackerConsumer().run_once()

    rows = await repository.read_events(
        db_session, tenant_id=T, since_seq=0, limit=200, type="spatial.passby"
    )
    assert [r.payload["anon_id"] for r in rows] == ["P-001"]
    # Never in a zone, so nothing to exit and nothing to dwell in.
    kinds = await types_in_log(db_session)
    assert "spatial.zone_enter" not in kinds
    assert "spatial.dwell" not in kinds


async def test_entering_a_zone_is_never_also_a_passby(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """Going in is the opposite of skipping.

    Someone who circles a stand and then walks in engaged with it. Counting them
    as a pass-by as well would make one person both the success and the failure,
    and every zone's skip rate would be inflated by its own visitors.
    """
    await seed_zones(graph_session)
    await stay(db_session, bbox=NEAR_PX, at=BASE, seconds=20)
    await stay(db_session, bbox=RIGHT_PX, at=BASE + dt.timedelta(seconds=20), seconds=20)
    await stay(
        db_session,
        bbox=LEFT_PX,
        at=BASE + dt.timedelta(seconds=90),
        seconds=5,
        anon_id="P-002",
    )

    await TrackerConsumer().run_once()

    rows = await repository.read_events(
        db_session, tenant_id=T, since_seq=0, limit=200, type="spatial.passby"
    )
    assert [r.payload["adjacent_zone_id"] for r in rows] == []


async def test_staying_well_clear_of_a_zone_is_not_a_passby(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """0.25 away is not "passed by". Without a radius every visitor would be a
    pass-by for every zone they didn't visit, and the signal would mean nothing."""
    await seed_zones(graph_session)
    await stay(db_session, bbox=LEFT_PX, at=BASE, seconds=30)
    await stay(
        db_session,
        bbox=LEFT_PX,
        at=BASE + dt.timedelta(seconds=90),
        seconds=5,
        anon_id="P-002",
    )

    await TrackerConsumer().run_once()

    kinds = await types_in_log(db_session)
    assert "spatial.passby" not in kinds


async def test_a_second_approach_does_not_double_count(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """Somebody pacing outside a stand is one person who declined it, not twelve.

    The derived id is keyed on (person, zone) for the whole session, so a second
    close-out dedupes in the log rather than adding a second skip.
    """
    await seed_zones(graph_session)
    await stay(db_session, bbox=NEAR_PX, at=BASE, seconds=20)
    # Long enough away to be finalised, then back again.
    await stay(db_session, bbox=NEAR_PX, at=BASE + dt.timedelta(seconds=120), seconds=20)
    await stay(
        db_session,
        bbox=LEFT_PX,
        at=BASE + dt.timedelta(seconds=240),
        seconds=5,
        anon_id="P-002",
    )

    await TrackerConsumer().run_once()

    rows = await repository.read_events(
        db_session, tenant_id=T, since_seq=0, limit=300, type="spatial.passby"
    )
    assert len(rows) == 1


# ── session end ───────────────────────────────────────────────────────────────


async def test_ending_a_session_closes_everyone_still_being_tracked(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """The tail of every activation.

    Whoever is in the room when the doors shut is never closed out otherwise —
    no final dwell, no pass-by — because the dropout sweep needs a later event
    to run on and there are no more detections coming. On a short session that
    is a large share of the audience, and the report would simply be smaller
    than the day was, with nothing to show the gap.
    """
    await seed_zones(graph_session)
    await stay(db_session, bbox=NEAR_PX, at=BASE, seconds=45)

    before = await types_in_log(db_session)
    assert "spatial.dwell" not in before, "still in the room — nothing closed yet"

    await session_ended(db_session, at=BASE + dt.timedelta(seconds=50))
    await TrackerConsumer().run_once()

    rows = await repository.read_events(
        db_session, tenant_id=T, since_seq=0, limit=200, type="spatial.dwell"
    )
    assert len(rows) == 1
    assert rows[0].payload["reason"] == "session_end"
    assert rows[0].payload["duration"] == pytest.approx(45.0)

    passbys = await repository.read_events(
        db_session, tenant_id=T, since_seq=0, limit=200, type="spatial.passby"
    )
    assert [r.payload["adjacent_zone_id"] for r in passbys] == ["z_right"]


async def test_detection_without_frame_dimensions_is_dead_lettered(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """Pixel bboxes cannot be compared to normalized polygons without the frame
    size. A detection missing it is unprocessable, so it must park rather than
    wedge the pipeline."""
    await seed_zones(graph_session)
    await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(), tenant_id=T, session_id=S,
            type="perception.detection",
            payload={"person_id": "P-001", "bbox": LEFT_PX},  # no frame_width
            occurred_at=BASE,
        ),
    )
    await db_session.commit()

    await TrackerConsumer().run_once()

    parked = await repository.list_dead_letters(db_session, consumer="tracker")
    assert len(parked) == 1
    assert "frame_width" in parked[0].error

    # and the cursor moved past it rather than retrying forever
    cursor = await repository.get_cursor(db_session, consumer="tracker", tenant_id=T)
    assert cursor > 0


# ── replay ────────────────────────────────────────────────────────────────────


async def test_replay_is_a_no_op(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """THE test for this item.

    Reset the cursor and run the whole pipeline again — exactly what happens
    after a crash, or when an operator replays per event-bus-spec.md §5. Nothing
    may be duplicated: not events, not nodes, not edges.

    This only holds because the tracker derives its event_ids. With random ids
    the second run would append a parallel set of spatial events and every dwell
    would be counted twice, silently, in the ROI numbers.
    """
    await seed_zones(graph_session)
    await stay(db_session, bbox=LEFT_PX, at=BASE, seconds=45)
    await stay(db_session, bbox=RIGHT_PX, at=BASE + dt.timedelta(seconds=45), seconds=5)

    tracker, writer = TrackerConsumer(), GraphWriterConsumer()
    await tracker.run_once()
    await writer.run_once()

    before_events = len(await types_in_log(db_session))
    before_edges = await _edge_count(graph_session)
    before_people = await graph_repo.people_in_session(
        graph_session, tenant_id=T, session_id=S
    )

    # Guard the guard: comparing zero to zero would "pass" while proving
    # nothing. Assert the pipeline actually produced something before checking
    # that replaying it produces nothing more.
    kinds = await types_in_log(db_session)
    assert kinds.count("spatial.dwell") == 1, "pipeline produced no dwell to duplicate"
    assert before_edges >= 3, f"expected ENTERED+LEFT+DWELLED_IN edges, got {before_edges}"
    assert before_people == 1

    # rewind both consumers to the beginning and run the lot again
    for name in ("tracker", "graph_writer"):
        await repository.reset_cursor(db_session, consumer=name, tenant_id=T, to_seq=0)
    await db_session.commit()

    await TrackerConsumer().run_once()   # fresh instance: no in-memory state
    await GraphWriterConsumer().run_once()

    assert len(await types_in_log(db_session)) == before_events, "events duplicated"
    assert await _edge_count(graph_session) == before_edges, "edges duplicated"
    assert (
        await graph_repo.people_in_session(graph_session, tenant_id=T, session_id=S)
        == before_people
    ), "people duplicated"


async def test_replay_with_a_live_consumer_is_also_a_no_op(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """The same rewind, but against consumers that are already running.

    This is what the live service does, and it is strictly harder than the test
    above: those consumers keep derived in-memory state. The tracker remembers
    which zone each person is in, so on a rewind the first replayed detection
    looks like a move *backwards* and it emits an exit and dwell for a journey
    nobody made.

    Deriving event ids does not help — the spurious events have genuinely
    different causes, so they get different ids and the bus stores them happily.
    The fix is Consumer.on_replay(), which drops the stale state.

    Regression test for a bug the fresh-instance version above could not catch.
    """
    await seed_zones(graph_session)
    await stay(db_session, bbox=LEFT_PX, at=BASE, seconds=45)
    await stay(db_session, bbox=RIGHT_PX, at=BASE + dt.timedelta(seconds=45), seconds=5)

    tracker, writer = TrackerConsumer(), GraphWriterConsumer()
    await tracker.run_once()
    await writer.run_once()

    before_events = len(await types_in_log(db_session))
    before_edges = await _edge_count(graph_session)
    assert before_edges >= 3

    for name in ("tracker", "graph_writer"):
        await repository.reset_cursor(db_session, consumer=name, tenant_id=T, to_seq=0)
    await db_session.commit()

    # the SAME instances, state and all — no fresh construction
    await tracker.run_once()
    await writer.run_once()

    assert len(await types_in_log(db_session)) == before_events, (
        "a live consumer duplicated events on replay — stale in-memory state"
    )
    assert await _edge_count(graph_session) == before_edges, "edges duplicated"


async def _edge_count(graph_session: GraphSession) -> int:
    result = await graph_session.run(
        "MATCH (:Person {tenant_id: $t})-[r]->(:Zone {tenant_id: $t}) "
        "RETURN count(r) AS edges",
        t=T,
    )
    record = await result.single()
    return record["edges"]


# ── graph writer ──────────────────────────────────────────────────────────────


async def test_graph_writer_builds_person_and_edges(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    await seed_zones(graph_session)
    await stay(db_session, bbox=LEFT_PX, at=BASE, seconds=45)
    await stay(db_session, bbox=RIGHT_PX, at=BASE + dt.timedelta(seconds=45), seconds=5)

    await TrackerConsumer().run_once()
    await GraphWriterConsumer().run_once()

    assert await graph_repo.people_in_session(
        graph_session, tenant_id=T, session_id=S
    ) == 1

    rows = await graph_repo.dwell_by_zone(graph_session, tenant_id=T, session_id=S)
    assert len(rows) == 1
    assert rows[0]["zone"] == "Entrance"
    assert rows[0]["avg_dwell"] == pytest.approx(45.0)


async def test_consumers_are_tenant_isolated(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """multi-tenant.md §2 — one tenant's detections must never reach another's
    graph, even though both are processed by the same loop."""
    await seed_zones(graph_session, tenant_id=T)
    await seed_zones(graph_session, tenant_id=OTHER)
    from tests.conftest import as_tenant

    await detect(db_session, bbox=LEFT_PX, at=BASE, tenant_id=T)
    # Writing another tenant's row requires acting as them — RLS rejects it
    # otherwise, which is the point.
    await as_tenant(db_session, OTHER)
    await detect(db_session, bbox=LEFT_PX, at=BASE, tenant_id=OTHER, anon_id="P-999")
    await as_tenant(db_session, T)

    await TrackerConsumer().run_once()
    await GraphWriterConsumer().run_once()

    assert await graph_repo.people_in_session(
        graph_session, tenant_id=T, session_id=S
    ) == 1
    assert await graph_repo.people_in_session(
        graph_session, tenant_id=OTHER, session_id=S
    ) == 1

    result = await graph_session.run(
        "MATCH (p:Person {tenant_id: $t}) RETURN collect(p.anon_id) AS ids", t=T
    )
    record = await result.single()
    assert record["ids"] == ["P-001"]


# ── cursors ───────────────────────────────────────────────────────────────────


async def test_cursor_tolerates_gaps_in_seq(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """seq is ordered but not contiguous (event-bus-spec.md §2). A deduped
    insert burns a value, so a consumer must poll `seq > cursor`, never
    `cursor + 1` — this test creates a real gap and proves nothing stalls."""
    await seed_zones(graph_session)

    duplicate_id = uuid.uuid4()
    event = EventIn(
        event_id=duplicate_id, tenant_id=T, session_id=S,
        type="perception.detection",
        payload={"person_id": "P-001", "bbox": LEFT_PX,
                 "frame_width": FRAME_W, "frame_height": FRAME_H},
        occurred_at=BASE,
    )
    await repository.append_event(db_session, event)
    await repository.append_event(db_session, event)  # deduped — burns a seq
    await detect(db_session, bbox=RIGHT_PX, at=BASE + dt.timedelta(seconds=10))

    seqs = [r.seq for r in await repository.read_events(
        db_session, tenant_id=T, since_seq=0, limit=100
    )]
    assert seqs != list(range(seqs[0], seqs[0] + len(seqs))), "expected a gap in seq"

    consumed = await TrackerConsumer().run_once()
    assert consumed > 0
    cursor = await repository.get_cursor(db_session, consumer="tracker", tenant_id=T)
    assert cursor == max(seqs)


async def test_cursor_never_moves_backwards(db_session: AsyncSession) -> None:
    """advance_cursor uses GREATEST so two loops can't drag a cursor back and
    re-deliver events that were already handled."""
    await repository.advance_cursor(
        db_session, consumer="tracker", tenant_id=T, last_seq=100
    )
    await repository.advance_cursor(
        db_session, consumer="tracker", tenant_id=T, last_seq=50
    )
    await db_session.commit()
    assert await repository.get_cursor(
        db_session, consumer="tracker", tenant_id=T
    ) == 100


# ── hardening (Phase 1 review findings) ───────────────────────────────────────


def test_event_type_must_be_in_a_known_namespace() -> None:
    """An unknown namespace is rejected rather than stored forever in an
    append-only table where no consumer will ever match it.

    Note the limit of this check: it catches a bad *namespace*, not a typo in
    the suffix — "spatial.zone_entr" still starts with "spatial." and passes.
    See the note in schemas.py.
    """
    import pydantic

    for bad in ("spatal.zone_enter", "detection", "evil.injected"):
        with pytest.raises(pydantic.ValidationError, match="unknown event namespace"):
            EventIn(
                event_id=uuid.uuid4(), tenant_id=T, session_id=S,
                type=bad, payload={}, occurred_at=BASE,
            )


def test_rfid_read_is_accepted() -> None:
    """Week 1 task 1.11 emits rfid.read. It was in no contract at all, so the
    namespace check rejected it and POD 1 would have hit a 422 on their first
    request with nothing to explain it."""
    e = EventIn(
        event_id=uuid.uuid4(), tenant_id=T, session_id=S, type="rfid.read",
        payload={"reader_id": "r1", "tag_id": "tag-abc"}, occurred_at=BASE,
    )
    assert e.type == "rfid.read"


def test_new_types_in_a_known_namespace_are_accepted() -> None:
    """§3 says the taxonomy is additive-only, so the check is a namespace
    prefix, not an allow-list — a type nothing implements yet must still pass."""
    e = EventIn(
        event_id=uuid.uuid4(), tenant_id=T, session_id=S,
        type="spatial.passby",  # P2, no producer or consumer today
        payload={}, occurred_at=BASE,
    )
    assert e.type == "spatial.passby"


async def test_zone_cache_expires_so_redrawn_zones_take_effect(
    db_session: AsyncSession, graph_session: GraphSession, monkeypatch
) -> None:
    """Operators redraw zones mid-session. An unexpiring cache would keep
    scoring detections against the old polygons until a restart, silently
    attributing dwell to the wrong zone."""
    await seed_zones(graph_session)
    tracker = TrackerConsumer()

    first = await tracker.zones_for(T, S)
    assert {z["id"] for z in first} == {"z_left", "z_right"}

    # operator adds a zone
    await graph_repo.upsert_zone(
        graph_session, tenant_id=T, session_id=S, zone_id="z_new",
        name="Demo Pod", type="demo", polygon=[[0.0, 0.0], [0.1, 0.0], [0.1, 0.1]],
    )

    # still cached — the whole point of the cache
    assert {z["id"] for z in await tracker.zones_for(T, S)} == {"z_left", "z_right"}

    # once the TTL lapses, the new zone appears without a restart
    # capture the real clock first — patching with a lambda that calls the
    # patched name recurses forever
    real_monotonic = time.monotonic
    monkeypatch.setattr(
        "app.consumers.tracker.time.monotonic", lambda: real_monotonic() + 3600
    )
    assert "z_new" in {z["id"] for z in await tracker.zones_for(T, S)}


async def test_tracked_people_are_bounded(
    db_session: AsyncSession, graph_session: GraphSession, monkeypatch
) -> None:
    """Position state only shrank when someone left every zone, so anyone whose
    last known position was inside one leaked for the life of the process."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "tracker_max_tracked_people", 3)
    await seed_zones(graph_session)

    for i in range(10):
        await detect(db_session, bbox=LEFT_PX, at=BASE, anon_id=f"P-{i:03d}")
    tracker = TrackerConsumer()
    await tracker.run_once()

    assert len(tracker._where) <= 3
