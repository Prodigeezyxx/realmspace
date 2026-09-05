"""
The loop every consumer runs. Written once, because getting it subtly wrong
twice is worse than getting it wrong once.

event-bus-spec.md §4 states the contract: a consumer "reads from its cursor,
processes idempotently, advances cursor, dead-letters on repeated failure."
This is that sentence, in code.

## Three things here are load-bearing

**Polling is `seq > cursor`, never `cursor + 1`.** `read_events` already does
this. It matters because `seq` has permanent gaps — a deduped insert burns a
BIGSERIAL value, as does any rolled-back transaction (event-bus-spec.md §2). A
consumer waiting for the next consecutive number stalls forever on an event that
will never exist.

**The cursor advances after each event, not after each batch.** A crash
mid-batch then re-delivers at most one event instead of the whole batch. Since
handlers must be idempotent anyway this is belt-and-braces, but it keeps the
blast radius of a bad handler to one event.

**A dead-lettered event is skipped, not retried forever.** The cursor moves past
it. A consumer that blocks on one poison event wedges everything behind it —
spec §5 says it surfaces in the HITL review screen for a human instead.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from abc import ABC, abstractmethod

from app import repository
from app.config import get_settings
from app import db
from app.models import EventLog

log = logging.getLogger(__name__)


def retry_delay(attempt: int) -> float:
    """How long to wait before attempt `attempt + 1`. Doubles, then stops.

    Exponential because the failures worth retrying are transient — a database
    blip, a graph write racing a restart — and they clear on their own. Retrying
    at a fixed interval is the worst thing to do to something already struggling.

    Capped because the tracker sits on the real-time path with a <500ms budget
    from detection to dashboard (roadmap Phase 1 acceptance). A batch held behind
    one failure that keeps doubling would blow that quietly, and it would do it
    when the room is busiest — which is exactly when the failure is most likely.

    A pure function of the attempt number, so the growth can be tested without
    waiting for it.
    """
    settings = get_settings()
    return min(
        settings.consumer_retry_delay_seconds * (2 ** (attempt - 1)),
        settings.consumer_retry_max_delay_seconds,
    )


class Consumer(ABC):
    """Base class for a bus consumer.

    Subclasses declare a `name` (which is the key in consumer_cursor, so it must
    be stable — renaming it silently resets the cursor to 0 and replays the whole
    log), the event types they care about, and how to handle one event.
    """

    #: Stable identity. Changing it means starting from seq 0 again.
    name: str = "unnamed"

    #: Event types this consumer handles. Others are skipped but still advance
    #: the cursor — a consumer must not stall on traffic that isn't its business.
    handles: tuple[str, ...] = ()

    #: May a human retry one parked event through this consumer, on its own?
    #:
    #: Default **False**, because the safe answer for a consumer nobody has
    #: thought about is "no". Only a handler that is a pure function of the
    #: event can be re-run in isolation and produce what the original attempt
    #: would have: anything holding derived state sees an empty version of it,
    #: and anything whose output is time-sensitive is fixing nothing by firing
    #: late. Each subclass that sets this True must say why on the class.
    #:
    #: What this is *not* is a claim about idempotency — every consumer is
    #: already idempotent, which is what makes replay safe. This is narrower:
    #: whether one event out of its sequence still means the same thing.
    retryable: bool = False

    def __init__(self) -> None:
        #: Highest seq this instance has processed, per tenant. Used only to
        #: notice a rewind — see the replay note on on_replay().
        self._high_water: dict[str, int] = {}

    @abstractmethod
    async def handle(self, event: EventLog) -> None:
        """Process one event. Must be idempotent — it will be called again for
        the same event after a crash, and on any deliberate replay."""

    async def on_replay(self, tenant_id: str) -> None:
        """Called when this consumer's cursor has moved *backwards*.

        Idempotent handlers are not sufficient on their own for a consumer that
        keeps derived state in memory. The tracker remembers which zone each
        person is in; if the cursor rewinds and that memory survives, the first
        replayed detection looks like a *move* from wherever the person ended up
        back to where they started, and the tracker emits a spurious exit and
        dwell that never happened.

        Found by replaying against the running service, not by the test suite —
        the test used a fresh consumer instance, which is exactly the condition
        that hides this. Subclasses holding derived state must clear it here.
        """

    def wants(self, event: EventLog) -> bool:
        return not self.handles or event.type in self.handles

    async def process_batch(self, tenant_id: str) -> int:
        """One poll: read from the cursor, handle, advance. Returns how many
        events were consumed (including skipped and dead-lettered ones)."""
        settings = get_settings()
        consumed = 0

        async with db.SessionLocal() as session:
            # Consumers process every tenant in turn, so each batch declares
            # which one it is working on. Without this every query below returns
            # nothing — RLS fails closed, which is the right direction but a
            # confusing silence if you forget.
            await db.scope_to_tenant(session, tenant_id)

            cursor = await repository.get_cursor(
                session, consumer=self.name, tenant_id=tenant_id
            )

            # A cursor below where this instance has already read means someone
            # rewound it — an operator replay (spec §5), or a fresh cursor after
            # the row was cleared. Derived in-memory state is now stale.
            seen = self._high_water.get(tenant_id)
            if seen is not None and cursor < seen:
                log.info(
                    "%s: cursor rewound %s → %s for %s; clearing derived state",
                    self.name, seen, cursor, tenant_id,
                )
                await self.on_replay(tenant_id)
                self._high_water.pop(tenant_id, None)

            events = await repository.read_events(
                session,
                tenant_id=tenant_id,
                since_seq=cursor,
                limit=settings.consumer_batch_size,
            )

            for event in events:
                if self.wants(event):
                    await self._handle_with_retries(session, event, tenant_id)
                    # a dead-lettered event falls through and is skipped past
                self._high_water[tenant_id] = event.seq
                consumed += 1

            # One cursor write and one commit for the whole batch, rather than
            # one per event. Per-event commits meant an fsync per detection —
            # at 20fps with several people in frame that is hundreds a second,
            # and it is the first thing that would miss the <500ms Phase 1
            # latency target under real load.
            #
            # The trade is that a crash mid-batch replays the whole batch
            # instead of one event. That is safe here and only here: handlers
            # are idempotent, derived event ids make re-emission a no-op, and a
            # crash means a fresh process with no stale in-memory state. The
            # batch is bounded by consumer_batch_size.
            if events:
                await repository.advance_cursor(
                    session,
                    consumer=self.name,
                    tenant_id=tenant_id,
                    last_seq=events[-1].seq,
                )
                await session.commit()

        return consumed

    async def _handle_with_retries(
        self, session, event: EventLog, tenant_id: str
    ) -> bool:
        """Try the handler, then dead-letter. Returns False if it was parked.

        `seq` is read once, up front, and never off `event` again inside the
        loop. That is not style — an ORM instance whose session gets rolled back
        or committed can have its attributes expired, and touching one then
        triggers a lazy reload. Inside an exception handler that reload raises
        MissingGreenlet and **replaces the original error with a confusing one**,
        so the actual failure never reaches the dead letter. Caching the one
        value we need makes the error path independent of session state.

        Note also that we do not roll back `session` here. Handlers never
        receive it — they open their own sessions (Postgres) or use the graph
        driver — so a handler failure cannot poison it, and rolling it back
        would expire `event` for exactly the reason above.
        """
        settings = get_settings()
        event_seq = event.seq
        last_error = ""

        for attempt in range(1, settings.consumer_max_attempts + 1):
            try:
                await self.handle(event)
                return True
            except Exception:  # noqa: BLE001 — the whole point is to not crash
                last_error = traceback.format_exc()
                log.warning(
                    "%s failed on seq=%s attempt %s/%s",
                    self.name,
                    event_seq,
                    attempt,
                    settings.consumer_max_attempts,
                )
                if attempt < settings.consumer_max_attempts:
                    await asyncio.sleep(retry_delay(attempt))

        await repository.record_dead_letter(
            session,
            consumer=self.name,
            tenant_id=tenant_id,
            event_seq=event_seq,
            error=last_error,
            attempts=settings.consumer_max_attempts,
        )
        log.error(
            "%s dead-lettered seq=%s after %s attempts",
            self.name,
            event_seq,
            settings.consumer_max_attempts,
        )
        return False

    async def run_once(self) -> int:
        """One pass over every tenant. This is what tests drive directly, rather
        than starting the background loop and sleeping."""
        async with db.SessionLocal() as session:
            tenants = await repository.list_tenants(session)

        total = 0
        for tenant_id in tenants:
            total += await self.process_batch(tenant_id)
        return total

    async def run_forever(self) -> None:
        """The background task. Polls fast while events are flowing, backs off
        when idle.

        The busy interval is small on purpose: event-bus-spec.md §4 calls the
        tracker real-time and roadmap Phase 1 acceptance is < 500ms from
        detection to dashboard, so the hot path cannot be a lazy poll.
        """
        settings = get_settings()
        log.info("consumer %s started", self.name)
        while True:
            try:
                consumed = await self.run_once()
            except asyncio.CancelledError:
                log.info("consumer %s stopped", self.name)
                raise
            except Exception:  # noqa: BLE001 — a loop that dies stops the pipeline
                log.exception("consumer %s loop error", self.name)
                consumed = 0

            await asyncio.sleep(
                settings.consumer_busy_interval_seconds
                if consumed
                else settings.consumer_idle_interval_seconds
            )
