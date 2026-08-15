"""
The attribution ledger — every booth-touch → outcome link, with its consent basis.

`roi-framework.md` §4 calls this "an exportable, auditable list … what a
CFO/auditor asks for; nobody else in the category ships it". It is also the last
clause of Phase 4's acceptance.

## Built from the log, not the graph

This is the decision the whole file rests on. An auditor is asking *what did you
know, and when*. The graph is current state: a withdrawal redacts the Contact and
deletes the link, so a ledger built from it could not show that the touch ever
happened — the row would simply be missing, which is the one thing an audit trail
must never do.

The log keeps each `handoff.lead` exactly as it went out, including the consent
snapshot taken at that moment. So the ledger reads events and joins them on
`dedupe_key`, and this file is a pure function of those events — no database, so
the arithmetic a client will argue about can be tested directly.

## Which creates the obligation to redact

The log is append-only, so a withdrawn visitor's name is still in it and always
will be. The ledger therefore redacts **at read time**: a contact with a
`consent.withdrawn` keeps their row — the touch, the timestamps, the consent
basis that was in force, and the fact of the retraction — and loses their name
and email.

That is what makes this an audit trail rather than a dump of the log. A regulator
asking "did you stop using their data" is answered by the row being there and
being empty of them.

## Model and window

The **window** comes from the session config (`attribution_window_days`, 30/60/90)
and is measured from the booth touch to the outcome's close. An outcome outside
it is still listed, marked out of window, and excluded from the influenced total
— `roi-framework.md` §5's "ages out of active attribution but stays in the audit
ledger", exactly.

The **model** is where this refuses to do something. `influenced`, `first_touch`
and `last_touch` all reduce to the same binary decision, because a booth is the
only touch realmspace can see: we cannot know whether it was first, last or
seventh in a journey we do not observe. `linear` and `time_decay` ask for a
*share* across that journey, and a share computed over one known touch is 100%
with arithmetic painted on it. So those two produce no attributed value and say
why, rather than reporting a number that would be indefensible in the room where
this document gets read.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

#: Models that resolve to "the booth touched this deal, or it did not".
BINARY_MODELS = ("influenced", "first_touch", "last_touch")

#: Models that need touches this system does not observe.
UNSUPPORTED_MODELS = ("linear", "time_decay")

UNSUPPORTED_REASON = (
    "{model!r} apportions a share of the deal across every touch in the buyer's "
    "journey, and realmspace observes exactly one of them — the booth. A share "
    "computed over one known touch is 100% presented as arithmetic. The touches "
    "and outcomes below are still recorded; pick a binary model, or compute the "
    "share in the system that can see the whole journey."
)


def _parse(value: Any) -> dt.datetime | None:
    """ISO-8601 → datetime, or None. Tolerant on purpose: a row that cannot be
    dated should drop out of the window arithmetic, not take the export with
    it."""
    if not value:
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def build(
    handoffs: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
    withdrawals: list[dict[str, Any]],
    *,
    attribution_model: str = "influenced",
    attribution_window_days: int = 90,
) -> dict[str, Any]:
    """The ledger, as rows plus totals.

    Each argument is a list of **event payloads** — `handoff.lead`,
    `outcome.recorded` and `consent.withdrawn` — in log order.
    """
    supported = attribution_model not in UNSUPPORTED_MODELS
    withdrawn_keys, withdrawn_contacts = _withdrawn(withdrawals, handoffs)

    by_key: dict[str, dict[str, Any]] = {}
    for handoff in handoffs:
        key = handoff.get("dedupe_key")
        if not key:
            continue
        row = by_key.setdefault(key, _new_row(key))
        _absorb_handoff(row, handoff)

    for outcome in outcomes:
        key = outcome.get("dedupe_key")
        if key in by_key:
            by_key[key]["outcomes"].append(_outcome_row(outcome))
        else:
            # An outcome naming a lead this session never produced. Kept rather
            # than dropped: a deal attributed to a booth touch that is not in the
            # log is exactly the discrepancy an auditor is looking for, and
            # silently discarding it would hide it.
            orphan = _new_row(key or "")
            orphan["orphan"] = True
            orphan["outcomes"].append(_outcome_row(outcome))
            by_key[key or f"__orphan_{len(by_key)}"] = orphan

    rows = []
    for row in by_key.values():
        if row["dedupe_key"] in withdrawn_keys or row["contact_id"] in withdrawn_contacts:
            _redact(row)
        _judge(
            row,
            window_days=attribution_window_days,
            supported=supported,
        )
        rows.append(row)

    rows.sort(key=lambda r: (r["first_touch_at"] or "", r["dedupe_key"]))

    return {
        "attribution_model": attribution_model,
        "attribution_window_days": attribution_window_days,
        "model_supported": supported,
        "model_note": (
            None
            if supported
            else UNSUPPORTED_REASON.format(model=attribution_model)
        ),
        "rows": rows,
        "totals": _totals(rows, supported=supported),
    }


# ── row assembly ──────────────────────────────────────────────────────────────


def _new_row(dedupe_key: str) -> dict[str, Any]:
    return {
        "dedupe_key": dedupe_key,
        "contact_id": None,
        "contact_name": None,
        "contact_email": None,
        "anon_id": None,
        "first_touch_at": None,
        "final_touch_at": None,
        "zones_visited": [],
        "dwell_seconds_total": None,
        "lead_score": None,
        "lead_score_basis": None,
        "consent_tier": None,
        "consent_basis": None,
        "consent_copy_version": None,
        "consent_captured_at": None,
        "withdrawn": False,
        "orphan": False,
        "outcomes": [],
        "attributed_value": None,
        "currency": None,
    }


def _absorb_handoff(row: dict[str, Any], handoff: dict[str, Any]) -> None:
    """Fold one handoff into its row.

    Both stages of a lead land here. The **first** touch keeps its timestamp
    because that is when the booth actually touched the deal — the moment the
    attribution window is measured from — while the spatial detail is taken from
    the latest handoff seen, since the `final` stage carries the complete path.
    """
    contact = handoff.get("contact") or {}
    intent = handoff.get("spatial_intent") or {}
    consent = handoff.get("consent") or {}
    emitted = handoff.get("emitted_at")

    row["contact_id"] = contact.get("id") or row["contact_id"]
    row["contact_name"] = contact.get("name") or row["contact_name"]
    row["contact_email"] = contact.get("email") or row["contact_email"]
    row["anon_id"] = handoff.get("anon_id") or row["anon_id"]

    if row["first_touch_at"] is None or (emitted and emitted < row["first_touch_at"]):
        row["first_touch_at"] = emitted
    if emitted and (row["final_touch_at"] is None or emitted > row["final_touch_at"]):
        row["final_touch_at"] = emitted
        row["zones_visited"] = intent.get("zones_visited") or []
        row["dwell_seconds_total"] = intent.get("dwell_seconds_total")
        row["lead_score"] = intent.get("lead_score")
        row["lead_score_basis"] = intent.get("lead_score_basis")

    row["consent_tier"] = consent.get("tier") or row["consent_tier"]
    row["consent_basis"] = consent.get("basis") or row["consent_basis"]
    row["consent_copy_version"] = (
        consent.get("copy_version") or row["consent_copy_version"]
    )
    row["consent_captured_at"] = (
        consent.get("captured_at") or row["consent_captured_at"]
    )


def _outcome_row(outcome: dict[str, Any]) -> dict[str, Any]:
    return {
        "outcome_id": outcome.get("outcome_id"),
        "stage": outcome.get("stage"),
        "value": outcome.get("value"),
        "currency": outcome.get("currency"),
        "closed_at": outcome.get("closed_at"),
        "source": outcome.get("source"),
        "external_ref": outcome.get("external_ref"),
        "recorded_by": outcome.get("recorded_by"),
        "days_to_close": None,
        "in_window": None,
    }


# ── withdrawal ────────────────────────────────────────────────────────────────


def _withdrawn(
    withdrawals: list[dict[str, Any]], handoffs: list[dict[str, Any]]
) -> tuple[set[str], set[str]]:
    """Which leads have been withdrawn, by dedupe key and by contact id.

    A withdrawal names whichever identifier the person had to hand — consent id,
    contact id or anon id — so the anon ids are resolved back to their leads
    through the handoffs, which carry both.
    """
    contacts: set[str] = set()
    keys: set[str] = set()
    anon_ids: set[str] = set()

    for withdrawal in withdrawals:
        if withdrawal.get("contact_id"):
            contacts.add(withdrawal["contact_id"])
        if withdrawal.get("anon_id"):
            anon_ids.add(withdrawal["anon_id"])

    if anon_ids:
        for handoff in handoffs:
            if handoff.get("anon_id") in anon_ids and handoff.get("dedupe_key"):
                keys.add(handoff["dedupe_key"])

    return keys, contacts


def _redact(row: dict[str, Any]) -> None:
    """Drop the PII, keep the audit trail.

    The touch, the timestamps, the consent that was in force and the fact of the
    retraction all survive. What goes is the name and the email — which is the
    whole of what consent ever granted, and the answer to "did you stop using
    their data" is this row being here and being empty of them.
    """
    row["withdrawn"] = True
    row["contact_name"] = None
    row["contact_email"] = None
    row["dedupe_key"] = redact_dedupe_key(row["dedupe_key"])


def redact_dedupe_key(key: str) -> str:
    """The dedupe key embeds an email. Keep the tenant half so rows stay
    joinable to what an adapter was told, drop the half that names a person.

    Public, and imported by `consumers/crm_retract.py` rather than copied: the
    `crm_link` rows carry the same key and have to lose the same half of it, and
    two redaction functions are two chances to disagree about which half that
    is — with the disagreement showing up as an email surviving a withdrawal.
    """
    tenant, _, _rest = key.partition(":")
    return f"{tenant}:[withdrawn]" if tenant else "[withdrawn]"


# ── window and model ──────────────────────────────────────────────────────────


def _judge(row: dict[str, Any], *, window_days: int, supported: bool) -> None:
    """Decide each outcome's window, then what the booth may claim."""
    touch = _parse(row["first_touch_at"])
    attributed = 0.0
    counted = False

    for outcome in row["outcomes"]:
        closed = _parse(outcome["closed_at"])
        if touch and closed:
            days = (closed - touch).total_seconds() / 86400
            outcome["days_to_close"] = round(days, 1)
            # Inclusive at the boundary: a deal closing exactly on the 90th day
            # is inside a 90-day window. Anything else means the stated window is
            # one day shorter than the number the client agreed to.
            outcome["in_window"] = 0 <= days <= window_days
        else:
            # No close date, or no touch to measure from. Neither in nor out —
            # `None` says "cannot be judged", which an open opportunity genuinely
            # cannot be.
            outcome["in_window"] = None

        if (
            supported
            and outcome["in_window"]
            and outcome["stage"] == "won"
            and outcome["value"] is not None
        ):
            attributed += float(outcome["value"])
            counted = True
            row["currency"] = row["currency"] or outcome["currency"]

    # None, not 0.0, when nothing qualified: "no closed-won deal in the window"
    # and "a deal worth nothing" are different, and a ROI ratio built on the
    # second would be wrong in a direction that flatters nobody.
    row["attributed_value"] = attributed if counted else None


def _totals(rows: list[dict[str, Any]], *, supported: bool) -> dict[str, Any]:
    influenced = [r for r in rows if r["attributed_value"] is not None]
    currencies = {r["currency"] for r in influenced if r["currency"]}

    return {
        "leads": sum(1 for r in rows if not r["orphan"]),
        "withdrawn": sum(1 for r in rows if r["withdrawn"]),
        "orphan_outcomes": sum(1 for r in rows if r["orphan"]),
        "outcomes": sum(len(r["outcomes"]) for r in rows),
        "outcomes_in_window": sum(
            1 for r in rows for o in r["outcomes"] if o["in_window"]
        ),
        "influenced_leads": len(influenced),
        # Refuses to total two currencies, the same rule `lib/roi/cost.ts`
        # follows for spend: a sum across them is a number with no unit.
        "revenue_influenced": (
            sum(r["attributed_value"] for r in influenced)
            if supported and len(currencies) <= 1
            else None
        ),
        "currency": next(iter(currencies), None) if len(currencies) == 1 else None,
        "mixed_currencies": sorted(currencies) if len(currencies) > 1 else [],
    }
