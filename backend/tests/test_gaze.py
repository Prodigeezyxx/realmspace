"""
Gaze, and the head turns it must not report as interest.

**The refusals are the file.** A monocular camera gives a facing direction, not
a gaze vector, and a detector that answers on every frame turns the commonest
thing a person does with their head into a finding. That is the failure
`detection_rate` drift was left unbuilt for and the one grouping refuses when it
declines to read proximity as company; the tests below name each way this one
says nothing.

The positives exist to show the refusals are not just a detector that never
fires.

Runs against real Postgres and real Neo4j, in `t_test`.
"""

from __future__ import annotations

import datetime as dt
import math
import uuid
from collections.abc import AsyncIterator

import pytest
from neo4j import AsyncSession as GraphSession
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.consumers.gaze import GazeConsumer
from app.graph import repository as graph_repo
from app.schemas import EventIn
from tests.conftest import as_tenant

T = "t_test"
S = "s_gaze"

BASE = dt.datetime(2026, 8, 28, 10, 0, 0, tzinfo=dt.timezone.utc)
FRAME_W, FRAME_H = 1000, 1000

#: A stand on the right of the frame, and one further right again behind it.
NEAR_POLY = [[0.50, 0.40], [0.60, 0.40], [0.60, 0.60], [0.50, 0.60]]
FAR_POLY = [[0.80, 0.40], [0.90, 0.40], [0.90, 0.60], [0.80, 0.60]]
#: Nearer than either, and deliberately owned by the *other* camera in the
#: cross-camera test. It has to be the closest thing along the ray, or the
#: nearest-hit rule would exclude it on its own and the test would pass whether
#: the camera filter existed or not — which is exactly what the first version
#: of this file did.
OTHER_CAM_POLY = [[0.30, 0.40], [0.40, 0.40], [0.40, 0.60], [0.30, 0.60]]
#: Where the visitor stands, on the left, well clear of both.
STAND_POLY = [[0.05, 0.40], [0.20, 0.40], [0.20, 0.60], [0.05, 0.60]]

RIGHT = 0.0            # radians, +x — towards the stands
LEFT = math.pi         # away from them
GOOD = 0.9             # comfortably over gaze_min_confidence (0.45)


@pytest.fixture(autouse=True)
async def _scope_to_test_tenant(db_session: AsyncSession) -> AsyncIterator[None]:
    await as_tenant(db_session, T)
    yield


async def seed(graph_session: GraphSession, *, cameras: bool = False) -> None:
    await graph_repo.upsert_session(
        graph_session, tenant_id=T, session_id=S, venue="Gaze Hall"
    )
    zones = [
        ("z_stand", "Standing room", STAND_POLY, "cam-1" if cameras else None),
        ("z_near", "Product Pod", NEAR_POLY, "cam-1" if cameras else None),
        ("z_far", "Sponsor Wall", FAR_POLY, None),
    ]
    if cameras:
        zones.append(("z_other", "Other camera's stand", OTHER_CAM_POLY, "cam-2"))

    for zone_id, name, poly, cam in zones:
        await graph_repo.upsert_zone(
            graph_session, tenant_id=T, session_id=S,
            zone_id=zone_id, name=name, type="other", polygon=poly,
            **({"camera_id": cam} if cam else {}),
        )


async def look(
    db_session: AsyncSession,
    *,
    at: float,
    heading: float | None = RIGHT,
    confidence: float = GOOD,
    anon_id: str = "P-001",
    camera_id: str | None = None,
    nx: float = 0.12,
) -> None:
    """One detection of somebody standing in `z_stand`, facing `heading`."""
    half = 20
    cx, cy = nx * FRAME_W, 0.5 * FRAME_H
    payload: dict = {
        "anon_id": anon_id,
        "bbox": [cx - half, cy - half, cx + half, cy + half],
        "confidence": 0.9,
        "frame_id": int(at * 10),
        "frame_width": FRAME_W,
        "frame_height": FRAME_H,
    }
    if heading is not None:
        payload["heading"] = heading
        payload["heading_confidence"] = confidence
    if camera_id is not None:
        payload["camera_id"] = camera_id

    await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(), tenant_id=T, session_id=S,
            type="perception.detection", payload=payload,
            occurred_at=BASE + dt.timedelta(seconds=at),
        ),
    )


async def end(db_session: AsyncSession, *, at: float) -> None:
    await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(), tenant_id=T, session_id=S,
            type="session.ended", payload={"ended_by": "test"},
            occurred_at=BASE + dt.timedelta(seconds=at),
        ),
    )


async def gazes(db_session: AsyncSession, consumer: GazeConsumer) -> list[dict]:
    await db_session.commit()
    await consumer.run_once()
    rows = await repository.read_events(
        db_session, tenant_id=T, session_id=S, type="spatial.gaze", limit=100
    )
    return [r.payload for r in rows]


# ── what it reports ──────────────────────────────────────────────────────────


async def test_a_held_look_is_reported(db_session: AsyncSession, graph_session: GraphSession):
    """Somebody stands still and studies the pod for four seconds."""
    await seed(graph_session)
    for i in range(5):
        await look(db_session, at=float(i))
    await end(db_session, at=6)

    found = await gazes(db_session, GazeConsumer())

    assert len(found) == 1
    assert found[0]["anon_id"] == "P-001"
    assert found[0]["target_id"] == "z_near"
    assert found[0]["duration"] == pytest.approx(4.0, abs=0.01)
    assert found[0]["confidence"] == pytest.approx(GOOD, abs=0.001)


async def test_the_nearer_of_two_zones_wins(db_session: AsyncSession, graph_session: GraphSession):
    """A product pod in front of a sponsor wall. They are looking at the pod.

    Both polygons lie along the same line of sight, which is an ordinary booth
    layout rather than a contrivance, and picking by draw order rather than by
    distance would put the visitor's attention on the wall behind.
    """
    await seed(graph_session)
    for i in range(5):
        await look(db_session, at=float(i))
    await end(db_session, at=6)

    found = await gazes(db_session, GazeConsumer())
    assert [g["target_id"] for g in found] == ["z_near"]


async def test_a_look_ends_when_they_turn_away(db_session: AsyncSession, graph_session: GraphSession):
    """The duration measures the held look, not the gap between detections."""
    await seed(graph_session)
    for i in range(4):
        await look(db_session, at=float(i))
    for i in range(4, 8):
        await look(db_session, at=float(i), heading=LEFT)

    found = await gazes(db_session, GazeConsumer())

    assert len(found) == 1
    assert found[0]["duration"] == pytest.approx(3.0, abs=0.01)
    assert found[0]["reason"] == "no_target"


async def test_somebody_still_looking_at_close_is_counted(
    db_session: AsyncSession, graph_session: GraphSession
):
    """The gap `session.ended` closed for dwell, closed here too.

    Without it, the visitors actually looking at something when the doors shut
    are the ones whose attention is never counted.
    """
    await seed(graph_session)
    for i in range(5):
        await look(db_session, at=float(i))
    await end(db_session, at=5)

    found = await gazes(db_session, GazeConsumer())
    assert [g["reason"] for g in found] == ["session_end"]


# ── what it refuses, which is the point ──────────────────────────────────────


async def test_a_glance_is_not_interest(db_session: AsyncSession, graph_session: GraphSession):
    """Held for less than `gaze_min_seconds`. Nothing is emitted at all.

    This is the commonest thing a head does. Reporting it would make the
    baseline behaviour of every visitor into a finding.
    """
    await seed(graph_session)
    await look(db_session, at=0.0)
    await look(db_session, at=0.4)
    await look(db_session, at=1.0, heading=LEFT)
    await end(db_session, at=3)

    assert await gazes(db_session, GazeConsumer()) == []


async def test_a_low_confidence_heading_is_not_used(
    db_session: AsyncSession, graph_session: GraphSession
):
    """Below the floor the heading is a guess about a skeleton nobody saw."""
    await seed(graph_session)
    for i in range(5):
        await look(db_session, at=float(i), confidence=0.2)
    await end(db_session, at=6)

    assert await gazes(db_session, GazeConsumer()) == []


async def test_a_detection_with_no_heading_says_nothing(
    db_session: AsyncSession, graph_session: GraphSession
):
    """A detect-only model, or a skeleton that did not settle the question.

    Perception already declined; this must not invent an answer from the box.
    It is also what every deployment running the old model looks like, so the
    absence has to be silent rather than a dead letter.
    """
    await seed(graph_session)
    for i in range(5):
        await look(db_session, at=float(i), heading=None)
    await end(db_session, at=6)

    assert await gazes(db_session, GazeConsumer()) == []


async def test_a_ray_that_hits_nothing_reports_nothing(
    db_session: AsyncSession, graph_session: GraphSession
):
    """Facing an empty part of the room is not attention on anything."""
    await seed(graph_session)
    for i in range(5):
        await look(db_session, at=float(i), heading=LEFT)
    await end(db_session, at=6)

    assert await gazes(db_session, GazeConsumer()) == []


async def test_the_zone_they_stand_in_is_never_the_target(
    db_session: AsyncSession, graph_session: GraphSession
):
    """Looking at the floor you are on is not attention.

    A ray from inside a polygon always crosses that polygon on the way out, so
    without the exclusion every gaze in the booth would land on the zone the
    visitor already occupies — and dwell already measures that.
    """
    await seed(graph_session)
    for i in range(5):
        await look(db_session, at=float(i), heading=LEFT, nx=0.19)
    await end(db_session, at=6)

    found = await gazes(db_session, GazeConsumer())
    assert [g["target_id"] for g in found] == []


async def test_a_ray_is_never_cast_into_another_cameras_zones(
    db_session: AsyncSession, graph_session: GraphSession
):
    """The pair of tests that differ in one thing, as grouping's do.

    A zone polygon is normalized within *one* camera's frame, so the same
    coordinates name different floor in a different camera. `z_other` is the
    **closest** thing along this ray and belongs to `cam-2`; the visitor is seen
    by `cam-1`. Without the filter the nearest-hit rule would hand them a look
    at a stand this camera never saw.

    The nearness matters. A first version put the other camera's zone furthest
    away, where nearest-hit excluded it anyway — so the test passed with the
    filter deleted, which is a test that watches nothing.
    """
    await seed(graph_session, cameras=True)
    for i in range(5):
        await look(db_session, at=float(i), camera_id="cam-1")
    await end(db_session, at=6)

    found = await gazes(db_session, GazeConsumer())
    assert [g["target_id"] for g in found] == ["z_near"]


async def test_the_same_ray_does_find_it_when_the_camera_owns_it(
    db_session: AsyncSession, graph_session: GraphSession
):
    """The other half of the pair: identical walk, seen by `cam-2`.

    Same geometry, same heading, one thing changed — so the previous test is
    showing a filter working rather than a detector that finds nothing.
    """
    await seed(graph_session, cameras=True)
    for i in range(5):
        await look(db_session, at=float(i), camera_id="cam-2")
    await end(db_session, at=6)

    found = await gazes(db_session, GazeConsumer())
    assert [g["target_id"] for g in found] == ["z_other"]


async def test_the_same_look_replays_to_the_same_id(
    db_session: AsyncSession, graph_session: GraphSession
):
    """A replay must dedupe rather than count one look twice."""
    await seed(graph_session)
    for i in range(5):
        await look(db_session, at=float(i))
    await end(db_session, at=6)

    first = await gazes(db_session, GazeConsumer())
    assert len(first) == 1

    await repository.reset_cursor(db_session, tenant_id=T, consumer="gaze", to_seq=0)
    # Committed before the consumer runs, or it blocks on the row lock this
    # session still holds.
    await db_session.commit()

    replayed = GazeConsumer()
    await replayed.on_replay(T)
    await replayed.run_once()

    assert await gazes(db_session, GazeConsumer()) == first


# ── the graph edge, and the query data-model.md has always shipped ───────────


async def test_the_look_reaches_the_graph(
    db_session: AsyncSession, graph_session: GraphSession
):
    """`(Person)-[:LOOKED_AT {duration, confidence, started_at}]->(Zone)`.

    A Zone rather than the `Object|Surface` data-model.md names, because a
    Surface carries a zone_id and no geometry — there is nothing in a frame to
    aim a ray at. The deviation is recorded in that file; this pins the shape.
    """
    from app.consumers.graph_writer import GraphWriterConsumer

    await seed(graph_session)
    for i in range(5):
        await look(db_session, at=float(i))
    await end(db_session, at=6)

    await gazes(db_session, GazeConsumer())
    await GraphWriterConsumer().run_once()

    rows = await graph_session.run(
        """
        MATCH (p:Person {tenant_id: $t, session_id: $s})-[r:LOOKED_AT]->(z:Zone)
        RETURN z.id AS zone, r.duration AS duration, r.confidence AS confidence
        """,
        t=T, s=S,
    )
    edges = [dict(row) async for row in rows]

    assert len(edges) == 1
    assert edges[0]["zone"] == "z_near"
    assert edges[0]["duration"] == pytest.approx(4.0, abs=0.01)
    assert edges[0]["confidence"] == pytest.approx(GOOD, abs=0.001)


async def test_writing_the_look_twice_draws_one_edge(
    db_session: AsyncSession, graph_session: GraphSession
):
    """The graph is a separate store from the log, so redelivery is guaranteed.

    Every other edge in `graph_repo` MERGEs on a stable key for this reason;
    this one keys on `started_at` like `DWELLED_IN`, so one person looking at
    the same zone twice is two edges and one event replayed is still one.
    """
    from app.consumers.graph_writer import GraphWriterConsumer

    await seed(graph_session)
    for i in range(5):
        await look(db_session, at=float(i))
    await end(db_session, at=6)

    await gazes(db_session, GazeConsumer())
    await GraphWriterConsumer().run_once()

    await repository.reset_cursor(db_session, tenant_id=T, consumer="graph_writer", to_seq=0)
    await db_session.commit()
    await GraphWriterConsumer().run_once()

    rows = await graph_session.run(
        "MATCH (:Person {tenant_id: $t, session_id: $s})-[r:LOOKED_AT]->(:Zone) RETURN count(r) AS n",
        t=T, s=S,
    )
    assert (await rows.single())["n"] == 1
