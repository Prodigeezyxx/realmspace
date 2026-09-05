"""
The re-anonymiser — undoing an identification when somebody takes it back.

`consent-and-identity.md` §5: *"Consent withdrawn mid-visit → set `withdrawn_at`;
a bus consumer re-anonymises: drops the `IDENTIFIED_AS` edge, deletes/redacts the
`Contact` per policy, and emits a `crm.retract` where the CRM supports it. The
anonymous path survives for aggregate ROI."*

  consent.withdrawn → stamp the ConsentEvent, drop the link, redact the Contact
                    → crm.retract, one per contact that had been pushed

## Separate from the identity consumer, for the same reason dispatch is separate from rules

The identity consumer decides; this undoes. Keeping them apart means a withdrawal
is processed even when identification is failing, and — more importantly — that
the code path a person exercises when they change their mind is not sharing state
with the code path that had an interest in them agreeing.

## What survives, and why that is not a compromise

The Person, its zone edges, its dwells and every figure derived from them stay
exactly as they were. That data was never consent-gated — `privacy.md` has the
anonymous path running with no consent at all — and deleting it would silently
change reports already delivered from that activation, for a person the report
never named.

The ConsentEvent survives too, stamped with `withdrawn_at`. It is the evidence
that permission was given and then withdrawn, and a deployment that deleted it
would have nothing to answer with if the withdrawal itself were ever disputed.

What goes is the link and the PII, which is all that consent ever granted.

## Why `crm.retract` is emitted here and not sent here

The local graph is ours; a record already pushed to HubSpot is not. This consumer
redacts what it owns and puts `crm.retract` on the bus for the CRM adapters
(Phase 4's later half) to act on. Same split as `rules` → `dispatch`, for the
same reason: a slow third-party API has no business inside the path that makes
the withdrawal true locally.

It is emitted even though no adapter exists yet. A withdrawal that happened
before the adapters shipped still has to be retractable *by* them when they do —
the event is on the log, so a CRM adapter starting from seq 0 will find it.
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

WITHDRAWN = "consent.withdrawn"
RETRACT = "crm.retract"

#: Where a retraction is aimed when nothing has said otherwise.
#:
#: `crm.retract` carries a `destination` because a contact may have been pushed
#: to several. Until the attribution consumer records where a handoff actually
#: went (Phase 4's later half), the honest value is "everywhere" rather than a
#: named CRM this deployment may not even use — an adapter reads its own name or
#: this and acts; it never has to guess whether silence meant it.
ALL_DESTINATIONS = "all"


class ReAnonymiseConsumer(Consumer):
    name = "reanonymise"
    handles = (WITHDRAWN,)

    #: Retryable. The graph writes are MERGE/SET on stable keys and the retract
    #: id is derived, so re-running one parked withdrawal in isolation produces
    #: what the original attempt would have. This is also the one consumer where
    #: a stuck event is least acceptable — the parked event is a person's
    #: withdrawal, and it should be one click to try again.
    retryable = True

    async def handle(self, event: EventLog) -> None:
        payload = event.payload
        consent_id = payload.get("consent_id")
        contact_id = payload.get("contact_id")
        anon_id = payload.get("anon_id")

        if not (consent_id or contact_id or anon_id):
            raise ValueError(
                f"consent.withdrawn seq={event.seq} names nobody — it needs at "
                "least one of consent_id, contact_id, anon_id"
            )

        withdrawn_at = payload.get("withdrawn_at") or event.occurred_at.isoformat()
        settings = get_settings()

        async with get_driver().session(database=settings.neo4j_database) as gs:
            retracted = await graph_repo.re_anonymise(
                gs,
                tenant_id=event.tenant_id,
                withdrawn_at=withdrawn_at,
                consent_id=consent_id,
                contact_id=contact_id,
                anon_id=anon_id,
                session_id=event.session_id if anon_id else None,
            )

            if consent_id:
                # Also stamp the consent directly. `re_anonymise` matches from
                # the Contact, so a T1 consent that never produced one — somebody
                # agreed and gave no details, then changed their mind — would
                # otherwise stay valid, and a replay of the capture would happily
                # identify them afterwards.
                await graph_repo.withdraw_consent(
                    gs,
                    tenant_id=event.tenant_id,
                    consent_id=consent_id,
                    withdrawn_at=withdrawn_at,
                )

        if not retracted:
            # Nothing was linked: a withdrawal before any identification, or a
            # replay of one already carried out. Both are ordinary, and neither
            # is worth a `crm.retract` — there is no pushed record to undo.
            log.info(
                "reanonymise: nothing linked for seq=%s (consent=%s contact=%s anon=%s)",
                event.seq,
                consent_id,
                contact_id,
                anon_id,
            )
            return

        async with db.SessionLocal() as session:
            await db.scope_to_tenant(session, event.tenant_id)
            for retracted_id in retracted:
                await repository.append_event(
                    session,
                    EventIn(
                        event_id=derive_event_id(
                            "crm_retract",
                            event.tenant_id,
                            retracted_id,
                            payload.get("reason", ""),
                        ),
                        tenant_id=event.tenant_id,
                        session_id=event.session_id,
                        type=RETRACT,
                        payload={
                            "contact_id": retracted_id,
                            "destination": ALL_DESTINATIONS,
                            "reason": (
                                "erasure_request"
                                if payload.get("reason") == "erasure_request"
                                else "consent_withdrawn"
                            ),
                            # Mirrors `handoff.lead`'s key, per event-bus-spec.md
                            # §3, so an adapter retrying a retraction cannot fire
                            # it twice at the CRM.
                            "dedupe_key": f"{retracted_id}:{withdrawn_at}",
                        },
                        occurred_at=event.occurred_at,
                    ),
                )
            await session.commit()
