"""
The `spatial_intent` block — "the realmspace differentiator" (`integrations.md` §2).

It is the part of a lead no CRM could have known: which zones this person stood
in, for how long, what they touched, and in what order. Everything here is
arithmetic over rows `graph_repo.spatial_intent_for` fetched, so it is testable
without a graph.

## `attention_score` is in seconds, and the contract's example was not

`integrations.md` §2 illustrates the field as `0.82`, which reads as a 0–1
ratio. `roi-framework.md` §2 defines dwell-weighted attention as
`Σ(dwell × weight)`, which is a quantity in seconds — and there is no
denominator anywhere in the framework that would turn one into the other.
Normalising it here would mean inventing that denominator.

So the value is the definition the framework gives, and `attention_basis` names
the unit so a destination mapping it into a CRM field knows what it is holding.
The example in the doc is corrected to match rather than the number being bent
to match the example.

## Zone weights default to 1.0

A session whose operator never set weights gets plain dwell seconds, which is
the right degradation: unweighted attention is still a real measurement, and
refusing to compute one because nobody expressed a preference would drop the
signal entirely.
"""

from __future__ import annotations

from typing import Any

#: Names the unit of `attention_score`, since the number alone does not.
ATTENTION_BASIS = "weighted_dwell_seconds"


def _sorted_dwells(dwells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Chronological by arrival. `started_at` is an ISO-8601 string, which sorts
    lexicographically in the same order it sorts chronologically — true because
    the tracker always writes UTC with a fixed offset. Rows missing it sort last
    rather than raising, because a path with one undated dwell is still a path."""
    return sorted(dwells, key=lambda d: (d.get("started_at") is None, d.get("started_at") or ""))


def build(
    dwells: list[dict[str, Any]],
    surfaces: list[dict[str, Any]],
    *,
    surfaces_available: int | None = None,
    max_funnel_order: int | None = None,
) -> dict[str, Any]:
    """Assemble the block. `dwells` and `surfaces` come from `spatial_intent_for`.

    `surfaces_available` and `max_funnel_order` describe what the activation
    *offered*, and are carried through rather than used here — the score needs
    them, and reading them twice from two places is how the two drift.
    """
    ordered = _sorted_dwells(dwells)

    # Zones in the order first entered, de-duplicated. A visitor who returns to
    # the entrance on the way out has visited it once, not twice — this is the
    # list a salesperson reads as a route.
    zones_visited: list[str] = []
    for dwell in ordered:
        name = dwell.get("zone") or dwell.get("zone_id")
        if name and name not in zones_visited:
            zones_visited.append(name)

    # Totalled per zone before ranking, not per dwell. Somebody who visited a pod
    # three times for a minute each is more interested in it than somebody who
    # stood in the entrance for two minutes once, and picking the longest single
    # dwell would say the opposite.
    per_zone: dict[str, float] = {}
    weighted = 0.0
    total = 0.0
    for dwell in ordered:
        name = dwell.get("zone") or dwell.get("zone_id")
        duration = float(dwell.get("duration") or 0.0)
        total += duration
        weighted += duration * float(dwell.get("weight") or 1.0)
        if name:
            per_zone[name] = per_zone.get(name, 0.0) + duration

    depth = max(
        (int(d["funnel_order"]) for d in ordered if d.get("funnel_order") is not None),
        default=None,
    )

    engaged: list[str] = []
    for surface in surfaces:
        label = surface.get("label") or surface.get("surface_id")
        if label and label not in engaged:
            engaged.append(label)

    return {
        "zones_visited": zones_visited,
        "top_dwell_zone": max(per_zone, key=per_zone.__getitem__) if per_zone else None,
        "dwell_seconds_total": round(total, 1),
        "surfaces_engaged": engaged,
        "attention_score": round(weighted, 1),
        "attention_basis": ATTENTION_BASIS,
        "funnel_depth_reached": depth,
        "path_summary": summarise(ordered, engaged),
        # Carried so the scorer and anything reading the handoff later work from
        # the same denominators the score was computed against.
        "surfaces_available": surfaces_available,
        "max_funnel_order": max_funnel_order,
    }


def summarise(ordered: list[dict[str, Any]], engaged: list[str]) -> str:
    """One sentence a salesperson can read before a follow-up call.

    Deliberately plain and generated, not written by an LLM: this ships inside a
    CRM record, and a fluent sentence that hallucinated a zone would be worse
    than no sentence at all. Everything in here is a fact from the rows above.
    """
    if not ordered:
        return "No zone visits recorded."

    parts: list[str] = []
    for dwell in ordered:
        name = dwell.get("zone") or dwell.get("zone_id") or "an unnamed zone"
        duration = float(dwell.get("duration") or 0.0)
        parts.append(f"{name} {_minutes(duration)}")

    summary = "Visited " + ", then ".join(parts)
    if engaged:
        summary += f". Engaged {', '.join(engaged)}"
    return summary + "."


def _minutes(seconds: float) -> str:
    """Rounded, and never more precise than is useful — "2m", not "127.4s"."""
    if seconds < 90:
        return f"{round(seconds)}s"
    return f"{round(seconds / 60)}m"
