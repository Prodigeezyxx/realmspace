"""
A session digest, computed from the log so every claim can be traced back to it.

`floats-agent`'s roadmap specifies the insight agent more precisely than ours
did, and this track adopts that wording verbatim — the same move ADR-002 made
with their rule spec, so two tracks do not grow two insight contracts:

> **Insight agent**: periodic bounded graph snapshot → `insight.generated` with
> text + supporting event IDs; shown on `/live`; click-through opens the
> underlying events.
> **Event snapshotting for LLM** — per-session digest, token-bounded.

Two words in that do the work: *supporting event IDs*, and *bounded*.

## Why the digest is built from the log and not the graph

The graph holds current state. `dwell_by_zone` will tell you Product Pod averages
180 seconds, and it cannot tell you which events say so — there is no id to
carry. An insight built that way is an assertion a reader has to take on trust,
which is the same failure as the report's invented `1,287 visitors`: it reads as
authority and cannot be checked.

So the window is read from the event log (`repository.read_window`, the read the
rules evaluator already uses), the numbers are computed here, and **the ids of
the events each number came from travel with it**. `/live` opens them.

## Bounded, and the bound is visible

`MAX_EVENTS` caps the read. Past it the digest says `truncated`, the way
`routers/ledger.py` does, rather than quietly describing part of a window as
though it were the whole. `MAX_REFS` caps the citations per claim, because a
thousand event ids in a payload is not traceability, it is a haystack.

## Anonymous by construction

Only `spatial.*` and `surface.interaction` are read. No consent, no handoff, no
contact — an insight is about the room, which is why `insight.generated` sits in
`ANONYMOUS_EVENT_TYPES` in the browser contract and on the anonymised cloud-sync
path in `event-bus-spec.md` §6. The type filter here is what makes that true
rather than hoped for.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from app import repository
from app.models import EventLog

#: The types a digest may read. See the module docstring — this list is the
#: privacy property, not a performance one.
SOURCE_TYPES = (
    "spatial.zone_enter",
    "spatial.dwell",
    "spatial.passby",
    "surface.interaction",
)

#: Per type, per window. A busy activation can outrun this; saying so is the
#: point of `truncated`.
MAX_EVENTS = 500

#: Citations per claim. Enough to check, few enough to read.
MAX_REFS = 20


@dataclass
class Digest:
    """What happened in one window, and which events say so."""

    window_from: dt.datetime
    window_to: dt.datetime
    people: int = 0
    entries: int = 0
    #: zone id → {"zone", "seconds", "visits", "refs"}
    zones: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: surface id → {"surface", "interactions", "refs"}
    surfaces: dict[str, dict[str, Any]] = field(default_factory=dict)
    passbys: int = 0
    truncated: bool = False

    @property
    def empty(self) -> bool:
        return self.entries == 0 and not self.zones and not self.surfaces

    def top_zone(self) -> dict[str, Any] | None:
        """The zone that held attention longest in this window."""
        ranked = [z for z in self.zones.values() if z["seconds"] > 0]
        return max(ranked, key=lambda z: z["seconds"]) if ranked else None

    def refs(self) -> list[dict[str, Any]]:
        """The events the text rests on, newest claim first.

        Deliberately the *supporting* events rather than every event in the
        window: an insight about Product Pod cites the dwells in Product Pod. A
        reader following them should land on the thing being claimed, not on the
        window's whole traffic.
        """
        top = self.top_zone()
        collected: list[dict[str, Any]] = list(top["refs"]) if top else []
        for surface in self.surfaces.values():
            collected.extend(surface["refs"])
        # Stable and de-duplicated: one event can support two claims.
        seen: set[int] = set()
        unique = []
        for ref in collected:
            if ref["seq"] not in seen:
                seen.add(ref["seq"])
                unique.append(ref)
        return unique[:MAX_REFS]

    def measurements(self) -> dict[str, Any]:
        """The numbers the text was drawn from, carried beside it.

        The same `grounded_in` idea `consumers/sdr.py` uses, and for the same
        reason: a reviewer checks the sentence against these rather than
        trusting it.
        """
        top = self.top_zone()
        return {
            "people": self.people,
            "zone_entries": self.entries,
            "passbys": self.passbys,
            "top_zone": top["zone"] if top else None,
            "top_zone_seconds": round(top["seconds"], 1) if top else None,
            "zones": [
                {
                    "zone": z["zone"],
                    "seconds": round(z["seconds"], 1),
                    "visits": z["visits"],
                }
                for z in sorted(
                    self.zones.values(), key=lambda z: z["seconds"], reverse=True
                )
            ],
            "surfaces": [
                {"surface": s["surface"], "interactions": s["interactions"]}
                for s in sorted(
                    self.surfaces.values(),
                    key=lambda s: s["interactions"],
                    reverse=True,
                )
            ],
        }


async def build(
    session,
    *,
    tenant_id: str,
    session_id: str,
    window_from: dt.datetime,
    window_to: dt.datetime,
    before_seq: int,
) -> Digest:
    """Read one window and fold it into a digest.

    `before_seq` is not optional for the reason `repository.read_window` states:
    it is what makes a replay reproduce the original run. Without it an insight
    replayed at seq 100 would summarise events that, at the moment being
    replayed, had not arrived.
    """
    digest = Digest(window_from=window_from, window_to=window_to)
    people: set[str] = set()

    for type in SOURCE_TYPES:
        rows = await repository.read_window(
            session,
            tenant_id=tenant_id,
            session_id=session_id,
            type=type,
            since=window_from,
            until=window_to,
            before_seq=before_seq,
            limit=MAX_EVENTS,
            # `[from, to)` — fixed contiguous windows, so every event belongs
            # to exactly one: the session's first is not dropped on the
            # boundary it starts at, and an event landing exactly on a
            # boundary is not counted in both. See `repository.read_window`.
            start_inclusive=True,
            end_inclusive=False,
        )
        if len(rows) >= MAX_EVENTS:
            digest.truncated = True

        for row in rows:
            anon_id = row.payload.get("anon_id")
            if anon_id:
                people.add(str(anon_id))
            _absorb(digest, row)

    digest.people = len(people)
    return digest


def _absorb(digest: Digest, row: EventLog) -> None:
    payload = row.payload
    ref = {"seq": row.seq, "event_id": str(row.event_id)}

    if row.type == "spatial.zone_enter":
        digest.entries += 1
        zone = _zone(digest, payload)
        zone["visits"] += 1
        _cite(zone, ref)

    elif row.type == "spatial.dwell":
        zone = _zone(digest, payload)
        # `duration` is seconds (event-bus-spec.md §3). A dwell closed by a
        # dropout is a lower bound, not a measurement — it is counted, because
        # excluding it would understate a zone, and the distinction belongs in
        # anything that ranks rather than in the sum.
        zone["seconds"] += float(payload.get("duration") or 0)
        _cite(zone, ref)

    elif row.type == "spatial.passby":
        digest.passbys += 1

    elif row.type == "surface.interaction":
        surface_id = str(payload.get("surface_id") or "unknown")
        surface = digest.surfaces.setdefault(
            surface_id,
            {
                "surface": payload.get("surface_label") or surface_id,
                "interactions": 0,
                "refs": [],
            },
        )
        surface["interactions"] += 1
        _cite(surface, ref)


def _zone(digest: Digest, payload: dict[str, Any]) -> dict[str, Any]:
    zone_id = str(payload.get("zone_id") or "unknown")
    return digest.zones.setdefault(
        zone_id,
        {
            "zone_id": zone_id,
            "zone": payload.get("zone_name") or zone_id,
            "seconds": 0.0,
            "visits": 0,
            "refs": [],
        },
    )


def _cite(bucket: dict[str, Any], ref: dict[str, Any]) -> None:
    if len(bucket["refs"]) < MAX_REFS:
        bucket["refs"].append(ref)
