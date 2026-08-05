"""
The broadcast consumer — reads the log, pushes to connected sockets.

## Why this is a consumer and not a hook in the write path

Calling `hub.broadcast()` from inside `append_event` would be fewer lines and
lower latency. It would also be wrong in two ways:

1. It only sees events *this process* appended. Anything written by another
   process — a second producer, a replay tool, psql — would never reach a
   browser, and the failure would be invisible.
2. It couples durability to delivery. A slow or failing socket would sit inside
   the request that is trying to write to the log.

As a `Consumer` it inherits everything already built and tested in `base.py`:
its own cursor, the `seq > last_seq` polling that tolerates gaps, retry, and
dead-lettering. `event-bus-spec.md` §4's model stays true — everything that
reacts to the log is a consumer, with no privileged side doors.

## Latency

`base.py` polls every 100ms while events are flowing. A detection therefore
reaches a browser in roughly one hop (~100ms) if it is broadcast directly, or
two (~200ms) for a `spatial.*` event that the tracker has to derive first. The
target is <500ms (roadmap Phase 1 acceptance, Week 1 task 1.8), so there is
headroom; `consumer_busy_interval_seconds` is the dial if that stops being true.

## Handles everything

Unlike the tracker and graph writer this has no `handles` filter. The browser
decides what it cares about — the live view wants detections, the event log
panel wants everything, and a future surface may want types nobody has written
yet. Filtering here would mean a backend change every time the UI changes its
mind.
"""

from __future__ import annotations

import logging

from app.consumers.base import Consumer
from app.hub import hub
from app.models import EventLog
from app.schemas import to_wire

log = logging.getLogger(__name__)


class BroadcastConsumer(Consumer):
    name = "broadcast"
    handles = ()  # everything

    #: Not retryable, and for a different reason than the tracker's: this
    #: consumer is stateless, but its output is a push to whoever is connected
    #: *now*. Re-sending a stale event to today's sockets does not repair
    #: yesterday's missed frame — it just puts something out of order on a live
    #: screen. A missed broadcast is repaired by the client's `since_seq`
    #: reconnect, which already exists, not by replaying it from a queue.
    retryable = False

    async def handle(self, event: EventLog) -> None:
        """Push one event to whoever is watching that tenant's session.

        Sending to `(tenant_id, session_id)` and nowhere else is what keeps one
        tenant's live feed out of another's browser. The hub has no API for
        sending outside a room, so there is no way to get this wrong by
        accident.

        A room with nobody in it is the normal case — nobody is required to be
        watching for the log to keep working.
        """
        delivered = await hub.broadcast(
            event.tenant_id,
            event.session_id,
            {"type": "event", "event": to_wire(event)},
        )
        if delivered:
            log.debug(
                "broadcast seq=%s to %s socket(s) on %s/%s",
                event.seq,
                delivered,
                event.tenant_id,
                event.session_id,
            )
