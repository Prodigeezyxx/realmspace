"""
Who pressed it — the consumer between a tablet's tap and the Engagement layer.

    surface.touched (tablet)  →  surface.interaction (this)  →  graph, ROI

`surface.interaction` has had every reader since Phase 2 and no producer, and
the roadmap's reason was that "real interactions need booth hardware". A tablet
running `/touch` is that hardware — but a tablet has no camera, so it can attest
that its button was pressed and cannot attest to who pressed it. That is the
whole of what this file is for.

Same split as `perception.detection` → `consumers/tracker.py` → `spatial.*`: the
producer emits the raw fact it can actually observe, and the consumer, which is
where the state lives, derives the rest.

## What it refuses, which is most of the design

`graph_writer._on_surface_interaction` requires an `anon_id` and
`scorecard.ts` counts one into `engagedPersons`, so an interaction *is* a claim
about a person. The only honest source of that claim is zone occupancy: who the
tracker says was standing in the surface's zone when the finger landed.

  - a surface with no `zone_id` resolves nothing — the operator never said where
    it is, and the nearest visitor is not an answer;
  - **exactly one** person in the zone attributes the tap to them;
  - two or more refuses, because picking the closest, the newest or the longest
    dwelling one is inventing the observation the tablet could not make;
  - nobody in the zone refuses too, and that case is real rather than
    theoretical: a member of staff demonstrating the touchpoint is a person the
    cameras may not have as a visitor at all.

This is `consumers/gaze.py`'s rule — "a signal that fires on the common case
looks like coverage" — and `consumers/grouping.py`'s refusal to read proximity
as company. A busy stand will refuse most taps.

**And that refusal costs nothing that was previously being measured**, which is
the point worth keeping. `surface.touched` is on the log either way, so the
report counts every tap as an interaction and only counts a *visitor* as engaged
when we know which visitor. Two claims, kept apart: "we measured 40 taps" and
"we can name 11 of the people who made them". That is the same distinction the
scorecard already makes between an absence and a zero.

## Occupancy is read from the log, not from the graph

The graph writes `DWELLED_IN` from `spatial.dwell`, which the tracker emits when
somebody *leaves* a zone. At the moment of a tap the person is still standing
there and no dwell exists, so the graph would answer "nobody" live and "one
person" on a replay of the same log — the same input giving two answers.

So this reads the tracker's `spatial.zone_enter` / `zone_exit` and walks them by
**event time**, which is the same in both directions.

## Why it waits for the tracker

The zone_enter that puts somebody in the room is appended when the tracker
*processes* the detection, which can be after the tap it explains has already
landed. Resolving immediately would read an empty zone and refuse a tap that was
perfectly attributable a poll later — and, worse, refuse it *permanently*, since
the cursor would have moved on.

So a touch whose `seq` the tracker has not yet passed raises, and the base
class's retry and backoff do the waiting. That is the shape
`consumers/erasure.py` uses to wait for a retraction, for the same reason: the
answer is not wrong yet, it is not available yet.
"""

from __future__ import annotations

import datetime as dt
import logging

from app import db, repository
from app.consumers.base import Consumer
from app.consumers.ids import derive_event_id
from app.config import get_settings
from app.graph import repository as graph_repo
from app.graph.driver import get_driver
from app.models import EventLog
from app.schemas import EventIn

log = logging.getLogger(__name__)

TOUCHED = "surface.touched"
INTERACTION = "surface.interaction"
ZONE_ENTER = "spatial.zone_enter"
ZONE_EXIT = "spatial.zone_exit"

#: The consumer whose output this reads. Named rather than inferred, because the
#: wait below is a correctness condition and a renamed tracker should break
#: loudly here rather than silently stop waiting.
TRACKER = "tracker"


class TouchNotResolvableYet(RuntimeError):
    """The tracker has not caught up. Retry, do not drop.

    Its own type so the retry is legible in the log and on `/ops` as waiting
    rather than as a failure — the difference between "this will work shortly"
    and "this needs a human".
    """


class TouchConsumer(Consumer):
    name = "touch"
    handles = (TOUCHED,)

    #: Retryable, and it has to be: the ordinary case is a tap that arrives
    #: before the tracker has explained it. A pure function of the log — the
    #: emitted id derives from the touch, and occupancy is recomputed rather
    #: than accumulated — so re-running one parked event gives what the first
    #: attempt would have.
    retryable = True

    async def handle(self, event: EventLog) -> None:
        payload = event.payload
        surface_id = payload.get("surface_id")
        if not surface_id:
            # Not raised: the tablet cannot produce this, so a row like it came
            # from somewhere that will not be fixed by retrying.
            log.warning("touch: seq=%s has no surface_id", event.seq)
            return

        settings = get_settings()
        async with get_driver().session(database=settings.neo4j_database) as gs:
            surface = await graph_repo.surface(
                gs,
                tenant_id=event.tenant_id,
                session_id=event.session_id,
                surface_id=surface_id,
            )

        if surface is None:
            # The operator removed the touchpoint. `graph_writer` would decline
            # the interaction anyway — "a reading for a surface nobody
            # configured is dropped rather than conjured into existence" — so
            # this declines one step earlier and says which surface.
            log.info(
                "touch: %s is not a configured touchpoint on %s; no interaction",
                surface_id,
                event.session_id,
            )
            return

        zone_id = surface.get("zone_id")
        if not zone_id:
            # The tap is measured; the person is not. See the module docstring:
            # the nearest visitor is not an answer to a question nobody asked
            # the operator.
            log.info(
                "touch: %s is in no zone, so the tap is counted and not "
                "attributed — set a zone on the touchpoint to attribute it",
                surface_id,
            )
            return

        async with db.SessionLocal() as session:
            await db.scope_to_tenant(session, event.tenant_id)

            tracker_at = await repository.get_cursor(
                session, consumer=TRACKER, tenant_id=event.tenant_id
            )
            if tracker_at < event.seq:
                raise TouchNotResolvableYet(
                    f"tracker is at seq {tracker_at} and this tap is seq "
                    f"{event.seq}; who was in {zone_id} is not knowable yet"
                )

            occupants = await self._occupants(
                session,
                tenant_id=event.tenant_id,
                session_id=event.session_id,
                zone_id=zone_id,
                at=event.occurred_at,
            )

            if len(occupants) != 1:
                log.info(
                    "touch: %s people in %s when %s was pressed; the tap is "
                    "counted and not attributed",
                    len(occupants),
                    zone_id,
                    surface_id,
                )
                return

            anon_id = next(iter(occupants))
            await repository.append_event(
                session,
                EventIn(
                    # One interaction per tap. Derived from the touch's own id
                    # so a replay re-derives it and the log dedupes, rather than
                    # doubling a touchpoint's tally.
                    event_id=derive_event_id(
                        "touch_interaction", event.tenant_id, str(event.event_id)
                    ),
                    tenant_id=event.tenant_id,
                    session_id=event.session_id,
                    type=INTERACTION,
                    payload={
                        "anon_id": anon_id,
                        "surface_id": surface_id,
                        "surface_label": surface.get("label") or surface_id,
                        "kind": payload.get("kind") or "tap",
                        "at": payload.get("at")
                        or event.occurred_at.isoformat(),
                        # Which tap this explains, so an operator comparing the
                        # two tallies can find the ones that were attributed.
                        "touch_id": payload.get("touch_id"),
                        # How we know. The zone, not a proximity score: there is
                        # no distance here to report.
                        "attributed_by": "zone_occupancy",
                        "zone_id": zone_id,
                    },
                    occurred_at=event.occurred_at,
                ),
            )
            await session.commit()

    async def _occupants(
        self,
        session,
        *,
        tenant_id: str,
        session_id: str,
        zone_id: str,
        at: dt.datetime,
    ) -> set[str]:
        """Who the tracker says was inside `zone_id` at `at`.

        Walked by **event time** rather than by seq, because the two disagree
        exactly when it matters: a batch replayed after an outage arrives in one
        order and happened in another. `consumers/insights.py` and the
        scorecard's peak-occupancy pass both learned this, the second of them by
        reading 1 where three people overlapped.

        Read whole rather than windowed. A window would have to assume a longest
        possible visit, and being wrong about that drops somebody who has been
        standing there since before it — turning two occupants into one, which
        is the one error that produces a *confident wrong attribution* rather
        than a refusal.
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
