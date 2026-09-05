"""
How many people are in a zone right now, against the capacity the operator set.

    spatial.zone_enter / zone_exit  →  spatial.occupancy  →  rules, /live

`roadmap.md`'s Phase 5 floor orchestrator is deferred with two unmet
preconditions — no pilot has asked for it, and no trajectory-prediction model has
been chosen — and it records the half that needs neither: *"live occupancy
against a configured capacity, prompting through the Phase 3 rules engine … is
measurement, not prediction."* This is that half. Nothing here predicts anything.

## What was already waiting for it

`Zone.capacity` is specified end to end and read by nothing. `data-model.md`
declares it "optional, for crowding alerts"; `schemas.ZoneConfig` accepts it,
`graph_repo.upsert_zone` stores it and `zones_for_session` returns it; the
wizard's zone editor has a field for it. An operator sets a number and it goes
nowhere — the same shape `(:Group)` was in before Phase 6, constrained in the
schema with no producer.

And a crowding rule could not be written. `spatial.dwell` is emitted when
somebody *leaves*, so "five people at the entrance" was only knowable after they
had gone, which is the wrong end of the event for the floor.

## It emits on crossings, not on arrivals

Under-capacity → at-or-over emits `status: "over"`. Back under emits
`"cleared"`. An event per person entering a room that is already full would be
`consumers/gaze.py`'s rule — a signal that fires on the common case looks like
coverage — and downstream it is `consumers/dispatch.py`'s furniture: a staff
prompt raised nine times in a minute teaches the floor to stop reading the panel.
The rules engine gets one event per crossing and needs `condition.any`.

## A zone with no capacity emits nothing

Not a default, at any count. A threshold nobody set is not a threshold, and
inventing one here is the rule `roi-framework.md` imposes on revenue and P3's
cost tile imposed on spend: a number the client never stated is not ours to
supply. `ZoneEditor` leaves the field blank, and blank has to keep meaning
"unmeasured" rather than becoming zero.

## Occupancy is read from the log, by event time

Not from the graph: `DWELLED_IN` is written from `spatial.dwell`, which the
tracker emits on the way out, so at the moment a room is full the graph holds
nothing about the people standing in it (`consumers/touch.py` says the same about
a tap). And not in log order: the 2026-08-25 walk found the scorecard reading 1
where three visitors overlapped, because a producer appending one visitor's whole
journey at a time — or a batch replayed after an outage — arrives in one order
and happened in another.

So the occupant set is recomputed from the log at each evaluation rather than
accumulated in memory. That is ADR-002's first correction ("the window is derived
from the bus, not held in memory") and it is what makes this replayable: a
restart mid-activation, or a cursor rewound to 0, re-derives the same crossings
and therefore the same `event_id`s, and the bus dedupes them. Accumulated state
would fire a second time from a different seq, and the derived id would not
collide because the seq is part of it.

**The cost is stated rather than hidden**: one log read per zone transition into a
zone that has a capacity. `consumers/touch.py` already accepted that shape per
tap. Correctness under an out-of-order log is worth more here than the scan, and
the alternative — a window — has to assume a longest possible visit and drops
whoever has been standing there since before it.

## What the threshold cannot survive

Capacity lives in the graph, which is current state. An operator who raises a
zone's capacity mid-activation changes what a replay of yesterday decides, and
there is no version of the number on the log to read instead. Stated here rather
than papered over: the alternative is putting capacity in every spatial payload,
which is a schema change for a figure that is the operator's configuration and
not a measurement.

## Its own consumer

Beside `grouping.py` and `gaze.py`, for their reason: the tracker is the <500ms
path the Phase 1 acceptance is measured on, and a crowding bug must not be able
to stop dwell being measured.
"""

from __future__ import annotations

import datetime as dt
import logging

from app import db, repository
from app.config import get_settings
from app.consumers.base import Consumer
from app.consumers.ids import derive_event_id
from app.graph import repository as graph_repo
from app.graph.driver import get_driver
from app.models import EventLog
from app.schemas import EventIn

log = logging.getLogger(__name__)

ZONE_ENTER = "spatial.zone_enter"
ZONE_EXIT = "spatial.zone_exit"
OCCUPANCY = "spatial.occupancy"

#: A zone that has reached or passed the capacity its operator set.
STATUS_OVER = "over"
#: …and has since dropped back below it.
STATUS_CLEARED = "cleared"


async def occupants_at(
    session,
    *,
    tenant_id: str,
    session_id: str,
    zone_id: str,
    at: dt.datetime,
) -> set[str]:
    """Who the tracker says was inside `zone_id` at `at`.

    One definition, two callers — this consumer and `consumers/touch.py`, which
    is where it was written. A second copy would let a tap be attributed to
    somebody `/live` says is not in the room, and neither answer would be
    obviously the wrong one. Same argument `consumers/zones.py` makes for being a
    port of `zone-detect.ts` rather than a second implementation of it.

    Walked by **event time** rather than by seq, because the two disagree exactly
    when it matters: a batch replayed after an outage arrives in one order and
    happened in another. `consumers/insights.py` and the scorecard's
    peak-occupancy pass both learned this, the second of them by reading 1 where
    three people overlapped.

    Read whole rather than windowed. A window would have to assume a longest
    possible visit, and being wrong about that drops somebody who has been
    standing there since before it — turning two occupants into one, which is the
    one error that produces a *confident wrong attribution* rather than a
    refusal.
    """
    inside: set[str] = set()
    since_seq = 0

    while True:
        rows = await repository.read_events(
            session,
            tenant_id=tenant_id,
            session_id=session_id,
            types=(ZONE_ENTER, ZONE_EXIT),
            since_seq=since_seq,
            limit=repository.MAX_LIMIT,
        )
        if not rows:
            break

        for row in rows:
            if row.occurred_at > at:
                continue
            p = row.payload
            if p.get("zone_id") != zone_id:
                continue
            anon_id = p.get("anon_id")
            if not anon_id:
                continue
            if row.type == ZONE_ENTER:
                inside.add(anon_id)
            else:
                inside.discard(anon_id)

        since_seq = rows[-1].seq
        if len(rows) < repository.MAX_LIMIT:
            break

    return inside


class OccupancyConsumer(Consumer):
    name = "occupancy"
    handles = (ZONE_ENTER, ZONE_EXIT)

    #: A pure function of the log: the occupant set is recomputed rather than
    #: accumulated, and the emitted id derives from the transition that caused
    #: it. Re-running one parked event gives exactly what the first attempt
    #: would have, which is what this flag asks.
    retryable = True

    async def handle(self, event: EventLog) -> None:
        payload = event.payload
        zone_id = payload.get("zone_id")
        anon_id = payload.get("anon_id")
        if not zone_id or not anon_id:
            # The tracker always sets both. A row without them came from
            # somewhere a retry will not fix, and the crossing it would have
            # caused is one transition of noise either way.
            log.warning(
                "occupancy: seq=%s has no zone_id or anon_id", event.seq
            )
            return

        capacity, zone_name = await self._capacity_of(
            tenant_id=event.tenant_id,
            session_id=event.session_id,
            zone_id=zone_id,
        )
        if capacity is None:
            return

        async with db.SessionLocal() as session:
            await db.scope_to_tenant(session, event.tenant_id)

            inside = await occupants_at(
                session,
                tenant_id=event.tenant_id,
                session_id=event.session_id,
                zone_id=zone_id,
                at=event.occurred_at,
            )

            # `inside` already accounts for this event, since it is on the log by
            # the time we read it. The count *before* it is therefore the same
            # set with this one person's transition undone — which is also why a
            # duplicate enter for somebody already inside crosses nothing.
            after = len(inside)
            if event.type == ZONE_ENTER:
                before = len(inside - {anon_id})
            else:
                before = len(inside | {anon_id})

            status = self._crossing(before, after, capacity)
            if status is None:
                return

            await repository.append_event(
                session,
                EventIn(
                    # Keyed on the transition that crossed the line, so a replay
                    # re-derives it and the bus dedupes. The status is in the id
                    # because one seq can only cause one crossing, and naming it
                    # makes a duplicated id legible if that ever stops being
                    # true.
                    event_id=derive_event_id(
                        "occupancy",
                        event.tenant_id,
                        event.session_id,
                        zone_id,
                        status,
                        event.seq,
                    ),
                    tenant_id=event.tenant_id,
                    session_id=event.session_id,
                    type=OCCUPANCY,
                    payload={
                        "zone_id": zone_id,
                        # As `spatial.*` events carry `zone_name` and a touch
                        # carries `surface_label`: an insight or a staff prompt
                        # naming `z_entry_arch` is one an operator has to
                        # translate on the floor.
                        "zone_name": zone_name or zone_id,
                        "occupancy": after,
                        "capacity": capacity,
                        "status": status,
                        "at": event.occurred_at.isoformat(),
                    },
                    # The crossing happened when the person moved, not when this
                    # consumer noticed. Event time everywhere, so a replay of an
                    # activation reproduces its own timeline.
                    occurred_at=event.occurred_at,
                ),
            )
            await session.commit()

    @staticmethod
    def _crossing(before: int, after: int, capacity: int) -> str | None:
        """Which line was crossed by going from `before` to `after`, if either.

        At capacity counts as over: a zone an operator called a ten-person zone
        is full at ten, not at eleven. Deliberately not a hysteresis band —
        somebody stepping in and out of a full zone's boundary produces a real
        pair of crossings, and the tracker's own confirm window and dropout
        sweep are where boundary flicker is already dealt with
        (`event-bus-spec.md`, "session hygiene"). A second, quieter definition of
        the same thing here would be a place for the two to disagree.
        """
        if before < capacity <= after:
            return STATUS_OVER
        if after < capacity <= before:
            return STATUS_CLEARED
        return None

    async def _capacity_of(
        self, *, tenant_id: str, session_id: str, zone_id: str
    ) -> tuple[int | None, str | None]:
        """This zone's capacity and name, or `(None, None)` if it has none.

        Read fresh rather than cached, unlike the tracker's polygons. That cache
        exists because a graph round trip per detection at 20fps would be absurd;
        this runs once per zone transition, off the <500ms path, and the thing
        being read is the threshold an alert fires on. A stale one raises the
        wrong alarm on a floor somebody is standing on.
        """
        settings = get_settings()
        async with get_driver().session(database=settings.neo4j_database) as gs:
            zones = await graph_repo.zones_for_session(
                gs, tenant_id=tenant_id, session_id=session_id, include_undrawn=True
            )
        for zone in zones:
            if zone.get("id") != zone_id:
                continue
            capacity = zone.get("capacity")
            if capacity is None:
                return None, None
            return int(capacity), zone.get("name")
        # The operator removed the zone. `graph_writer` declines an event for a
        # zone nobody configured rather than conjuring one; this declines one
        # step earlier, and the transitions themselves stay on the log.
        return None, None
