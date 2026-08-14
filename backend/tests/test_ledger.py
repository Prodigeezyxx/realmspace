"""
The attribution ledger, as executable claims.

Driven through the pure builder rather than the API, because what a client
argues about is the arithmetic: is this deal inside the window, why does the
booth get credit for it, and what happened to the person who withdrew.

The two that matter most are the window boundary — a deal closing on the last day
of an agreed window is inside it, and being off by one there means the stated
window is a day shorter than what was signed — and the refusal to score
`linear`/`time_decay`, which is the only place in this repo that declines to
produce a number a client explicitly asked for.
"""

from __future__ import annotations

from app.attribution import ledger

TOUCH = "2026-08-13T10:00:00+00:00"


def handoff(**over):
    payload = {
        "schema": "realmspace.lead_handoff/v1",
        "stage": "identified",
        "dedupe_key": "t_test:sam@example.com",
        "anon_id": "P-012",
        "emitted_at": TOUCH,
        "contact": {
            "id": "ct_1",
            "email": "sam@example.com",
            "name": "Sam Rivera",
        },
        "spatial_intent": {
            "zones_visited": ["Entry", "Pod"],
            "dwell_seconds_total": 160.0,
            "lead_score": 74,
            "lead_score_basis": "spatial/v1",
        },
        "consent": {
            "tier": "T2",
            "basis": "explicit_optin",
            "copy_version": "consent-en-2026-08",
            "captured_at": TOUCH,
        },
    }
    payload.update(over)
    return payload


def outcome(**over):
    payload = {
        "outcome_id": "o_1",
        "dedupe_key": "t_test:sam@example.com",
        "stage": "won",
        "value": 5000.0,
        "currency": "USD",
        "closed_at": "2026-09-01T10:00:00+00:00",
        "source": "operator",
        "recorded_by": "u_test",
    }
    payload.update(over)
    return payload


# ── the join ──────────────────────────────────────────────────────────────────


def test_a_touch_and_a_deal_meet_on_the_dedupe_key():
    built = ledger.build([handoff()], [outcome()], [])

    assert len(built["rows"]) == 1
    row = built["rows"][0]
    assert row["contact_email"] == "sam@example.com"
    assert row["zones_visited"] == ["Entry", "Pod"]
    assert row["outcomes"][0]["outcome_id"] == "o_1"
    assert row["attributed_value"] == 5000.0
    assert built["totals"]["revenue_influenced"] == 5000.0


def test_both_handoff_stages_collapse_onto_one_lead():
    """A lead is emitted twice by design. The ledger is a list of leads, not of
    handoffs, so the two stages are one row — with the touch dated from the
    first, because that is when the booth actually touched the deal, and the path
    taken from the last, because that one is complete."""
    final = handoff(
        stage="final",
        emitted_at="2026-08-13T18:00:00+00:00",
        spatial_intent={
            "zones_visited": ["Entry", "Pod", "Demo Bar"],
            "dwell_seconds_total": 420.0,
            "lead_score": 88,
            "lead_score_basis": "spatial/v1",
        },
    )

    built = ledger.build([handoff(), final], [outcome()], [])

    assert len(built["rows"]) == 1
    row = built["rows"][0]
    assert row["first_touch_at"] == TOUCH
    assert row["final_touch_at"] == "2026-08-13T18:00:00+00:00"
    assert row["zones_visited"] == ["Entry", "Pod", "Demo Bar"]
    assert row["lead_score"] == 88


def test_a_lead_with_no_outcome_yet_is_still_a_row():
    """Most of any live ledger. Dropping it would overstate the conversion rate
    of everything left."""
    built = ledger.build([handoff()], [], [])

    assert len(built["rows"]) == 1
    assert built["rows"][0]["outcomes"] == []
    assert built["rows"][0]["attributed_value"] is None
    assert built["totals"]["leads"] == 1


def test_an_outcome_naming_a_lead_that_is_not_here_is_kept_and_flagged():
    """A deal attributed to a booth touch with no record is exactly the
    discrepancy an audit is looking for. Discarding it would hide it."""
    built = ledger.build([], [outcome(dedupe_key="t_test:ghost@example.com")], [])

    assert built["rows"][0]["orphan"] is True
    assert built["totals"]["orphan_outcomes"] == 1
    assert built["totals"]["leads"] == 0


# ── the window ────────────────────────────────────────────────────────────────


def test_a_deal_closing_on_the_last_day_of_the_window_is_inside_it():
    """Inclusive at the boundary. Exclusive would mean the window a client
    agreed to is silently one day shorter than the number they signed."""
    built = ledger.build(
        [handoff()],
        [outcome(closed_at="2026-11-11T10:00:00+00:00")],  # exactly 90 days
        [],
        attribution_window_days=90,
    )

    assert built["rows"][0]["outcomes"][0]["days_to_close"] == 90.0
    assert built["rows"][0]["outcomes"][0]["in_window"] is True
    assert built["totals"]["revenue_influenced"] == 5000.0


def test_a_deal_closing_the_day_after_is_listed_but_not_counted():
    """roi-framework.md §5: it "ages out of active attribution but stays in the
    audit ledger"."""
    built = ledger.build(
        [handoff()],
        [outcome(closed_at="2026-11-12T10:00:00+00:00")],  # 91 days
        [],
        attribution_window_days=90,
    )

    row = built["rows"][0]
    assert row["outcomes"][0]["in_window"] is False
    assert row["attributed_value"] is None
    assert built["totals"]["revenue_influenced"] == 0
    # Still on the ledger — that is the whole distinction being drawn.
    assert built["totals"]["outcomes"] == 1


def test_a_shorter_window_excludes_what_a_longer_one_admits():
    late = [outcome(closed_at="2026-10-01T10:00:00+00:00")]  # ~49 days

    assert ledger.build([handoff()], late, [], attribution_window_days=90)["totals"][
        "revenue_influenced"
    ] == 5000.0
    assert ledger.build([handoff()], late, [], attribution_window_days=30)["totals"][
        "revenue_influenced"
    ] == 0


def test_an_open_opportunity_is_neither_in_nor_out_of_the_window():
    """`None`, not False. An undated deal has not failed the window — it has not
    been judged by it, and a CFO reading "out of window" would take that as a
    deal that came too late rather than one still in play."""
    built = ledger.build(
        [handoff()], [outcome(stage="open", closed_at=None, value=None)], []
    )

    assert built["rows"][0]["outcomes"][0]["in_window"] is None
    assert built["rows"][0]["attributed_value"] is None


def test_a_lost_deal_in_the_window_is_recorded_and_not_credited():
    built = ledger.build([handoff()], [outcome(stage="lost")], [])

    assert built["rows"][0]["outcomes"][0]["in_window"] is True
    assert built["rows"][0]["attributed_value"] is None
    assert built["totals"]["outcomes_in_window"] == 1


# ── the model ─────────────────────────────────────────────────────────────────


def test_the_binary_models_all_credit_the_booth_the_same_way():
    """A booth is the only touch realmspace observes, so first, last and
    influenced cannot be told apart from here — and pretending otherwise would
    be a claim about a journey this system does not see."""
    totals = [
        ledger.build([handoff()], [outcome()], [], attribution_model=model)["totals"][
            "revenue_influenced"
        ]
        for model in ledger.BINARY_MODELS
    ]

    assert totals == [5000.0, 5000.0, 5000.0]


def test_a_multi_touch_model_refuses_with_its_reason():
    """The one place in this repo that declines to produce a number a client
    asked for. A share computed over one known touch is 100% with arithmetic
    painted on it."""
    built = ledger.build([handoff()], [outcome()], [], attribution_model="linear")

    assert built["model_supported"] is False
    assert "one of them" in built["model_note"]
    assert built["totals"]["revenue_influenced"] is None
    # The evidence is still all there — only the claim is withheld.
    assert built["rows"][0]["outcomes"][0]["in_window"] is True
    assert built["totals"]["outcomes"] == 1


def test_two_currencies_are_never_summed():
    """Same rule the cost tile follows: a total across currencies is a number
    with no unit."""
    built = ledger.build(
        [handoff(), handoff(dedupe_key="t_test:kim@example.com")],
        [
            outcome(),
            outcome(
                outcome_id="o_2",
                dedupe_key="t_test:kim@example.com",
                currency="NGN",
                value=200.0,
            ),
        ],
        [],
    )

    assert built["totals"]["revenue_influenced"] is None
    assert built["totals"]["mixed_currencies"] == ["NGN", "USD"]


# ── withdrawal ────────────────────────────────────────────────────────────────


def test_a_withdrawn_lead_keeps_its_touch_and_loses_its_name():
    """The property that makes this an audit trail rather than a dump of the
    log. The log keeps the name forever; the ledger must not."""
    built = ledger.build(
        [handoff()],
        [outcome()],
        [{"anon_id": "P-012", "reason": "visitor_request"}],
    )

    row = built["rows"][0]
    assert row["withdrawn"] is True
    assert row["contact_name"] is None
    assert row["contact_email"] is None
    # What survives is the answer to "did this touch happen and on what basis".
    assert row["first_touch_at"] == TOUCH
    assert row["consent_copy_version"] == "consent-en-2026-08"
    assert row["zones_visited"] == ["Entry", "Pod"]


def test_the_redacted_key_stops_naming_the_person():
    """The dedupe key embeds an email, so leaving it whole would undo the
    redaction one column to the left."""
    built = ledger.build(
        [handoff()], [], [{"contact_id": "ct_1", "reason": "erasure_request"}]
    )

    assert built["rows"][0]["dedupe_key"] == "t_test:[withdrawn]"


def test_a_withdrawal_can_name_the_contact_instead_of_the_track():
    built = ledger.build(
        [handoff()], [outcome()], [{"contact_id": "ct_1", "reason": "operator"}]
    )

    assert built["rows"][0]["withdrawn"] is True
    assert built["totals"]["withdrawn"] == 1
