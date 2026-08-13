"""
The two numbers in a handoff that are judgements, tested without a database.

`spatial_intent.build` and `score.lead_score` are pure so they can be argued
with. These are the cases a client would raise in a room: why is my score 0,
why is this lead ranked above that one, what does 214 mean.
"""

from __future__ import annotations

from app.attribution import score as scoring
from app.attribution import spatial_intent


def dwell(zone: str, duration: float, *, started_at: str, weight=None, funnel=None):
    return {
        "zone_id": f"z_{zone.lower()}",
        "zone": zone,
        "duration": duration,
        "started_at": started_at,
        "weight": weight,
        "funnel_order": funnel,
    }


# ── spatial_intent ────────────────────────────────────────────────────────────


def test_zones_come_back_in_the_order_they_were_entered():
    """The list a salesperson reads as a route, so arrival order is the only
    order that makes sense — not alphabetical and not by duration."""
    intent = spatial_intent.build(
        [
            dwell("Product Pod", 120, started_at="2026-08-13T10:05:00Z"),
            dwell("Entry", 30, started_at="2026-08-13T10:00:00Z"),
        ],
        [],
    )

    assert intent["zones_visited"] == ["Entry", "Product Pod"]


def test_a_zone_returned_to_is_visited_once():
    intent = spatial_intent.build(
        [
            dwell("Entry", 20, started_at="2026-08-13T10:00:00Z"),
            dwell("Pod", 60, started_at="2026-08-13T10:01:00Z"),
            dwell("Entry", 10, started_at="2026-08-13T10:09:00Z"),
        ],
        [],
    )

    assert intent["zones_visited"] == ["Entry", "Pod"]


def test_the_top_zone_totals_repeat_visits_rather_than_ranking_single_stays():
    """Three one-minute visits to a pod beat one two-minute stand at the door.
    Ranking by longest single dwell would say the opposite, and it would be
    wrong about which product the person was actually interested in."""
    intent = spatial_intent.build(
        [
            dwell("Entry", 120, started_at="2026-08-13T10:00:00Z"),
            dwell("Pod", 60, started_at="2026-08-13T10:02:00Z"),
            dwell("Pod", 60, started_at="2026-08-13T10:05:00Z"),
            dwell("Pod", 60, started_at="2026-08-13T10:08:00Z"),
        ],
        [],
    )

    assert intent["top_dwell_zone"] == "Pod"


def test_attention_is_weighted_dwell_seconds_and_says_so():
    """roi-framework.md §2's definition, in the unit that definition produces.
    The field carries its own basis because the number alone does not say
    whether it is a ratio or a quantity."""
    intent = spatial_intent.build(
        [
            dwell("Entry", 100, started_at="2026-08-13T10:00:00Z", weight=0.5),
            dwell("Pod", 100, started_at="2026-08-13T10:02:00Z", weight=2.0),
        ],
        [],
    )

    assert intent["attention_score"] == 250.0
    assert intent["attention_basis"] == "weighted_dwell_seconds"
    # Unweighted total stays available beside it — the two answer different
    # questions and a report needs both.
    assert intent["dwell_seconds_total"] == 200.0


def test_an_unweighted_session_still_produces_attention():
    """An operator who never set weights gets plain dwell seconds. Refusing to
    compute because nobody expressed a preference would drop the signal."""
    intent = spatial_intent.build(
        [dwell("Entry", 100, started_at="2026-08-13T10:00:00Z")], []
    )

    assert intent["attention_score"] == 100.0


def test_the_path_summary_is_generated_from_the_rows_and_nothing_else():
    """This ships inside a CRM record. A fluent sentence that invented a zone
    would be worse than no sentence."""
    intent = spatial_intent.build(
        [
            dwell("Entry", 30, started_at="2026-08-13T10:00:00Z"),
            dwell("AR Mirror", 180, started_at="2026-08-13T10:01:00Z"),
        ],
        [{"surface_id": "s_1", "label": "Bottle Wall"}],
    )

    assert intent["path_summary"] == (
        "Visited Entry 30s, then AR Mirror 3m. Engaged Bottle Wall."
    )


def test_an_empty_path_says_so_rather_than_pretending():
    intent = spatial_intent.build([], [])

    assert intent["zones_visited"] == []
    assert intent["top_dwell_zone"] is None
    assert intent["path_summary"] == "No zone visits recorded."


# ── lead_score ────────────────────────────────────────────────────────────────


def full_intent(**over):
    intent = {
        "dwell_seconds_total": 60.0,
        "funnel_depth_reached": 2,
        "max_funnel_order": 4,
        "surfaces_engaged": ["Bottle Wall"],
        "surfaces_available": 2,
    }
    intent.update(over)
    return intent


def test_a_visitor_who_did_everything_scores_100():
    value, basis, components = scoring.lead_score(
        full_intent(
            dwell_seconds_total=300.0,
            funnel_depth_reached=4,
            surfaces_engaged=["a", "b"],
        ),
        engaged_threshold_seconds=60.0,
    )

    assert value == 100
    assert basis == "spatial/v1"
    assert components == ["dwell", "funnel", "surfaces"]


def test_a_very_long_dwell_cannot_saturate_the_score_on_its_own():
    """Ten times the threshold is not ten times the lead. Without the cap one
    long stay would make the other two components decorative."""
    value, _, _ = scoring.lead_score(
        full_intent(
            dwell_seconds_total=6000.0, funnel_depth_reached=0, surfaces_engaged=[]
        ),
        engaged_threshold_seconds=60.0,
    )

    assert value == 50  # the dwell component's full weight, and nothing else


def test_a_component_the_operator_never_configured_is_dropped_not_zeroed():
    """A session with no funnel order has no funnel ratio. Scoring it zero would
    punish a visitor for a setting their host did not fill in."""
    without_funnel = scoring.lead_score(
        full_intent(max_funnel_order=None), engaged_threshold_seconds=60.0
    )
    with_funnel = scoring.lead_score(full_intent(), engaged_threshold_seconds=60.0)

    assert "funnel" not in without_funnel[2]
    assert without_funnel[0] != with_funnel[0]
    # Renormalised over what remains, so a partly-configured session still
    # produces a score on the same 0-100 scale.
    assert 0 <= without_funnel[0] <= 100


def test_an_unscoreable_lead_is_none_and_never_zero():
    """"We cannot score this" and "this is a bad lead" are different statements,
    and a CRM sorting by score would treat them identically."""
    value, basis, components = scoring.lead_score(
        {"dwell_seconds_total": 0.0, "surfaces_engaged": []},
        engaged_threshold_seconds=None,
    )

    assert value is None
    assert components == []
    # The basis still ships: a null score whose derivation is unknown is as
    # unarguable as a number whose derivation is unknown.
    assert basis == "spatial/v1"


def test_the_basis_version_travels_with_every_score():
    """A score in a CRM outlives this file. Changing the formula bumps the
    version rather than editing v1's meaning underneath last quarter's leads."""
    assert scoring.lead_score(full_intent(), engaged_threshold_seconds=60.0)[1] == (
        scoring.BASIS
    )
