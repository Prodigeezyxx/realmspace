"""
The graph writer — event-bus-spec.md §4, "events → nodes/edges
(data-model.md)", near-real-time, on the edge.

Reads both families and writes through app/graph/repository.py:

  perception.detection  → upsert_person       (first_seen / last_seen advance)
  spatial.zone_enter    → (Person)-[:ENTERED]->(Zone)
  spatial.zone_exit     → (Person)-[:LEFT]->(Zone)
  spatial.dwell         → (Person)-[:DWELLED_IN {duration,…}]->(Zone)

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
from app.graph import repository as graph_repo
from app.graph.driver import get_driver
from app.models import EventLog

log = logging.getLogger(__name__)

DETECTION = "perception.detection"
ZONE_ENTER = "spatial.zone_enter"
ZONE_EXIT = "spatial.zone_exit"
DWELL = "spatial.dwell"


class GraphWriterConsumer(Consumer):
    name = "graph_writer"
    handles = (DETECTION, ZONE_ENTER, ZONE_EXIT, DWELL)

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

    async def _on_detection(self, gs, event: EventLog) -> None:
        anon_id = event.payload.get("person_id")
        if not anon_id:
            raise ValueError(f"perception.detection seq={event.seq} has no person_id")

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
