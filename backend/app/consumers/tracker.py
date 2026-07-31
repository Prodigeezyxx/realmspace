"""
The tracker consumer — event-bus-spec.md §4, "detections → tracked anon persons
+ spatial events", real-time, on the edge.

## What it does NOT do

It does not detect people and it does not assign IDs. Perception already runs
ByteTrack (`perception/realmspace.py:174`, `yolo.track(persist=True)`) and puts
a stable `person_id` on every detection. Per §3, `perception.detection` is
produced by *edge perception*; `spatial.*` is produced by *the tracker*. This
consumer is the second half only.

It also never reconciles identities. privacy.md is explicit: "A person who walks
behind a wall and reappears is treated as a new ID", and there is no
cross-session or cross-camera re-identification. If perception hands us a new
id, that is a new person. Full stop.

## What it emits

Reading a detection, it works out which zone the person's centroid is in and
compares that to where they were last frame:

  entered a zone      → spatial.zone_enter
  left a zone         → spatial.zone_exit  + spatial.dwell (with the duration)

The events go back into the same log via repository.append_event — the tracker
is just another producer, with no privileged path. The graph writer picks them
up from there.

## In-memory state, and what that costs

Current zone per person lives in a dict, not the database. That is the right
call for a real-time loop, but it means a restart loses it. Because the cursor
has already advanced, the old detections are not replayed, so a person standing
in a zone across a restart gets a fresh zone_enter on their next detection and
their dwell clock restarts.

Not hidden, not free. The fix, when it matters, is to rebuild state at startup
from the graph's ENTERED edges that have no matching LEFT.
"""

from __future__ import annotations

import datetime as dt
import logging
import time

from app import repository
from app.config import get_settings
from app.consumers.base import Consumer
from app.consumers.ids import derive_event_id
from app.consumers.zones import centroid, normalize, zone_for_point
from app import db
from app.graph import repository as graph_repo
from app.graph.driver import get_driver
from app.models import EventLog
from app.schemas import EventIn

log = logging.getLogger(__name__)

DETECTION = "perception.detection"
ZONE_ENTER = "spatial.zone_enter"
ZONE_EXIT = "spatial.zone_exit"
DWELL = "spatial.dwell"


class TrackerConsumer(Consumer):
    name = "tracker"
    handles = (DETECTION,)

    def __init__(self) -> None:
        super().__init__()
        # (tenant_id, session_id, anon_id) -> (zone_id, entered_at_iso)
        #
        # Bounded: dicts keep insertion order, so when this exceeds the cap the
        # oldest entries are evicted first. Without a cap it only ever shrinks
        # when somebody leaves *every* zone, so anyone whose last known position
        # was inside one stays here for the life of the process — a slow leak
        # across a multi-day activation with thousands of visitors.
        #
        # Evicting someone still standing in a zone costs one spurious
        # zone_enter on their next detection, which is the same, already
        # documented consequence as a restart. Better than unbounded growth.
        self._where: dict[tuple[str, str, str], tuple[str, str]] = {}
        # (tenant_id, session_id) -> (zones, fetched_at_monotonic)
        self._zones: dict[tuple[str, str], tuple[list[dict], float]] = {}

    def _prune_where(self) -> None:
        cap = get_settings().tracker_max_tracked_people
        while len(self._where) > cap:
            self._where.pop(next(iter(self._where)))

    async def on_replay(self, tenant_id: str) -> None:
        """Forget where everyone was, so the replay reconstructs it from the log.

        Without this, replaying looks like every person teleporting from wherever
        they finished back to where they started, and the tracker faithfully
        emits an exit and a dwell for a journey nobody made. Deriving event ids
        does not save you here: those spurious events have genuinely different
        causes, so they get genuinely different ids and the bus stores them.

        Idempotency protects against reprocessing the same input. It does not
        protect against processing the same input in a different *state*.
        """
        for key in [k for k in self._where if k[0] == tenant_id]:
            del self._where[key]

    async def zones_for(self, tenant_id: str, session_id: str) -> list[dict]:
        """Zone polygons for a session, cached with a short TTL.

        The cache exists because this is the hot path — a graph round trip per
        detection would be absurd at 20fps. The TTL exists because operators
        redraw zones mid-session (multi-tenant.md §4, and the session wizard
        allows it). Without expiry the tracker would keep scoring detections
        against the old polygons until the process restarted, silently
        attributing dwell to the wrong zone with nothing to indicate a problem.

        A TTL rather than event-driven invalidation because no event in the §3
        taxonomy announces a zone edit. When one exists, subscribe to it and
        drop the TTL.
        """
        settings = get_settings()
        key = (tenant_id, session_id)
        now = time.monotonic()

        cached = self._zones.get(key)
        if cached is not None and now - cached[1] < settings.tracker_zone_cache_seconds:
            return cached[0]

        async with get_driver().session(database=settings.neo4j_database) as gs:
            zones = await graph_repo.zones_for_session(
                gs, tenant_id=tenant_id, session_id=session_id
            )
        self._zones[key] = (zones, now)
        return zones

    def forget_zones(self, tenant_id: str, session_id: str) -> None:
        """Drop a session's cached polygons immediately, ahead of the TTL."""
        self._zones.pop((tenant_id, session_id), None)

    async def handle(self, event: EventLog) -> None:
        payload = event.payload
        # Two spellings in the wild: `anon_id` (data-model.md's name for the
        # Person key, and what the postgres-track's producer sends as `anonId`)
        # and `person_id` (what event-bus-spec.md §3 documents). Accepting both
        # means one producer script works against both backends; §3 records
        # `anon_id` as canonical and `person_id` as accepted.
        anon_id = payload.get("anon_id") or payload.get("person_id")
        bbox = payload.get("bbox")
        if not anon_id or not bbox:
            raise ValueError(
                f"perception.detection seq={event.seq} missing anon_id/person_id or bbox"
            )

        # Frame dimensions are required: bboxes arrive in pixels and zone
        # polygons are normalized 0..1, so there is no way to compare them
        # without knowing the frame size. Contract documented in spec §3.
        frame_w = payload.get("frame_width")
        frame_h = payload.get("frame_height")
        if not frame_w or not frame_h:
            raise ValueError(
                f"perception.detection seq={event.seq} missing frame_width/frame_height "
                "— required to normalise a pixel bbox against 0..1 zone polygons"
            )

        zones = await self.zones_for(event.tenant_id, event.session_id)
        if not zones:
            return  # no zones drawn yet; nothing spatial to say

        cx, cy = centroid(bbox)
        nx, ny = normalize(cx, cy, frame_w, frame_h)
        now_zone = zone_for_point(nx, ny, zones)

        key = (event.tenant_id, event.session_id, anon_id)
        previous = self._where.get(key)
        prev_zone = previous[0] if previous else None

        if now_zone == prev_zone:
            return  # still in the same place — nothing happened

        at = _iso(event.occurred_at)
        emitted: list[EventIn] = []

        if prev_zone is not None:
            entered_at = previous[1]  # type: ignore[index]
            emitted.append(
                self._event(
                    event,
                    type=ZONE_EXIT,
                    parts=(ZONE_EXIT, anon_id, prev_zone, entered_at),
                    payload={
                        "anon_id": anon_id,
                        "zone_id": prev_zone,
                        "at": at,
                        "entered_at": entered_at,
                    },
                )
            )
            duration = _seconds_between(entered_at, at)
            emitted.append(
                self._event(
                    event,
                    type=DWELL,
                    # keyed on the visit, not the detection: one dwell per stay
                    parts=(DWELL, anon_id, prev_zone, entered_at),
                    payload={
                        "anon_id": anon_id,
                        "zone_id": prev_zone,
                        "duration": duration,
                        "started_at": entered_at,
                        "ended_at": at,
                        "exceeded_threshold": duration
                        >= get_settings().dwell_threshold_seconds,
                    },
                )
            )

        if now_zone is not None:
            emitted.append(
                self._event(
                    event,
                    type=ZONE_ENTER,
                    parts=(ZONE_ENTER, anon_id, now_zone, at),
                    payload={"anon_id": anon_id, "zone_id": now_zone, "at": at},
                )
            )
            self._where[key] = (now_zone, at)
            self._prune_where()
        else:
            self._where.pop(key, None)

        if emitted:
            async with db.SessionLocal() as session:
                for out in emitted:
                    await repository.append_event(session, out)
                await session.commit()

    def _event(
        self,
        source: EventLog,
        *,
        type: str,
        parts: tuple[str, ...],
        payload: dict,
    ) -> EventIn:
        """Build a spatial event derived from `source`.

        The event_id is derived, never random — see consumers/ids.py. Replay
        must produce the same id or the bus cannot dedupe it and every dwell
        gets counted twice.
        """
        return EventIn(
            event_id=derive_event_id(self.name, source.tenant_id, source.session_id, *parts),
            tenant_id=source.tenant_id,
            session_id=source.session_id,
            type=type,
            payload=payload,
            occurred_at=source.occurred_at,
        )


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat()


def _seconds_between(start_iso: str, end_iso: str) -> float:
    start = dt.datetime.fromisoformat(start_iso)
    end = dt.datetime.fromisoformat(end_iso)
    return max((end - start).total_seconds(), 0.0)
