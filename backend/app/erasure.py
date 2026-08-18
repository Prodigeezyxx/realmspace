"""
What an erasure removes from the log, and how it works out whose events those are.

`consent-and-identity.md` §5: *"Right to erasure (GDPR Art. 17 / CCPA) →
tenant-scoped erasure job that removes a `Contact` and all PII edges, keeping
only anonymised aggregates."*

The withdrawal path already does everything that sentence literally asks for: it
drops the `IDENTIFIED_AS` edge, redacts the graph Contact to a tombstone and
retracts the record from every CRM that received it. What it cannot touch is the
log, and the log is where the visitor's email actually is — inside
`consent.captured`'s contact object, and inside every `handoff.lead` payload that
was built from it. An erasure that leaves those is not an erasure; it is a
withdrawal with a stronger word on it.

This module is the policy half, kept pure so the part a regulator would ask about
can be tested without a database in front of it. `consumers/erasure.py` is the
IO half.

## Finding the events is harder than redacting them

An erasure request names one of three things — a contact, a consent, or a track —
because that is what the person asking can be identified by
(`routers/consent.py` makes the same argument about a withdrawal). The events
that are about them are keyed on all four identifiers at different points in
their life: the capture knows the consent and the track, the identification joins
the track to the contact, and the handoff carries the contact, the track and the
dedupe key. So the subject is resolved by walking the log and closing over
whatever it finds, from whichever identifier it was given.

## A track id is only meaningful inside its session

`P-012` in one activation and `P-012` in the next are two different people —
`privacy.md` is explicit that person ids are session-scoped and destroyed at the
end, and the whole no-cross-session-re-identification claim rests on it. So an
anon id is never held on its own here; it is held as `(session_id, anon_id)`.

Getting that wrong would be the worst bug this file could have: an erasure for
one visitor silently redacting a different visitor's events at another activation,
which is data loss that looks like compliance.

And `dedupe_key` walks past that guard unless it is stopped here. A review found
this after the first version shipped: `consumers/attribution.py` builds the key as
`tenant:email|anon_id`, so a visitor with **no email** — every anonymous handoff,
and any consented contact captured without one — gets `tenant:P-012`, which is
not session-scoped and is identical for a different person with the same track id
somewhere else. So a key is only taken from a handoff whose contact carries an
email, which is exactly when it names a person rather than a track. Never from an
`outcome.recorded`: its key was copied from a handoff, and taking it there would
reintroduce the same hole through the one event type that cannot be checked.

The people that excludes are not lost — they are covered by `(session, anon_id)`
and by `contact.id`, which is what should have been carrying them. What is lost is
an outcome recorded against a key that is only a track, and that key names nobody,
so there was nothing in it to redact.

It closes a second door too. A replayed erasure re-reads rows whose key is already
`tenant:[withdrawn]` — one value for every erased person in the tenant — and the
same test keeps that constant out of the set.

## What is kept, deliberately

The consent record, the withdrawal, the retraction and the identification all
survive. None of them contains PII — they are ids, tiers, bases and timestamps —
and together they are the evidence that permission was given, acted on and then
undone. A deployment that erased those would have nothing to answer with if the
erasure itself were ever disputed, which is the same argument `reanonymise.py`
makes about keeping the ConsentEvent.

`outcome.recorded` keeps its `external_ref` for the same reason `crm_link` keeps
its `external_id`: it names a record in the client's own system, and an audit
that cannot name the record cannot check what happened to it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.attribution.ledger import redact_dedupe_key

CAPTURED = "consent.captured"
RESOLVED = "identity.resolved"
HANDOFF = "handoff.lead"
OUTCOME = "outcome.recorded"

#: The types that join one identifier to another. Read to resolve the subject.
LINKING_TYPES = (CAPTURED, RESOLVED, HANDOFF)

#: The types whose payloads actually carry a name or an email. Everything else
#: on the log about this person is ids and timestamps, and stays — see the
#: module docstring.
PII_TYPES = (CAPTURED, HANDOFF, OUTCOME)

#: Contact fields consent grants us and erasure takes back. `id` is not among
#: them: it is ours, it names no one on its own, and the ledger row, the
#: `crm_link` and the `erasure.completed` receipt all still have to resolve.
CONTACT_PII = ("email", "name", "company", "title")


@dataclass
class Subject:
    """One person, as every part of the log refers to them."""

    contact_ids: set[str] = field(default_factory=set)
    consent_ids: set[str] = field(default_factory=set)
    #: `(session_id, anon_id)` — never a bare anon id. See the module docstring.
    tracks: set[tuple[str, str]] = field(default_factory=set)
    dedupe_keys: set[str] = field(default_factory=set)

    def __bool__(self) -> bool:
        return bool(
            self.contact_ids or self.consent_ids or self.tracks or self.dedupe_keys
        )

    def covers(self, *, type: str, session_id: str, payload: dict[str, Any]) -> bool:
        """Is this event about this person?"""
        if type == CAPTURED:
            return (
                payload.get("consent_id") in self.consent_ids
                or (session_id, payload.get("anon_id")) in self.tracks
            )
        if type == RESOLVED:
            return (
                payload.get("contact_id") in self.contact_ids
                or payload.get("consent_id") in self.consent_ids
                or (session_id, payload.get("anon_id")) in self.tracks
            )
        if type == HANDOFF:
            return (
                (payload.get("contact") or {}).get("id") in self.contact_ids
                or payload.get("dedupe_key") in self.dedupe_keys
                or (session_id, payload.get("anon_id")) in self.tracks
            )
        if type == OUTCOME:
            return payload.get("dedupe_key") in self.dedupe_keys
        return False

    def absorb(self, *, type: str, session_id: str, payload: dict[str, Any]) -> bool:
        """Take every identifier this event knows. True if anything was new."""
        before = (
            len(self.contact_ids),
            len(self.consent_ids),
            len(self.tracks),
            len(self.dedupe_keys),
        )

        anon_id = payload.get("anon_id")
        if anon_id:
            self.tracks.add((session_id, anon_id))
        if payload.get("consent_id"):
            self.consent_ids.add(payload["consent_id"])
        if payload.get("contact_id"):
            self.contact_ids.add(payload["contact_id"])
        contact = payload.get("contact") or {}
        if contact.get("id"):
            self.contact_ids.add(contact["id"])
        if type == HANDOFF and contact.get("email") and payload.get("dedupe_key"):
            # Only from a handoff, and only when there was an email to build it
            # from. See the module docstring — a key that is really a track id
            # bridges two people who share one, and a redacted key is shared by
            # everybody already erased.
            self.dedupe_keys.add(payload["dedupe_key"])

        return before != (
            len(self.contact_ids),
            len(self.consent_ids),
            len(self.tracks),
            len(self.dedupe_keys),
        )


def resolve(
    events: list[tuple[str, str, dict[str, Any]]], seed: Subject
) -> Subject:
    """Close the subject over the log, from whichever identifier we were given.

    `events` is `(type, session_id, payload)` for the linking types, in any
    order. Iterated to a fixed point rather than in one pass because the chain
    can run either way — a request naming a contact reaches the track through the
    identification and the capture's consent id only through the track after
    that, and a request naming a consent walks the same chain in reverse.

    Bounded by construction: each pass either adds an identifier or is the last
    one, and there are finitely many identifiers on the log.
    """
    subject = seed
    changed = True
    while changed:
        changed = False
        for type, session_id, payload in events:
            if subject.covers(type=type, session_id=session_id, payload=payload):
                changed |= subject.absorb(
                    type=type, session_id=session_id, payload=payload
                )
    return subject


def redact(
    type: str, payload: dict[str, Any], *, discriminator: str | None = None
) -> dict[str, Any] | None:
    """The payload with the person taken out, or `None` if nothing had to change.

    `None` matters: it is what keeps `redacted_at` off the rows that were only
    ever ids and timestamps, so "what did this erasure touch" stays an honest
    answer rather than a list of everything it looked at.

    `discriminator` is what keeps two erased visitors two visitors. It goes into
    the rewritten `dedupe_key`, which `attribution.ledger.build` groups on — one
    value per erasure, so this person's handoffs and this person's outcomes still
    meet on one key and nobody else's join them there. `consumers/erasure.py`
    supplies it; `redact_dedupe_key` has the whole argument.
    """
    if type not in PII_TYPES:
        return None

    redacted = dict(payload)
    changed = False

    contact = redacted.get("contact")
    if isinstance(contact, dict):
        kept = {k: v for k, v in contact.items() if k not in CONTACT_PII}
        if kept != contact:
            changed = True
            # The key stays even when nothing is left in it but the id. An
            # absent `contact` means "nobody was named here", which is what an
            # anonymous handoff says, and an erased lead is not that — it is a
            # person who was named and then asked not to be.
            redacted["contact"] = kept

    key = redacted.get("dedupe_key")
    if isinstance(key, str) and key:
        cleaned = redact_dedupe_key(key, discriminator=discriminator)
        if cleaned != key:
            changed = True
            redacted["dedupe_key"] = cleaned

    return redacted if changed else None
