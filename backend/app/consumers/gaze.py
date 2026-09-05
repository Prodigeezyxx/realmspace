"""
The gaze consumer — `spatial.gaze`, the last unbuilt signal in the §3 taxonomy.

`roadmap.md` recorded why it stayed unbuilt for five phases, and it was never a
missing decision: *"it needs head pose, and perception emits bounding boxes."*
Perception emits a heading now (`perception/realmspace.py`,
`heading_from_keypoints`), and this is the half that turns a direction into a
target.

## What leaves the edge, and what does not

`privacy.md`'s capture table allows pose keypoints "briefly", for a gaze vector,
and forbids face mesh being persisted at all. So the skeleton is consumed inside
perception's frame loop and **two scalars** reach the bus: a heading in radians
and a confidence. This consumer never sees a keypoint. That matters beyond
tidiness — the log is append-only and exported, so anything posted to it is
permanent, and a skeleton is a good deal more identifying than a box.

## Zones, not surfaces

`data-model.md` specifies `(Person)-[:LOOKED_AT]->(Object|Surface)`, and a
`Surface` in this system carries a `zone_id` and no geometry — there is nothing
in the frame to look at. `Zone` has a normalized polygon and an owning camera.
So gaze resolves against zones, which is a deliberate deviation recorded in
`data-model.md` rather than left to be discovered, and it costs nothing in the
event contract: `GazePayload.targetId` is deliberately generic.

## Its own consumer, like grouping

The tracker is the `< 500ms` path the Phase 1 acceptance is measured on. A bug
here must not be able to stop dwell being measured, so this runs beside it
rather than inside it — the argument `consumers/grouping.py` makes for the same
choice, for the same reason.

## What it refuses, which is most of the design

A signal that fires on the common case looks like coverage and is worse than
nothing: `detection_rate` drift is unbuilt for being confounded with the room
emptying, and grouping refuses to read proximity as company. Gaze's version of
that trap is reporting every head turn as interest. So:

  - a heading below `gaze_min_confidence` is not used;
  - a ray that hits nothing produces nothing;
  - the zone somebody is standing in is excluded — looking at the floor you are
    on is not attention;
  - a target must be held for `gaze_min_seconds`, and a glance that moves on
    before then is never emitted at all.

Each of those has a test named for it, because relaxing one is the obvious
"improvement" that turns this into a head-turn detector.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Any

from app import db
from app import repository
from app.config import get_settings
from app.consumers.base import Consumer
from app.consumers.ids import derive_event_id, person_key
from app.consumers.zones import centroid, first_zone_along_ray, normalize, zone_for_point
from app.graph import repository as graph_repo
from app.graph.driver import get_driver
from app.models import EventLog
from app.schemas import EventIn

log = logging.getLogger(__name__)

DETECTION = "perception.detection"
ZONES_UPDATED = "session.zones_updated"
SESSION_ENDED = "session.ended"
GAZE = "spatial.gaze"


class Look:
    """One target, held since a moment, seen this many times.

    A class rather than a tuple because the confidence is accumulated: the
    emitted event carries the *best* evidence seen across the hold, not the
    frame that happened to close it.
    """

    __slots__ = ("target_id", "since", "last", "best_confidence", "samples")

    def __init__(self, target_id: str, at: dt.datetime, confidence: float) -> None:
        self.target_id = target_id
        self.since = at
        self.last = at
        self.best_confidence = confidence
        self.samples = 1

    def extend(self, at: dt.datetime, confidence: float) -> None:
        self.last = at
        self.best_confidence = max(self.best_confidence, confidence)
        self.samples += 1

    def held_for(self) -> float:
        return (self.last - self.since).total_seconds()


class GazeConsumer(Consumer):
    name = "gaze"

    handles = (DETECTION, ZONES_UPDATED, SESSION_ENDED)

    #: Not retryable in isolation, for the reason the tracker and grouping both
    #: state about themselves: this carries state built from the detections
    #: before the parked one — who was looking where, and since when. Replaying
    #: one detection against an empty state does not reproduce the original
    #: attempt, it produces a confident wrong answer.
    retryable = False

    def __init__(self) -> None:
        super().__init__()
        # (tenant, session) -> person_key -> Look
        self._looks: dict[tuple[str, str], dict[str, Look]] = {}
        # (tenant, session) -> (zones, monotonic stamp)
        self._zones: dict[tuple[str, str], tuple[list[dict], float]] = {}

    async def on_replay(self, tenant_id: str) -> None:
        """Forget every held look, so the replay rebuilds them from the log.

        Without this a replay starts mid-gaze and closes looks at moments
        nothing caused. Deriving the ids does not save it: those events have
        genuinely different causes, so genuinely different ids, and the bus
        keeps both.
        """
        for key in [k for k in self._looks if k[0] == tenant_id]:
            del self._looks[key]
        for key in [k for k in self._zones if k[0] == tenant_id]:
            del self._zones[key]

    async def zones_for(self, tenant_id: str, session_id: str) -> list[dict]:
        """Zone polygons, cached exactly as the tracker caches them.

        Same TTL and the same event-driven invalidation, for the same reasons
        that file sets out: a graph round trip per detection would be absurd at
        20fps, and an operator who redraws a zone must not have gaze scored
        against the old shape until a process restart.
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

    async def handle(self, event: EventLog) -> None:
        key = (event.tenant_id, event.session_id)

        if event.type == ZONES_UPDATED:
            self._zones.pop(key, None)
            return

        if event.type == SESSION_ENDED:
            emitted = self._close_all(event)
        else:
            emitted = await self._on_detection(key, event)

        if not emitted:
            return

        async with db.SessionLocal() as session:
            await db.scope_to_tenant(session, event.tenant_id)
            for out in emitted:
                await repository.append_event(session, out)
            await session.commit()

    async def _on_detection(
        self, key: tuple[str, str], event: EventLog
    ) -> list[EventIn]:
        payload: dict[str, Any] = event.payload or {}

        heading = payload.get("heading")
        if heading is None:
            # A detect-only model, or a skeleton that did not settle the
            # question. Perception already declined; nothing to add.
            return []

        settings = get_settings()
        confidence = float(payload.get("heading_confidence") or 0.0)
        if confidence < settings.gaze_min_confidence:
            # The threshold lives here rather than at every camera, so one
            # deployment cannot quietly run a looser bar than another.
            return self._close_if_open(key, event, reason="low_confidence")

        anon_id = person_key(payload)
        bbox = payload.get("bbox")
        frame_w = payload.get("frame_width")
        frame_h = payload.get("frame_height")
        if not anon_id or not bbox or not frame_w or not frame_h:
            # Malformed for this consumer's purposes. The tracker dead-letters
            # these and says why; a second complaint would put one event on
            # `/ops` twice for one cause.
            return []

        zones = await self.zones_for(event.tenant_id, event.session_id)
        camera_id = payload.get("camera_id")
        if camera_id is not None:
            # The same filter the tracker applies, and it matters more here: a
            # ray is scored against polygons that are normalized within *one*
            # camera's frame, so casting it into another camera's zones points
            # at floor that camera never saw. A zone with no owner belongs to
            # every camera, which is what every zone drawn before ownership
            # existed is.
            zones = [z for z in zones if z.get("camera_id") in (None, camera_id)]
        if not zones:
            return self._close_if_open(key, event, reason="no_zones")

        cx, cy = centroid(bbox)
        nx, ny = normalize(cx, cy, frame_w, frame_h)

        standing_in = zone_for_point(nx, ny, zones)
        hit = first_zone_along_ray(nx, ny, float(heading), zones, exclude=standing_in)
        if hit is None:
            return self._close_if_open(key, event, reason="no_target")

        target_id, _distance = hit
        looks = self._looks.setdefault(key, {})
        current = looks.get(anon_id)

        if current is not None and current.target_id == target_id:
            current.extend(event.occurred_at, confidence)
            return []

        # Looking somewhere new. Whatever they were looking at before is closed
        # here, which is what makes the duration a measurement of a held look
        # rather than of the gap between two detections.
        emitted = self._close(key, event, anon_id) if current is not None else []
        looks[anon_id] = Look(target_id, event.occurred_at, confidence)
        return emitted

    def _close_if_open(
        self, key: tuple[str, str], event: EventLog, *, reason: str
    ) -> list[EventIn]:
        """End a held look because the evidence stopped supporting it.

        The reasons differ — the confidence fell away, the zones vanished, the
        ray stopped hitting anything — and none of them is a reason to *discard*
        a look that had already been held long enough to count. Somebody who
        studies a wall for ten seconds and then turns away has looked at it.
        """
        looks = self._looks.get(key) or {}
        anon_id = person_key(event.payload or {})
        if not anon_id or anon_id not in looks:
            return []
        return self._close(key, event, anon_id, reason=reason)

    def _close(
        self,
        key: tuple[str, str],
        event: EventLog,
        anon_id: str,
        *,
        reason: str = "moved_on",
    ) -> list[EventIn]:
        look = (self._looks.get(key) or {}).pop(anon_id, None)
        if look is None:
            return []

        settings = get_settings()
        duration = look.held_for()
        if duration < settings.gaze_min_seconds:
            # A glance. Emitting it would make the commonest thing a person does
            # with their head into a finding.
            return []

        return [
            EventIn(
                # Derived, so a replay reproduces the id and the bus dedupes
                # rather than counting one look twice. The moment it started is
                # in the id because the same person can look at the same zone
                # more than once in a session, and those are different events.
                event_id=derive_event_id(
                    self.name,
                    event.tenant_id,
                    event.session_id,
                    anon_id,
                    look.target_id,
                    look.since.isoformat(),
                ),
                tenant_id=event.tenant_id,
                session_id=event.session_id,
                type=GAZE,
                payload={
                    "anon_id": anon_id,
                    "target_id": look.target_id,
                    # The spec's §3 name for this field is `duration`, as it is
                    # for dwell. The browser calls it `durationSec` and
                    # `lib/bus/wire.ts` already translates that pair.
                    "duration": round(duration, 2),
                    "confidence": round(look.best_confidence, 3),
                    "started_at": look.since.isoformat(),
                    "ended_at": look.last.isoformat(),
                    "samples": look.samples,
                    "reason": reason,
                },
                # The look ended when it ended, not when this ran.
                occurred_at=look.last,
            )
        ]

    def _close_all(self, event: EventLog) -> list[EventIn]:
        """Everyone still looking at something when the doors shut.

        The same gap `session.ended` was added to close for the tracker: without
        it, the visitors in the room at the end are the ones whose attention is
        never counted.
        """
        key = (event.tenant_id, event.session_id)
        out: list[EventIn] = []
        for anon_id in list((self._looks.get(key) or {}).keys()):
            out.extend(self._close(key, event, anon_id, reason="session_end"))
        return out
