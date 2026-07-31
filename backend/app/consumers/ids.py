"""
Deterministic event ids for events a consumer produces.

## Why this file exists at all

The tracker is a consumer *and* a producer: it reads `perception.detection` and
writes `spatial.*` back into the same log. Every event needs an `event_id`, and
`schemas.EventIn` requires the producer to supply it.

If the tracker generated a random one, replay would break. Reprocess the same
detection after a crash and you'd mint a *new* id, the UNIQUE constraint on
`event_id` would see nothing to dedupe, and a second `spatial.dwell` would land
in the log. Nothing errors. The dwell is simply counted twice, and every ROI
number built on it (`roi-framework.md` §2, Layer 2) is quietly wrong.

So a derived event's id must be a pure function of what caused it. Same cause,
same id, forever — and the bus's existing idempotency does the rest.
"""

from __future__ import annotations

import uuid

# A fixed, arbitrary UUID that namespaces every id realmspace derives. It must
# never change: change it and every previously-derived id changes with it, so
# replaying old events would produce a parallel set of duplicates.
#
# uuid5 of the DNS namespace for "realmspace.floats.xr" — stable and traceable
# rather than a magic constant somebody typed.
NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "realmspace.floats.xr")


#: ASCII 0x1F, "unit separator" — a control character defined for exactly this
#: job and one that cannot occur in any part we pass: tenant and zone ids,
#: anon_ids, event type names and ISO-8601 timestamps are all printable text.
#:
#: The choice matters. With a printable separator like ":" the parts become
#: ambiguous — ("a:b", "c") and ("a", "b:c") both join to "a:b:c" and collide,
#: so two genuinely different events would share an event_id and the second
#: would be silently swallowed by the bus as a duplicate. Zone ids and session
#: ids are operator-supplied strings, so assuming they contain no colons is a
#: bet, not a guarantee.
_SEP = "\x1f"


def derive_event_id(*parts: str | int) -> uuid.UUID:
    """Build a stable event_id from the identity of whatever caused the event.

    uuid5 hashes a namespace plus a name into a UUID: same inputs, same output,
    forever, on any machine. (uuid4 is random and uuid1 uses the clock and MAC
    address — neither is reproducible, which is the one property needed here.)

    Callers pass the causal identity of the event, e.g.
    ("tracker", tenant_id, session_id, "spatial.dwell", anon_id, zone_id,
    entered_at). Reprocess the same input and you get the same id, so the bus's
    UNIQUE constraint on event_id turns the replay into a no-op.

    Used by consumers/tracker.py for every spatial.* event it emits.
    """
    return uuid.uuid5(NAMESPACE, _SEP.join(str(part) for part in parts))
