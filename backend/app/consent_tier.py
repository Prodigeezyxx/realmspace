"""
What a consent tier permits, in one place.

`consent-and-identity.md` §2 defines four tiers, each a strict superset of the
one above, and §3 says where the gate belongs: *"CRM sync consumers refuse to
emit a contact without a ≥T2 consent. This is enforced in code (the bus
consumer), not by convention."*

It was enforced nowhere. `consumers/identity.py` states the rule in a comment —
"CRM sync refuses below T2, enrichment below T3" — as an account of where the
gates live, and its own T1 gate was the only one that existed. A T1 visitor's
lead went to every CRM a tenant had connected.

The distinction the tiers draw is not a formality. **T1 is "take my details".
T2 is agreeing to be contacted.** They are different sentences on a kiosk and a
visitor picks between them; pushing a T1 capture into a client's CRM hands a
sales team somebody who never agreed to hear from them, and a personalised
follow-up email to that person is the thing they explicitly did not ask for.

## Why this is a module and not a line in each consumer

Two consumers need it — `crm_delivery` and `sdr` — and a third will
(`integrations.md` §7's enrichment, at T3). Written twice it is written twice
differently: one of them compares strings, one forgets that a missing tier is not
a passing tier, and the difference surfaces as a visitor being contacted on the
strength of a consent that did not cover it.

## A missing or unknown tier does not pass

`None` is what a handoff carries when the consent could not be read at all, and
an unrecognised value is what arrives from a capture surface written against a
newer taxonomy than this build. Both refuse. The alternative — treating unknown
as permissive — makes the failure mode of every future tier change "we contacted
people we should not have".
"""

from __future__ import annotations

from typing import Any

#: Ascending. `consent-and-identity.md` §2's table, in order, and the order is
#: the whole of the comparison — each tier is a strict superset of the one above.
TIERS = ("T0", "T1", "T2", "T3")

#: `consent-and-identity.md` §2: T2 is "explicit opt-in to be contacted /
#: marketed to", and what it unlocks is "CRM sync, personalised follow-up (SDR)".
#: Both of this module's callers are one of those two things.
CONTACTABLE = "T2"


def rank(tier: str | None) -> int:
    """Where a tier sits, or -1 for one this build does not recognise."""
    try:
        return TIERS.index(str(tier))
    except ValueError:
        return -1


def permits(tier: str | None, *, minimum: str = CONTACTABLE) -> bool:
    """May we do a thing that needs `minimum`, holding `tier`?"""
    given = rank(tier)
    return given >= 0 and given >= rank(minimum)


def refusal(tier: str | None, *, minimum: str = CONTACTABLE, act: str) -> str:
    """The sentence a dispatch row or a log line carries when we refuse.

    It names the tier we hold and the tier we needed, because the operator
    reading it is answering "why did this lead not arrive" and neither half is
    guessable from the other.
    """
    held = tier if rank(tier) >= 0 else f"{tier!r} (not a tier this build knows)"
    return (
        f"{act} needs consent {minimum} or above and this visitor gave {held} — "
        f"nothing was sent, and nothing is missing"
    )


def tier_of(handoff: dict[str, Any]) -> str | None:
    """The tier on a `LeadHandoff/v1`, or None if it carries no consent at all.

    An anonymous handoff has no `consent` block by design
    (`consumers/attribution.py`), and None is the correct answer for it rather
    than an error — it fails the gate, which is right: there is nobody to
    contact.
    """
    return (handoff.get("consent") or {}).get("tier")
