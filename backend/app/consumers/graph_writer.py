"""
The graph writer — event-bus-spec.md §4, "events → nodes/edges
(data-model.md)", near-real-time, on the edge.

Reads both families and writes through app/graph/repository.py:

  perception.detection  → upsert_person       (first_seen / last_seen advance)
  spatial.zone_enter    → (Person)-[:ENTERED]->(Zone)
  spatial.zone_exit     → (Person)-[:LEFT]->(Zone)
  spatial.dwell         → (Person)-[:DWELLED_IN {duration,…}]->(Zone)
  surface.interaction   → (Person)-[:INTERACTED_WITH {duration, kind}]->(Surface)

## Why this is safe to run twice

Every write is a MERGE keyed on something stable, so re-processing an event is a
no-op rather than a duplicate. That matters because the graph is a *separate
store* from the log — a graph write and a cursor advance cannot share a
transaction, so a crash between the two guarantees the event is redelivered.
There is no way to make that atomic across two databases; making the write
idempotent is the answer, and it is what spec §4 requires of every consumer.

## Identity

`anon_id` is perception's `person_id`, passed straight through. The writer never
tries to match one person to another — privacy.md rules out re-identification
across sessions and cameras, and data-model.md keys Person on
(tenant_id, session_id, anon_id) so the database enforces the session scoping.
"""

from __future__ import annotations

import logging

from app.config import get_settings
from app.consumers.base import Consumer
from app.consumers.ids import person_key
from app.graph import repository as graph_repo
from app.graph.driver import get_driver
from app.models import EventLog

log = logging.getLogger(__name__)

DETECTION = "perception.detection"
ZONE_ENTER = "spatial.zone_enter"
ZONE_EXIT = "spatial.zone_exit"
DWELL = "spatial.dwell"
SURFACE = "surface.interaction"
GROUP = "spatial.group"
GAZE = "spatial.gaze"

#: `spatial.group` statuses. See `consumers/grouping.py`.
DISSOLVED = "dissolved"


class GraphWriterConsumer(Consumer):
    name = "graph_writer"

    #: Safe for a human to retry one parked event on its own. Every handler here
    #: is a pure function of the event — it reads nothing but the payload and
    #: MERGEs on a stable key — so re-running one in isolation produces exactly
    #: what the original attempt would have. That is what makes the HITL retry
    #: button meaningful for this consumer and not for the others.
    retryable = True
    handles = (DETECTION, ZONE_ENTER, ZONE_EXIT, DWELL, SURFACE, GROUP, GAZE)

    async def handle(self, event: EventLog) -> None:
        settings = get_settings()
        async with get_driver().session(database=settings.neo4j_database) as gs:
            if event.type == DETECTION:
                await self._on_detection(gs, event)
            elif event.type == ZONE_ENTER:
                await self._on_zone_enter(gs, event)
            elif event.type == ZONE_EXIT:
                await self._on_zone_exit(gs, event)
            elif event.type == DWELL:
                await self._on_dwell(gs, event)
            elif event.type == SURFACE:
                await self._on_surface_interaction(gs, event)
            elif event.type == GROUP:
                await self._on_group(gs, event)
            elif event.type == GAZE:
                await self._on_gaze(gs, event)

    async def _on_group(self, gs, event: EventLog) -> None:
        """`spatial.group` → `(:Group)` and its `GROUP_MEMBER_OF` edges.

        The node survives dissolution. A group that has broken up still
        happened, and `data-model.md`'s "Groups in Lounge" query asks what
        visited — deleting it at close-out would mean a report could only ever
        describe the groups that were still standing when the doors shut.

        Membership is **replaced**, not appended (`set_group_members`), because a
        `changed` event carries the membership as it now stands. An append-only
        writer would leave a departed member linked forever and `size` would
        disagree with the number of edges beneath it.
        """
        payload = event.payload or {}
        group_id = payload.get("group_id")
        members = payload.get("members") or []
        if not group_id:
            raise ValueError(f"spatial.group seq={event.seq} has no group_id")

        at = event.occurred_at.isoformat()
        await graph_repo.upsert_group(
            gs,
            tenant_id=event.tenant_id,
            session_id=event.session_id,
            group_id=group_id,
            size=int(payload.get("size") or len(members)),
            cohesion=float(payload.get("cohesion") or 0.0),
            first_seen=at,
            last_seen=at,
        )
        await graph_repo.set_group_members(
            gs,
            tenant_id=event.tenant_id,
            session_id=event.session_id,
            group_id=group_id,
            members=list(members),
        )

    async def _on_gaze(self, gs, event: EventLog) -> None:
        """`spatial.gaze` → `(Person)-[:LOOKED_AT]->(Zone)`.

        The target is a Zone rather than the `Object|Surface` data-model.md
        names, because a Surface has no geometry to aim at — see
        `graph_repo.link_looked_at`. `consumers/gaze.py` has already applied the
        confidence floor and the hold, so anything arriving here is a look
        somebody actually held; the confidence rides onto the edge so a reader
        can still weigh it.
        """
        p = event.payload or {}
        await self._ensure_person(gs, event, p["anon_id"])
        await graph_repo.link_looked_at(
            gs,
            tenant_id=event.tenant_id,
            session_id=event.session_id,
            anon_id=p["anon_id"],
            zone_id=p["target_id"],
            duration=float(p["duration"]),
            confidence=float(p.get("confidence") or 0.0),
            started_at=p["started_at"],
        )

    async def _on_detection(self, gs, event: EventLog) -> None:
        # Namespaced by camera, the same way the tracker keys its state, so the
        # two cameras that both call their first visitor `P-001` become two
        # `(:Person)` nodes rather than one person who appears to be in two
        # places. Without this the graph would disagree with every `spatial.*`
        # event written above it, which all carry the namespaced id.
        anon_id = person_key(event.payload)
        if not anon_id:
            raise ValueError(f"perception.detection seq={event.seq} has no anon_id/person_id")

        seen = event.occurred_at.isoformat()
        # first_seen and last_seen both get this timestamp; upsert_person only
        # applies first_seen ON CREATE, so the earliest one wins and later
        # detections only move last_seen forward.
        await graph_repo.upsert_person(
            gs,
            tenant_id=event.tenant_id,
            session_id=event.session_id,
            anon_id=anon_id,
            first_seen=seen,
            last_seen=seen,
        )

    async def _on_zone_enter(self, gs, event: EventLog) -> None:
        p = event.payload
        await self._ensure_person(gs, event, p["anon_id"])
        await graph_repo.link_entered(
            gs,
            tenant_id=event.tenant_id,
            session_id=event.session_id,
            anon_id=p["anon_id"],
            zone_id=p["zone_id"],
            at=p["at"],
        )

    async def _on_zone_exit(self, gs, event: EventLog) -> None:
        p = event.payload
        await self._ensure_person(gs, event, p["anon_id"])
        await graph_repo.link_left(
            gs,
            tenant_id=event.tenant_id,
            session_id=event.session_id,
            anon_id=p["anon_id"],
            zone_id=p["zone_id"],
            at=p["at"],
        )

    async def _on_dwell(self, gs, event: EventLog) -> None:
        p = event.payload
        await self._ensure_person(gs, event, p["anon_id"])
        await graph_repo.link_dwelled_in(
            gs,
            tenant_id=event.tenant_id,
            session_id=event.session_id,
            anon_id=p["anon_id"],
            zone_id=p["zone_id"],
            duration=p["duration"],
            started_at=p["started_at"],
            ended_at=p["ended_at"],
        )

    async def _on_surface_interaction(self, gs, event: EventLog) -> None:
        """(Person)-[:INTERACTED_WITH]->(Surface), the Engagement layer's other half.

        `surface_id` must already exist as a node — the session wizard writes the
        touchpoints at configuration time, exactly as it does zones. A reading
        for a surface nobody configured is dropped rather than conjured into
        existence: the alternative is a report listing touchpoints the operator
        never installed, which is worse than a missing one because nobody would
        think to question it.
        """
        p = event.payload
        surface_id = p.get("surface_id")
        anon_id = p.get("anon_id") or p.get("person_id")
        if not surface_id or not anon_id:
            raise ValueError(
                f"surface.interaction seq={event.seq} missing surface_id or anon_id"
            )

        await self._ensure_person(gs, event, anon_id)
        await graph_repo.link_interacted_with(
            gs,
            tenant_id=event.tenant_id,
            session_id=event.session_id,
            anon_id=anon_id,
            surface_id=surface_id,
            at=p.get("at") or event.occurred_at.isoformat(),
            kind=p.get("kind"),
            duration=p.get("duration"),
        )

    async def _ensure_person(self, gs, event: EventLog, anon_id: str) -> None:
        """Make sure the Person exists before drawing an edge to them.

        link_entered and friends MATCH both endpoints, so a missing Person makes
        the MERGE a silent no-op rather than an error. That can genuinely happen:
        the tracker's spatial event may be consumed before the detection that
        caused it if a cursor was reset to a different point, and read_events
        orders by seq per tenant, not per person.

        upsert_person is idempotent, so calling it here costs one MERGE and
        removes a class of silently-missing edges.
        """
        seen = event.occurred_at.isoformat()
        await graph_repo.upsert_person(
            gs,
            tenant_id=event.tenant_id,
            session_id=event.session_id,
            anon_id=anon_id,
            first_seen=seen,
            last_seen=seen,
        )
