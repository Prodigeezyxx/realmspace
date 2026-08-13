"""
`lead_score` — the one number in the handoff that is a judgement.

`integrations.md` §2 asks for it and describes it only as "computed from spatial
+ (opt) enrichment". No model existed anywhere in the repo, so this file is the
model, and the whole design is about making it arguable rather than magic.

## Every input is something the booth measured

Three components, each a ratio of what this visitor did to what the activation
offered:

| Component | Ratio | Denominator comes from |
|---|---|---|
| Dwell | total dwell ÷ engagement threshold | `Session.engaged_threshold_seconds` |
| Funnel | furthest zone reached ÷ deepest zone | the session's `Zone.funnel_order`s |
| Surfaces | touchpoints used ÷ touchpoints present | the session's `Surface` count |

Denominators are the operator's own configuration, not constants chosen here.
That matters: a booth with one product pod and a booth with six should not be
scored against the same idea of "engaged", and a score whose denominator was a
number in this file would be comparing every client to a fiction.

## A missing component is dropped, not defaulted

A session with no funnel order configured has no funnel ratio. Scoring that as
zero would punish a visitor for a setting their host never filled in; scoring it
as one would flatter every lead. So it is removed and the remaining weights are
renormalised, and `components` says which ones actually contributed — the same
"name what is missing" rule the report follows for uncomputable figures.

If nothing can be computed at all the score is **None**, not zero. "We cannot
score this lead" and "this is a bad lead" are different statements, and a CRM
sorting by score would treat them identically.

## The basis string is the point

`spatial/v1` ships in the payload beside the number, for the reason
`event-bus-spec.md` §3 gives for `spatial.tagged`'s `method`: "a stored score
whose derivation is unknown cannot be re-judged after the fact". A score in a
CRM outlives this file. Change the formula, bump the version — never edit v1's
behaviour in place, or last quarter's leads silently stop meaning what they said.
"""

from __future__ import annotations

from typing import Any

#: Bump on any change to the arithmetic below. Never redefine an existing one.
BASIS = "spatial/v1"

#: What each component is worth when all three are available. Dwell dominates
#: because it is the signal with the least inference in it — a person standing
#: somewhere for two minutes chose to, whereas a surface tap can be a passer-by
#: with a free hand.
WEIGHTS = {"dwell": 0.5, "funnel": 0.3, "surfaces": 0.2}


def _ratio(value: float, denominator: float | None) -> float | None:
    """`value / denominator`, capped at 1, or None when it cannot be computed.

    Capped rather than allowed to run away: somebody who stayed ten times the
    engagement threshold is not ten times the lead, and without a cap one very
    long dwell would saturate the score on its own and make the other two
    components decorative.
    """
    if not denominator or denominator <= 0:
        return None
    return min(1.0, max(0.0, value / denominator))


def lead_score(
    intent: dict[str, Any], *, engaged_threshold_seconds: float | None
) -> tuple[int | None, str, list[str]]:
    """Return `(score, basis, components)`.

    `intent` is `spatial_intent.build`'s output — it already carries the
    denominators, so this cannot disagree with the block it scores.
    """
    ratios: dict[str, float] = {}

    dwell = _ratio(
        float(intent.get("dwell_seconds_total") or 0.0), engaged_threshold_seconds
    )
    if dwell is not None:
        ratios["dwell"] = dwell

    depth = intent.get("funnel_depth_reached")
    funnel = (
        _ratio(float(depth), intent.get("max_funnel_order"))
        if depth is not None
        else None
    )
    if funnel is not None:
        ratios["funnel"] = funnel

    surfaces = _ratio(
        len(intent.get("surfaces_engaged") or []), intent.get("surfaces_available")
    )
    if surfaces is not None:
        ratios["surfaces"] = surfaces

    if not ratios:
        # Nothing measurable. See the module docstring: None, never 0.
        return None, BASIS, []

    total_weight = sum(WEIGHTS[name] for name in ratios)
    score = sum(WEIGHTS[name] * value for name, value in ratios.items()) / total_weight
    return round(score * 100), BASIS, sorted(ratios)
