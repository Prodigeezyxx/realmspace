"""
The identity consumer — where an anonymous track becomes a person we may name.

`event-bus-spec.md` §4: *"on `consent.captured` link anon → contact"*, on the
edge, in seconds. It is the smallest consumer in the system and the only one
whose job is to be refusable.

  consent.captured  → (:ConsentEvent), then (Person)-[:IDENTIFIED_AS]->(:Contact)
                    → identity.resolved

## The gate is not in this file, and that is deliberate

`consent-and-identity.md` §3 says the `IDENTIFIED_AS` edge "cannot be created
unless a non-withdrawn ConsentEvent of the required tier exists in the same
transaction … enforced in code (the bus consumer), not by convention."

It is enforced one layer lower than that — inside `graph_repo.identify`, whose
Cypher matches the consent and creates the edge in a single statement. A check
here would be a check a second caller could skip, and "the consumer remembers to
look" is exactly the convention the doc is trying to replace. What this file does
is decide what to do when the answer is no.

## Why a refusal is not an error

`identify` returning None means the consent does not permit this. The ordinary
way that happens is a **replay after a withdrawal**: the capture is still on the
log — it always will be, the log is append-only — and re-reading it must not
resurrect a link the person asked to have removed. So the refusal is logged and
the event is consumed. Raising would park a perfectly correct decision in the
dead-letter queue and ask an operator to un-refuse it.

A missing Person is the opposite: usually the graph writer being one poll behind,
so it raises and lets `base.Consumer` retry with backoff.

## Contact ids

Derived, never random, for the same reason `ids.py` exists — a replay must
produce the same contact, or every reprocessed capture creates a new person.

Where an email is given it is the derivation key, so the same visitor consenting
at two activations under the same address is one Contact and attribution has
something continuous to attach to. Where none is given the consent id is the key,
which means two anonymous captures stay two contacts. That asymmetry is honest:
merging people we cannot identify would be inventing a fact, and
`consent-and-identity.md` §2's redlines put cross-activation re-identification
behind T3 rather than behind a guess.
"""

from __future__ import annotations

import logging

from app import db, repository
from app.config import get_settings
from app.consumers.base import Consumer
from app.consumers.ids import derive_event_id
from app.graph import repository as graph_repo
from app.graph.driver import get_driver
from app.models import EventLog
from app.schemas import EventIn

log = logging.getLogger(__name__)

CAPTURED = "consent.captured"
RESOLVED = "identity.resolved"

#: The tier at which a Contact may be created and linked at all.
#:
#: T1 is "take my details" (`consent-and-identity.md` §2), which is exactly this
#: link and nothing more. The higher gates live where the wider acts do: CRM sync
#: refuses below T2, enrichment below T3. Putting T2 here instead would mean a
#: visitor who agreed to be contacted-by-nobody could not have their own badge
#: scan recorded against their path.
MINIMUM_TIER = "T1"


def contact_id_for(tenant_id: str, *, email: str | None, consent_id: str) -> str:
    """A stable Contact id. See the module docstring for the email/consent split."""
    key = f"email:{email.strip().lower()}" if email else f"consent:{consent_id}"
    return f"ct_{derive_event_id('contact', tenant_id, key)}"


class IdentityConsumer(Consumer):
    name = "identity"
    handles = (CAPTURED,)

    #: A human may retry one parked capture on its own. Every write here is a
    #: MERGE keyed on the consent, the person and a derived contact id, and none
    #: of it reads state accumulated from earlier events — so re-running one in
    #: isolation produces what the original attempt would have. The gate still
    #: applies on the retry, which is the property that makes this safe rather
    #: than merely idempotent: a capture retried after a withdrawal is refused
    #: exactly as it would be on a replay.
    retryable = True

    async def handle(self, event: EventLog) -> None:
        payload = event.payload
        consent_id = payload.get("consent_id")
        anon_id = payload.get("anon_id")
        tier = payload.get("tier")

        if not consent_id or not anon_id or not tier:
            # The API pins all three, so this is a hand-written row or a producer
            # written against an older shape. Raising sends it to `/ops` with the
            # payload attached, which is the only way anybody finds out.
            raise ValueError(
                f"consent.captured seq={event.seq} needs consent_id, anon_id and "
                f"tier; got {sorted(payload)}"
            )

        contact = payload.get("contact") or {}
        email = contact.get("email")
        contact_id = contact_id_for(event.tenant_id, email=email, consent_id=consent_id)
        at = event.occurred_at.isoformat()

        settings = get_settings()
        async with get_driver().session(database=settings.neo4j_database) as gs:
            # The consent record first, and unconditionally. It is evidence, and
            # it is worth having even in the cases below where nothing is linked
            # — a consent that permitted nothing still happened.
            await graph_repo.upsert_consent_event(
                gs,
                tenant_id=event.tenant_id,
                consent_id=consent_id,
                tier=tier,
                basis=payload.get("basis", ""),
                copy_version=payload.get("copy_version", ""),
                captured_at=payload.get("captured_at") or at,
                captured_by=payload.get("captured_by", ""),
                source=payload.get("source", ""),
                anon_id=anon_id,
                session_id=event.session_id,
                expires_at=payload.get("expires_at"),
            )

            if not await graph_repo.person_exists(
                gs,
                tenant_id=event.tenant_id,
                session_id=event.session_id,
                anon_id=anon_id,
            ):
                # Almost always the graph writer being a poll behind — the two
                # consumers advance independently, and a capture can overtake the
                # detections it refers to. Raising retries with backoff; if it is
                # still missing after that, an operator gets a dead letter naming
                # a track that does not exist, which is the real problem it would
                # otherwise hide.
                raise RuntimeError(
                    f"consent.captured seq={event.seq} names anon_id {anon_id!r}, "
                    f"which is not in session {event.session_id!r} yet"
                )

            created = await graph_repo.identify(
                gs,
                tenant_id=event.tenant_id,
                session_id=event.session_id,
                anon_id=anon_id,
                contact_id=contact_id,
                consent_id=consent_id,
                minimum_tier=MINIMUM_TIER,
                via=payload.get("source") or "manual",
                at=at,
                email=email,
                name=contact.get("name"),
                company=contact.get("company"),
                title=contact.get("title"),
            )

        if created is None:
            # Withdrawn, or a tier that does not permit the link. Both are
            # decisions, not failures — see the module docstring.
            log.info(
                "identity: consent %s does not permit linking %s (tier %s)",
                consent_id,
                anon_id,
                tier,
            )
            return

        async with db.SessionLocal() as session:
            await db.scope_to_tenant(session, event.tenant_id)
            await repository.append_event(
                session,
                EventIn(
                    # Derived from the consent, so a replay re-emits the same
                    # event and the log dedupes it. A random id here would put a
                    # second identity.resolved on the bus for one identification,
                    # and every downstream count of identified visitors would
                    # drift upward on each replay.
                    event_id=derive_event_id(
                        "identity", event.tenant_id, consent_id, contact_id
                    ),
                    tenant_id=event.tenant_id,
                    session_id=event.session_id,
                    type=RESOLVED,
                    payload={
                        "anon_id": anon_id,
                        "contact_id": contact_id,
                        # Carried so a CRM adapter can re-check the justification
                        # it is acting on without going looking for it.
                        "consent_id": consent_id,
                        "tier": tier,
                        "via": payload.get("source") or "manual",
                        "at": at,
                    },
                    occurred_at=event.occurred_at,
                ),
            )
            await session.commit()
