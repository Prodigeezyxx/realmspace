"""
Two cameras, and the ways they used to become one person.

Before this, `perception/realmspace.py` ran one ByteTrack instance per camera
and every instance numbers its first visitor `P-001`. Every consumer keyed on
that bare id, so two cameras' first visitors were one person: interleaved zone
transitions, merged dwells, one `(:Person)`. Nothing raised. The activation
under-reported its audience and over-reported engagement, and both numbers
looked ordinary.

The fix has two halves and each has a test here, because either alone is still
wrong:

  1. the track id is namespaced by camera (`consumers/ids.person_key`), and
  2. a zone belongs to the camera whose frame it was drawn in.

Without (2) a detection is still scored against another camera's polygons —
normalized 0..1 means something different in each frame — so a visitor lands in
a zone they were never in and passes by every zone they were never near.

`test_a_single_camera_session_replays_to_the_same_ids` is the load-bearing one.
The namespace has a fallback for detections with no `camera_id` — every event
logged before Phase 6 — and spatial event ids are derived from this value, so a
fallback that is off by one character means the bus stops recognising its own
history and a replay double-counts every dwell in it.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from neo4j import AsyncSession as GraphSession
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.consumers.graph_writer import GraphWriterConsumer
from app.consumers.grouping import GroupingConsumer
from app.consumers.ids import person_key
from app.consumers.tracker import TrackerConsumer
from app.graph import repository as graph_repo
from app.schemas import EventIn

T = "t_test"
S = "s_multicam"

BASE = dt.datetime(2026, 8, 21, 10, 0, 0, tzinfo=dt.timezone.utc)

# The same two halves of the frame test_consumers.py uses. Both cameras see
# these coordinates, which is exactly the problem: 0.25 is the left half of
# whichever frame it came from.
LEFT_POLY = [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]
RIGHT_POLY = [[0.5, 0.0], [1.0, 0.0], [1.0, 1.0], [0.5, 1.0]]

FRAME_W, FRAME_H = 1000, 1000
LEFT_PX = [200, 400, 300, 600]    # centroid 0.25 → the left zone
RIGHT_PX = [700, 400, 800, 600]   # centroid 0.75 → the right zone


@pytest.fixture(autouse=True)
async def _scope_to_test_tenant(db_session: AsyncSession):
    from tests.conftest import as_tenant

    await as_tenant(db_session, T)
    yield


async def seed(
    graph_session: GraphSession,
    *,
    cameras: list[str],
    zone_owners: dict[str, str | None],
) -> None:
    """A session with its cameras, and zones owned as the test needs them.

    `zone_owners` maps zone id → owning camera (or None for "every camera"),
    so each test states its own booth layout rather than sharing one.
    """
    await graph_repo.upsert_session(
        graph_session, tenant_id=T, session_id=S, venue="Two-Camera Hall"
    )
    for camera_id in cameras:
        await graph_repo.upsert_camera(
            graph_session, tenant_id=T, session_id=S, camera_id=camera_id
        )
    for zone_id, owner in zone_owners.items():
        await graph_repo.upsert_zone(
            graph_session,
            tenant_id=T,
            session_id=S,
            zone_id=zone_id,
            name=zone_id,
            type="other",
            polygon=LEFT_POLY if zone_id.endswith("left") else RIGHT_POLY,
            camera_id=owner,
        )


async def detect(
    db_session: AsyncSession,
    *,
    bbox: list[int],
    at: dt.datetime,
    anon_id: str = "P-001",
    camera_id: str | None = None,
) -> None:
    payload = {
        "anon_id": anon_id,
        "bbox": bbox,
        "confidence": 0.9,
        "frame_width": FRAME_W,
        "frame_height": FRAME_H,
    }
    if camera_id is not None:
        payload["camera_id"] = camera_id
    await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(),
            tenant_id=T,
            session_id=S,
            type="perception.detection",
            payload=payload,
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
    camera_id: str | None = None,
    step: float = 5.0,
) -> None:
    """Detections across a stay — a zone has to be held to be believed."""
    t = at
    end = at + dt.timedelta(seconds=seconds)
    while True:
        await detect(
            db_session, bbox=bbox, at=t, anon_id=anon_id, camera_id=camera_id
        )
        if t >= end:
            break
        t = min(t + dt.timedelta(seconds=step), end)


async def events_of(
    db_session: AsyncSession, type: str
) -> list[dict]:
    rows = await repository.read_events(
        db_session, tenant_id=T, since_seq=0, limit=500, type=type
    )
    return [r.payload for r in rows]


# ── the namespace itself, without a database ─────────────────────────────────


def test_two_cameras_first_visitors_are_two_people() -> None:
    a = person_key({"anon_id": "P-001", "camera_id": "cam-1"})
    b = person_key({"anon_id": "P-001", "camera_id": "cam-2"})
    assert a != b
    assert (a, b) == ("cam-1/P-001", "cam-2/P-001")


def test_a_detection_with_no_camera_keeps_its_bare_id() -> None:
    """The whole of the back-compatibility contract, in one line.

    Derived event ids are built from this value, so anything other than the
    bare id here changes the ids of every event ever derived from a pre-Phase-6
    detection — and the bus, which dedupes on event_id, stops recognising a
    replay of its own history as a replay.
    """
    assert person_key({"anon_id": "P-001"}) == "P-001"
    assert person_key({"person_id": "P-001"}) == "P-001"


def test_no_id_at_all_is_none_rather_than_a_guess() -> None:
    assert person_key({"bbox": [1, 2, 3, 4]}) is None


# ── the tracker ───────────────────────────────────────────────────────────────


async def test_two_cameras_do_not_merge_into_one_person(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """The headline bug: both cameras' `P-001`, in their own zones, at once.

    Each camera owns one zone here, so this is two people standing in two
    different parts of the booth. Merged, they would read as one person
    flickering between two zones — and the confirm window would suppress most
    of it, so what actually reached the report was one visitor with a fraction
    of the dwell two of them spent.
    """
    await seed(
        graph_session,
        cameras=["cam-1", "cam-2"],
        zone_owners={"z_left": "cam-1", "z_right": "cam-2"},
    )
    await stay(db_session, bbox=LEFT_PX, at=BASE, seconds=60, camera_id="cam-1")
    await stay(db_session, bbox=RIGHT_PX, at=BASE, seconds=60, camera_id="cam-2")

    await TrackerConsumer().run_once()

    enters = await events_of(db_session, "spatial.zone_enter")
    assert sorted(e["anon_id"] for e in enters) == ["cam-1/P-001", "cam-2/P-001"]
    assert {e["anon_id"]: e["zone_id"] for e in enters} == {
        "cam-1/P-001": "z_left",
        "cam-2/P-001": "z_right",
    }


async def test_each_camera_gets_its_own_dwell(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """Two people, two dwells, each the length its own visitor stood there.

    Merged, the second camera's detections looked like the first person moving,
    which closes the first dwell early. The count is the visible symptom; the
    durations are the one that reaches the client's ROI report.
    """
    await seed(
        graph_session,
        cameras=["cam-1", "cam-2"],
        zone_owners={"z_left": "cam-1", "z_right": "cam-2"},
    )
    await stay(db_session, bbox=LEFT_PX, at=BASE, seconds=90, camera_id="cam-1")
    await stay(db_session, bbox=RIGHT_PX, at=BASE, seconds=90, camera_id="cam-2")
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

    await TrackerConsumer().run_once()

    dwells = {d["anon_id"]: d for d in await events_of(db_session, "spatial.dwell")}
    assert set(dwells) == {"cam-1/P-001", "cam-2/P-001"}
    assert dwells["cam-1/P-001"]["duration"] == pytest.approx(90.0)
    assert dwells["cam-2/P-001"]["duration"] == pytest.approx(90.0)


async def test_a_zone_is_not_scored_against_another_cameras_frame(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """The half namespacing does not fix.

    `cam-2` sees somebody at 0.25 — the left half of *its* frame, which is a
    different piece of floor from the left half of `cam-1`'s. With both zones
    owned by `cam-1`, the honest answer is that `cam-2` has no zones and
    therefore nothing spatial to say about them.
    """
    await seed(
        graph_session,
        cameras=["cam-1", "cam-2"],
        zone_owners={"z_left": "cam-1", "z_right": "cam-1"},
    )
    await stay(db_session, bbox=LEFT_PX, at=BASE, seconds=60, camera_id="cam-2")

    await TrackerConsumer().run_once()

    assert await events_of(db_session, "spatial.zone_enter") == []


async def test_an_unowned_zone_belongs_to_every_camera(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """Two cameras on one stand from either side is a real booth layout.

    It is also what every zone drawn before zones had owners is, which is why
    the default has to be this way round: adding a second camera must not
    silently stop the existing zones from measuring anything.
    """
    await seed(
        graph_session,
        cameras=["cam-1", "cam-2"],
        zone_owners={"z_left": None, "z_right": "cam-2"},
    )
    await stay(db_session, bbox=LEFT_PX, at=BASE, seconds=60, camera_id="cam-1")
    await stay(db_session, bbox=LEFT_PX, at=BASE, seconds=60, camera_id="cam-2")

    await TrackerConsumer().run_once()

    enters = await events_of(db_session, "spatial.zone_enter")
    assert {e["anon_id"] for e in enters} == {"cam-1/P-001", "cam-2/P-001"}
    assert {e["zone_id"] for e in enters} == {"z_left"}


async def test_an_unattributed_detection_is_refused_on_a_two_camera_session(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """`P-001` from nowhere, on a session with two cameras, is unanswerable.

    Guessing means merging two people. So it parks on `/ops` naming the camera
    set, and the operator restarts perception with `--camera-id`; the parked
    events replay once they can be attributed.
    """
    await seed(
        graph_session,
        cameras=["cam-1", "cam-2"],
        zone_owners={"z_left": None},
    )
    await stay(db_session, bbox=LEFT_PX, at=BASE, seconds=60, camera_id=None)

    consumer = TrackerConsumer()
    await consumer.run_once()

    assert await events_of(db_session, "spatial.zone_enter") == []
    parked = await repository.list_dead_letters(db_session, consumer=consumer.name)
    assert parked
    assert "camera_id" in parked[0].error


async def test_one_camera_still_accepts_a_bare_track_id(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """Nothing to be ambiguous about, so nothing to refuse.

    This is the shape of every detection logged before Phase 6, and of the
    postgres-track's producer, which sends no `camera_id` at all.
    """
    await seed(
        graph_session,
        cameras=["cam-1"],
        zone_owners={"z_left": None},
    )
    await stay(db_session, bbox=LEFT_PX, at=BASE, seconds=60, camera_id=None)

    await TrackerConsumer().run_once()

    enters = await events_of(db_session, "spatial.zone_enter")
    assert [e["anon_id"] for e in enters] == ["P-001"]


# ── the graph ─────────────────────────────────────────────────────────────────


async def test_two_cameras_write_two_people(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """The graph has to agree with the spatial events written above it.

    `(:Person)` is keyed on `anon_id`, so a graph writer reading the bare id
    while the tracker emits the namespaced one would leave dwell edges pointing
    at a person the detections never created.
    """
    await seed(
        graph_session,
        cameras=["cam-1", "cam-2"],
        zone_owners={"z_left": "cam-1", "z_right": "cam-2"},
    )
    await stay(db_session, bbox=LEFT_PX, at=BASE, seconds=60, camera_id="cam-1")
    await stay(db_session, bbox=RIGHT_PX, at=BASE, seconds=60, camera_id="cam-2")

    await TrackerConsumer().run_once()
    await GraphWriterConsumer().run_once()

    result = await graph_session.run(
        "MATCH (p:Person {tenant_id: $t, session_id: $s}) RETURN p.anon_id AS id "
        "ORDER BY p.anon_id",
        t=T,
        s=S,
    )
    assert [r["id"] async for r in result] == ["cam-1/P-001", "cam-2/P-001"]


# ── grouping ──────────────────────────────────────────────────────────────────


async def walk_together(
    db_session: AsyncSession,
    *,
    a_camera: str | None,
    b_camera: str | None,
    steps: int = 24,
) -> None:
    """Two people crossing the frame side by side, in step.

    Deliberately the shape that *does* make a group: 40px apart (0.04, inside
    `group_radius`), 24 samples (past `group_confirm_samples`), and a midpoint
    that travels 0.8 of the frame (past `group_min_travel`). Co-movement, which
    is the harder of the two tests and the one a stationary queue fails.

    Which camera each of them is on is the only variable, so the pair of tests
    below differ in exactly one thing.
    """
    for i in range(steps):
        at = BASE + dt.timedelta(seconds=i * 2)
        x = 100 + i * 33
        await detect(
            db_session,
            bbox=[x, 400, x + 60, 600],
            at=at,
            anon_id="P-001",
            camera_id=a_camera,
        )
        await detect(
            db_session,
            bbox=[x + 40, 400, x + 100, 600],
            at=at,
            anon_id="P-002",
            camera_id=b_camera,
        )


async def test_two_people_walking_together_on_one_camera_are_a_group(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """The positive control, and it is the point of it.

    Without this, the cross-camera test below could pass because the walk never
    made a group at all, and it would go on passing after somebody deleted the
    check it exists to defend.
    """
    await seed(graph_session, cameras=["cam-1", "cam-2"], zone_owners={"z_left": None})
    await walk_together(db_session, a_camera="cam-1", b_camera="cam-1")

    await TrackerConsumer().run_once()
    await GroupingConsumer().run_once()

    groups = await events_of(db_session, "spatial.group")
    assert groups
    assert sorted(groups[0]["members"]) == ["cam-1/P-001", "cam-1/P-002"]


async def test_the_same_walk_across_two_cameras_is_not_a_group(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """The identical walk, one person per camera. Two strangers.

    Both are at the same fraction of their own camera's width, so a proximity
    test that ignores the camera measures them as walking arm in arm for two
    minutes. There is no transform between the two frames that could tell
    otherwise — zones are normalized image coordinates end to end — so the only
    honest answer is to decline to compare.
    """
    await seed(graph_session, cameras=["cam-1", "cam-2"], zone_owners={"z_left": None})
    await walk_together(db_session, a_camera="cam-1", b_camera="cam-2")

    await TrackerConsumer().run_once()
    await GroupingConsumer().run_once()

    assert await events_of(db_session, "spatial.group") == []


# ── the one that guards the history ───────────────────────────────────────────


async def test_a_single_camera_session_replays_to_the_same_ids(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """Replaying a camera-less session produces the ids it produced before.

    Spatial event ids are derived from the person key, so if the no-camera
    fallback ever stops returning the bare id, every event derived from a
    pre-Phase-6 detection gets a new id — the bus sees no duplicate, writes it
    again, and every dwell in the archive is counted twice.

    Driven the way `test_consumers.py::test_replay_is_a_no_op` drives it: run,
    rewind the cursor, run again, and count.
    """
    await seed(
        graph_session,
        cameras=["cam-1"],
        zone_owners={"z_left": None, "z_right": None},
    )
    await stay(db_session, bbox=LEFT_PX, at=BASE, seconds=60, camera_id=None)
    await stay(
        db_session,
        bbox=RIGHT_PX,
        at=BASE + dt.timedelta(seconds=90),
        seconds=60,
        camera_id=None,
    )

    consumer = TrackerConsumer()
    await consumer.run_once()
    before = await events_of(db_session, "spatial.dwell")
    assert before, "nothing to replay"

    await repository.reset_cursor(
        db_session, consumer=consumer.name, tenant_id=T, to_seq=0
    )
    await db_session.commit()

    # A fresh instance, as `test_consumers.py::test_replay_is_a_no_op` does:
    # in-memory state from the first run would make the replay a different
    # journey rather than the same one.
    await TrackerConsumer().run_once()

    after = await events_of(db_session, "spatial.dwell")
    assert len(after) == len(before)
    assert [d["anon_id"] for d in after] == [d["anon_id"] for d in before]
