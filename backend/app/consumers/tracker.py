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

## Session hygiene

Raw polygon membership is not a visit. Three things corrupt it, and all three
produce events that look entirely ordinary downstream — they end up in a
client's ROI report as findings, which is why they are handled here rather than
filtered later:

  **Boundary flicker.** Somebody standing on a zone edge crosses it at frame
  rate. Untreated, one person loitering by a doorway generates dozens of
  enter/exit/dwell triples a minute, each indistinguishable from a real visit.
  A membership change must therefore hold for `tracker_zone_confirm_seconds`
  before it is believed — but the timestamps use the *real* crossing, not the
  moment of confirmation, so dwell durations stay accurate.

  **Sub-second dwells.** Passing through a corner of a zone is not time spent
  there. Below `tracker_min_dwell_seconds` the dwell is dropped; the exit is
  still emitted, because the person did leave.

  **Dropout.** A track that stops being detected inside a zone used to emit
  nothing at all — no exit, no dwell — so every visit that ended by walking out
  of frame was silently discarded and the activation under-reported. After
  `tracker_dropout_seconds` of silence the visit is closed at the last sighting
  and marked `reason: "dropout"`, so a consumer can discount it knowingly
  rather than never see it.

Ported from the postgres-track's `spatial-deriver.ts`, which derives the same
events in the browser. Two tracks, one definition of what counts as a visit —
the bake-off should turn on the graph store, not on which one counts better.

## In-memory state, and what that costs

Current zone per person lives in a dict, not the database. That is the right
call for a real-time loop, but it means a restart loses it. Because the cursor
has already advanced, the old detections are not replayed, so a person standing
in a zone across a restart gets a fresh zone_enter on their next detection and
their dwell clock restarts.

Not hidden, not free. The fix, when it matters, is to rebuild state at startup
from the graph's ENTERED edges that have no matching LEFT.

All timing here is **event time**, never wall clock. That is what keeps a replay
byte-identical to the original run: the same detections in, the same spatial
events with the same derived ids out.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from dataclasses import dataclass, field

from app import repository
from app.config import get_settings
from app.consumers.base import Consumer
from app.consumers.ids import derive_event_id
from app.consumers.zones import centroid, dist_to_polygon, normalize, zone_for_point
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
#: roi-framework.md §2, Reach: came close to a zone and never went in.
PASSBY = "spatial.passby"
#: Emitted by POST /v1/sessions when an operator redraws zones. See handle().
ZONES_UPDATED = "session.zones_updated"
#: The operator closed the activation. See handle().
SESSION_ENDED = "session.ended"

#: Why a visit ended. Carried on zone_exit, dwell and passby so a consumer can
#: weigh them differently — a dwell that ended in a dropout is a lower bound on
#: the real one, and the report should be able to say so.
REASON_MOVE = "move"
REASON_DROPOUT = "dropout"
REASON_SESSION_END = "session_end"


@dataclass
class TrackState:
    """What the tracker remembers about one person.

    `confirmed` is where they are believed to be; `candidate` is a membership
    change that has not yet held long enough to believe. Keeping the two apart
    is the whole of the flicker fix — the old code had only the former, so every
    boundary wobble was immediately a fact.
    """

    #: Believed zone, or None for "in frame but in no zone".
    confirmed: str | None = None
    #: When the confirmed stay actually began — the crossing, not its confirmation.
    entered_at: dt.datetime | None = None
    #: A pending change: the zone seen, and when it was first seen.
    candidate: str | None = None
    candidate_since: dt.datetime | None = None
    #: Last detection of this person, for dropout.
    last_seen: dt.datetime | None = field(default=None)
    #: Closest this person ever came to each zone, for pass-by.
    min_dist_by_zone: dict[str, float] = field(default_factory=dict)
    #: Zones they actually entered — excluded from pass-by, since going in is
    #: the opposite of the signal pass-by exists to capture.
    entered_zones: set[str] = field(default_factory=set)


class TrackerConsumer(Consumer):
    name = "tracker"
    handles = (DETECTION, ZONES_UPDATED, SESSION_ENDED)

    def __init__(self) -> None:
        super().__init__()
        # (tenant_id, session_id, anon_id) -> TrackState
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
        self._where: dict[tuple[str, str, str], TrackState] = {}
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

        Both a TTL *and* event-driven invalidation. `session.zones_updated` now
        exists and this consumer subscribes to it, which makes a redraw take
        effect on the next detection instead of up to a TTL later. The TTL stays
        as the backstop: the event can be lost to a dead letter, or the zones can
        be changed by something that never emits it (a hand-run Cypher statement
        during a demo, which happens), and a cache with no expiry would then hold
        the wrong polygons until the process restarted.
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
        if event.type == ZONES_UPDATED:
            # An operator redrew the zones. Drop the cached polygons so the next
            # detection is scored against the new ones.
            #
            # Deliberately *not* clearing `self._where`. A person standing still
            # while a zone is edited has genuinely not moved, and forgetting them
            # would emit a zone_enter for a step nobody took. If the edit moved
            # them out of the zone they were in, the next detection sees the
            # change and emits the exit and dwell then — which is the truth: the
            # zone stopped containing them at the moment it was redrawn.
            self.forget_zones(event.tenant_id, event.session_id)
            return

        if event.type == SESSION_ENDED:
            # The doors have shut. Everyone still being tracked gets closed out
            # now, rather than waiting for a dropout timeout that will never
            # arrive because no further detections are coming.
            emitted = self._flush_session(event)
            if emitted:
                await self._append(event.tenant_id, emitted)
            return

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

        settings = get_settings()
        now = event.occurred_at
        key = (event.tenant_id, event.session_id, anon_id)
        emitted: list[EventIn] = []

        # Sweep **before** recording this sighting, so a person who themselves
        # went quiet is caught. Updating `last_seen` first would exempt exactly
        # the track that had been missing — someone who leaves the frame for two
        # minutes and returns would have the whole gap counted as dwell, which
        # is the opposite of what a dropout means.
        emitted += self._sweep_dropouts(event, now)

        state = self._where.get(key)
        if state is None:
            state = TrackState()
            self._where[key] = state
            self._prune_where()
        state.last_seen = now

        # Closest approach to each zone, for pass-by. Recorded on every frame
        # because the nearest miss is the whole signal — sampling only on zone
        # changes would miss somebody who walked the length of a stand, paused
        # at arm's length from it, and moved on without ever crossing a boundary.
        for zone in zones:
            zone_id = zone["id"]
            if zone_id in state.entered_zones:
                continue
            distance = dist_to_polygon(nx, ny, zone.get("polygon") or [])
            previous = state.min_dist_by_zone.get(zone_id)
            if previous is None or distance < previous:
                state.min_dist_by_zone[zone_id] = distance

        if now_zone == state.confirmed:
            # Back where we believed they were: whatever change was pending was
            # flicker, and pretending otherwise is exactly the bug.
            state.candidate = None
            state.candidate_since = None
        elif state.candidate_since is None or state.candidate != now_zone:
            # A new candidate. Note the time of the actual crossing — the
            # emitted timestamps use this, not the later moment of confirmation,
            # so a confirmed dwell is as long as the person was really there.
            #
            # `candidate_since is None` is what marks "nothing pending", not
            # `candidate is None`: None is itself a legitimate destination,
            # meaning "in frame, in no zone". Conflating the two made a person
            # walking out of every zone invisible — their exit never fired,
            # because leaving looked identical to having nothing pending.
            state.candidate = now_zone
            state.candidate_since = now
        elif (
            now - state.candidate_since
        ).total_seconds() >= settings.tracker_zone_confirm_seconds:
            emitted += self._commit_transition(
                event, state, anon_id, now_zone, state.candidate_since
            )

        if emitted:
            await self._append(event.tenant_id, emitted)

    async def _append(self, tenant_id: str, events: list[EventIn]) -> None:
        """Write the tracker's own events back onto the bus.

        A fresh session carries no tenant scope, and under RLS an unscoped
        INSERT is rejected by the WITH CHECK policy. The tenant is the source
        event's — the tracker only ever emits about the tenant it is reading.
        """
        async with db.SessionLocal() as session:
            await db.scope_to_tenant(session, tenant_id)
            for out in events:
                await repository.append_event(session, out)
            await session.commit()

    def _close_visit(
        self,
        source: EventLog,
        *,
        anon_id: str,
        zone_id: str,
        entered_at: dt.datetime,
        left_at: dt.datetime,
        reason: str,
    ) -> list[EventIn]:
        """End a stay: an exit, and a dwell if the stay was long enough to count.

        The exit is unconditional — they left, and the graph's LEFT edge should
        say so. The dwell is not: below `tracker_min_dwell_seconds` the person
        clipped a corner rather than spent time there, and emitting it would put
        a visit in the report that nobody made.
        """
        settings = get_settings()
        entered_iso = _iso(entered_at)
        left_iso = _iso(left_at)
        duration = max((left_at - entered_at).total_seconds(), 0.0)

        out = [
            self._event(
                source,
                type=ZONE_EXIT,
                parts=(ZONE_EXIT, anon_id, zone_id, entered_iso),
                payload={
                    "anon_id": anon_id,
                    "zone_id": zone_id,
                    "at": left_iso,
                    "entered_at": entered_iso,
                    "reason": reason,
                },
            )
        ]

        if duration >= settings.tracker_min_dwell_seconds:
            out.append(
                self._event(
                    source,
                    type=DWELL,
                    # keyed on the visit, not the detection: one dwell per stay
                    parts=(DWELL, anon_id, zone_id, entered_iso),
                    payload={
                        "anon_id": anon_id,
                        "zone_id": zone_id,
                        "duration": duration,
                        "started_at": entered_iso,
                        "ended_at": left_iso,
                        "exceeded_threshold": duration
                        >= settings.dwell_threshold_seconds,
                        "reason": reason,
                    },
                )
            )
        return out

    def _commit_transition(
        self,
        source: EventLog,
        state: TrackState,
        anon_id: str,
        now_zone: str | None,
        crossed_at: dt.datetime,
    ) -> list[EventIn]:
        """Believe a zone change that has now held long enough."""
        emitted: list[EventIn] = []

        if state.confirmed is not None and state.entered_at is not None:
            emitted += self._close_visit(
                source,
                anon_id=anon_id,
                zone_id=state.confirmed,
                entered_at=state.entered_at,
                left_at=crossed_at,
                reason=REASON_MOVE,
            )

        if now_zone is not None:
            at = _iso(crossed_at)
            emitted.append(
                self._event(
                    source,
                    type=ZONE_ENTER,
                    parts=(ZONE_ENTER, anon_id, now_zone, at),
                    payload={"anon_id": anon_id, "zone_id": now_zone, "at": at},
                )
            )
            state.confirmed = now_zone
            state.entered_at = crossed_at
            # Going in disqualifies the zone from pass-by, permanently. Someone
            # who circles a stand twice and enters on the second pass engaged
            # with it; reporting them as a skip as well would count one person
            # as both the success and the failure.
            state.entered_zones.add(now_zone)
            state.min_dist_by_zone.pop(now_zone, None)
        else:
            state.confirmed = None
            state.entered_at = None

        state.candidate = None
        state.candidate_since = None
        return emitted

    def _finalize(
        self, source: EventLog, anon_id: str, state: TrackState, reason: str
    ) -> list[EventIn]:
        """Close a track out for good: end any open visit, then judge pass-bys.

        Order matters. The visit is closed first so that a zone entered right at
        the end is in `entered_zones` before pass-by is evaluated — otherwise
        somebody who finally walked in would be reported as having skipped the
        very zone they are standing in.
        """
        emitted: list[EventIn] = []

        if state.confirmed is not None and state.entered_at is not None and state.last_seen:
            emitted += self._close_visit(
                source,
                anon_id=anon_id,
                zone_id=state.confirmed,
                entered_at=state.entered_at,
                # Closed at the last sighting, not at the moment we noticed. The
                # person left some time between those two, and the earlier of
                # them is the one we can actually defend.
                left_at=state.last_seen,
                reason=reason,
            )
            state.entered_zones.add(state.confirmed)
            state.min_dist_by_zone.pop(state.confirmed, None)

        radius = get_settings().tracker_passby_radius
        at = _iso(state.last_seen) if state.last_seen else _iso(source.occurred_at)

        # Sorted so a replay emits them in a stable order. The ids are derived
        # and would dedupe regardless, but a log that reorders itself between
        # runs is miserable to diff when something does go wrong.
        for zone_id in sorted(state.min_dist_by_zone):
            if zone_id in state.entered_zones:
                continue
            distance = state.min_dist_by_zone[zone_id]
            if distance > radius:
                continue
            emitted.append(
                self._event(
                    source,
                    type=PASSBY,
                    # One pass-by per person per zone for the whole session —
                    # not per approach. Somebody pacing outside a stand is one
                    # person who declined it, not twelve.
                    parts=(PASSBY, anon_id, zone_id),
                    payload={
                        "anon_id": anon_id,
                        "adjacent_zone_id": zone_id,
                        "closest_dist": round(distance, 3),
                        "at": at,
                        "reason": reason,
                    },
                )
            )

        state.confirmed = None
        state.entered_at = None
        state.candidate = None
        state.candidate_since = None
        state.min_dist_by_zone = {}
        return emitted

    def _sweep_dropouts(self, source: EventLog, now: dt.datetime) -> list[EventIn]:
        """Finalise anyone in this session who has stopped being detected.

        Two things depend on this, not one. A visit that ended by the visitor
        walking out of frame emits nothing without it — and the longest stays are
        the likeliest to end that way, so the activation under-reports exactly
        the engagement it most wants to prove. And a **pass-by is only knowable
        at close-out**: until a track ends, someone lingering outside a zone
        might still walk in.

        That second one is why this no longer skips tracks with no confirmed
        zone. A pass-by subject never confirms one — that is what makes them a
        pass-by — so the old guard excluded precisely the case it now exists for.

        Scoped to the triggering event's tenant and session: a busy session must
        not scan every tenant the process has seen, and the timestamps only mean
        anything within one session's clock.
        """
        timeout = get_settings().tracker_dropout_seconds
        emitted: list[EventIn] = []

        for key, state in self._where.items():
            if key[0] != source.tenant_id or key[1] != source.session_id:
                continue
            if state.last_seen is None or (now - state.last_seen).total_seconds() < timeout:
                continue
            # Nothing open and nothing near: already finalised, or never had
            # anything to say. Skipping keeps the sweep from re-walking every
            # track that has already been closed.
            if state.confirmed is None and not state.min_dist_by_zone:
                continue

            emitted += self._finalize(source, key[2], state, REASON_DROPOUT)

        return emitted

    def _flush_session(self, source: EventLog) -> list[EventIn]:
        """Close out every track in a session, because the doors have shut.

        Without this the last visitors of an activation are never finalised: no
        closing dwell, no pass-by. On a short session that is a large share of
        the audience, and nothing in the report would show it as missing — the
        numbers would simply be smaller than the day was.
        """
        emitted: list[EventIn] = []
        for key, state in self._where.items():
            if key[0] != source.tenant_id or key[1] != source.session_id:
                continue
            if state.confirmed is None and not state.min_dist_by_zone:
                continue
            emitted += self._finalize(source, key[2], state, REASON_SESSION_END)
        return emitted

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


