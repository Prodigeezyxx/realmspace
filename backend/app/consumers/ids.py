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


#: Separates a camera id from the track id it namespaces. Printable, unlike
#: `_SEP` above, because this one is not an internal hash input: it ends up in
#: `Person.anon_id` in the graph, in every `spatial.*` payload, and on screen in
#: the twin. A control character there would be unreadable and unloggable.
#:
#: Printable brings back the ambiguity `_SEP` exists to avoid — ("cam/1", "P-2")
#: and ("cam", "1/P-2") would both join to "cam/1/P-2" — so the ambiguity is
#: removed at the other end instead: `schemas.CameraConfig` refuses a camera id
#: containing this character. One camera id, one reading.
CAMERA_SEP = "/"


def person_key(payload: dict) -> str | None:
    """Who a detection is about: the track id, namespaced by the camera.

    ByteTrack numbers people per process, from `P-001` up. Two cameras are two
    processes (`perception/realmspace.py` is one script per camera), so both
    emit `P-001` on their first visitor and every consumer keyed on the bare id
    treats those two people as one — merged dwells, one `(:Person)`, an
    activation that under-reports its audience while over-reporting engagement.
    Namespacing by camera is what keeps them apart.

    **A detection with no `camera_id` keeps its bare id**, and that is
    load-bearing rather than lenient. Every event logged before Phase 6 lacks
    the field, and `derive_event_id` builds spatial event ids out of this value,
    so a bare fallback is what makes a replay of an old session produce the same
    ids it produced the first time. Change it and the bus stops recognising its
    own history as a duplicate. The same reasoning put `drift.py`'s
    "unattributed" bucket where it is.

    The ambiguity that fallback creates — is `P-001` a camera-less detection or
    a camera called nothing? — is closed by `tracker.py` refusing a detection
    with no `camera_id` on a session that declares more than one camera. Bare
    ids are accepted only where they cannot collide.

    Returns None when there is no id at all; the caller decides whether that is
    a dead letter.
    """
    anon = payload.get("anon_id") or payload.get("person_id")
    if not anon:
        return None
    camera = payload.get("camera_id")
    return f"{camera}{CAMERA_SEP}{anon}" if camera else str(anon)
