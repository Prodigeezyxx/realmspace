"""
The retention purge — `gtm.md`'s "30-day data retention", finally meaning it.

  retention.purge_requested → refuse, or empty the window → retention.purged

## A consumer rather than an endpoint, for erasure's reason

The three conditions this refuses on are properties of the *moment the work is
attempted*, not of the moment somebody asked. A consumer gets that for free:
`base.Consumer` retries with backoff and parks the request on `/ops` with the
reason if it keeps failing, which is exactly right — somebody should know that a
tenant's retention window has not been enforced and why.

`consumers/erasure.py` makes the same argument about the same shape.

## What it does

Empties payloads older than the tenant's plan floor (`plans.retention_floor`),
keeping `seq`, `event_id`, `type` and both timestamps, then deletes the derived
`(:Person)` subgraph for any session that is entirely past the window, then moves
the purge watermark so no consumer can be rewound into what is now empty.

`app/retention.py` has the refusals and what is exempt.

## Ordering, and why the watermark is last

Log, then graph, then watermark. A crash between the first two leaves a purged
log and a stale graph, which the next run finishes — the graph delete is a MATCH
that finds nothing the second time. A crash before the watermark leaves a purged
window that a replay is still allowed into, which is the *safe* direction to
fail: the guard is missing rather than a rewind being wrongly permitted through a
watermark set before the work it describes.

## Not retryable in isolation

Unlike the erasure job, whose parked request is a person's rights and should be
one click. A purge that has half-run is a state a human should look at rather
than re-fire from `/ops`: the log for that window may already be empty, and the
right next action is another request, which is idempotent by construction —
`purge_events` skips rows it has already emptied.
"""

from __future__ import annotations

import datetime as dt
import logging

from app import db, plans, repository, retention
from app.config import get_settings
from app.consumers.base import Consumer
from app.consumers.ids import derive_event_id
from app.graph import repository as graph_repo
from app.graph.driver import get_driver
from app.models import EventLog
from app.schemas import EventIn

log = logging.getLogger(__name__)


class RetentionConsumer(Consumer):
    name = "retention"
    handles = (retention.PURGE_REQUESTED,)

    #: See the module docstring. A half-run purge wants a human, not a retry
    #: button — and the correct retry is a fresh request, which is idempotent.
    retryable = False

    async def handle(self, event: EventLog) -> None:
        async with db.SessionLocal() as session:
            await db.scope_to_tenant(session, event.tenant_id)

            plan = await plans.plan_for(session, event.tenant_id)
            floor = plans.retention_floor(plan)
            if floor is None:
                # A tier with no stated retention. `app/plans.py` enforces only
                # what the pricing sheet writes down, and deleting a client's
                # data on a limit nobody sold them would be the worst possible
                # place to start inventing one.
                await self._receipt(
                    event, floor=None, events=0, people=0, through_seq=0,
                    note=f"the {plan} plan states no retention window",
                )
                return

            through_seq = await retention.highest_purgeable_seq(
                session, tenant_id=event.tenant_id, before=floor
            )
            if through_seq == 0:
                await self._receipt(
                    event, floor=floor, events=0, people=0, through_seq=0,
                    note="nothing is past the window",
                )
                return

            # All three raise, and `base.Consumer` turns that into a retry with
            # backoff and then a parked event naming the reason.
            await retention.refuse_if_consumers_are_behind(
                session,
                tenant_id=event.tenant_id,
                through_seq=through_seq,
                consumers=self._live_consumer_names(),
            )
            await retention.refuse_if_an_erasure_is_in_flight(
                session, tenant_id=event.tenant_id
            )
            await retention.refuse_if_a_dead_letter_points_into_the_window(
                session, tenant_id=event.tenant_id, through_seq=through_seq
            )

            at = dt.datetime.now(dt.timezone.utc)
            sessions_past = await self._sessions_entirely_past(
                session, tenant_id=event.tenant_id, floor=floor
            )
            emptied, _ = await repository.purge_events(
                session, tenant_id=event.tenant_id, before=floor, at=at
            )
            await session.commit()

        people = await self._purge_graph(event.tenant_id, sessions_past)

        async with db.SessionLocal() as session:
            await db.scope_to_tenant(session, event.tenant_id)
            # Last, deliberately — see the module docstring on ordering.
            await repository.set_purge_watermark(
                session, tenant_id=event.tenant_id, seq=through_seq, through=floor
            )
            await session.commit()

        await self._receipt(
            event, floor=floor, events=emptied, people=people, through_seq=through_seq
        )

    # ── helpers ───────────────────────────────────────────────────────────────

    def _live_consumer_names(self) -> list[str]:
        """Every consumer that reads the log, minus this one.

        Read from the registry rather than listed here, so a consumer added
        later is covered without anybody remembering — the failure this check
        exists to prevent is precisely one nobody thought about.
        """
        from app.consumers.run import CONSUMER_CLASSES

        return [c.name for c in CONSUMER_CLASSES if c.name != self.name]

    async def _sessions_entirely_past(
        self, session, *, tenant_id: str, floor: dt.datetime
    ) -> list[str]:
        """Sessions whose **latest** event is past the floor.

        The latest and not the earliest: an activation with anything inside the
        window is live, and deleting its visitors would take data the client can
        still legitimately read.
        """
        from sqlalchemy import func, select

        rows = (
            await session.execute(
                select(EventLog.session_id)
                .where(EventLog.tenant_id == tenant_id)
                .group_by(EventLog.session_id)
                .having(func.max(EventLog.occurred_at) < floor)
            )
        ).scalars().all()
        return sorted(rows)

    async def _purge_graph(self, tenant_id: str, session_ids: list[str]) -> int:
        if not session_ids:
            return 0
        settings = get_settings()
        removed = 0
        async with get_driver().session(database=settings.neo4j_database) as gs:
            for session_id in session_ids:
                removed += await graph_repo.purge_people_in_session(
                    gs, tenant_id=tenant_id, session_id=session_id
                )
        return removed

    async def _receipt(
        self,
        event: EventLog,
        *,
        floor: dt.datetime | None,
        events: int,
        people: int,
        through_seq: int,
        note: str | None = None,
    ) -> None:
        """Counts, ids and the floor. Never content.

        On the same log this just emptied, so a receipt quoting what it removed
        would put the data straight back — the argument `erasure.completed`
        makes about itself, and the reason `retention.*` is exempt from being
        purged in turn.
        """
        async with db.SessionLocal() as session:
            await db.scope_to_tenant(session, event.tenant_id)
            await repository.append_event(
                session,
                EventIn(
                    # Derived from the request, so a replay reports the same
                    # completion rather than appending a second one.
                    event_id=derive_event_id(
                        "retention_purged", event.tenant_id, str(event.event_id)
                    ),
                    tenant_id=event.tenant_id,
                    session_id=event.session_id,
                    type=retention.PURGED,
                    payload={
                        "request_event_id": str(event.event_id),
                        "purged_through": floor.isoformat() if floor else None,
                        "purged_before_seq": through_seq,
                        "events_purged": events,
                        "people_purged": people,
                        "completed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                        **({"note": note} if note else {}),
                    },
                    occurred_at=event.occurred_at,
                ),
            )
            await session.commit()
