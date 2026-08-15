"""
The outbound half of a withdrawal.

`consumers/reanonymise.py` has emitted `crm.retract` since Phase 4 opened, and
`event-bus-spec.md` §3 has listed its consumer as "CRM adapters" for as long.
There was none. A visitor who withdrew had their local record redacted while the
copy already pushed to a client's CRM sat there untouched — the half of
`consent-and-identity.md` §5 that leaves this building.

## `destination: "all"` finally resolves to something

The re-anonymiser's own comment said what was missing: *"until the attribution
consumer records where a handoff actually went (Phase 4's later half), the
honest value is 'everywhere' rather than a named CRM this deployment may not
even use"*. `crm_link` (migration 0008) is that record, so "all" now means the
destinations that really received this contact, and a named destination means
that one.

## Retryable, unlike the delivery consumers

`consumers/crm_delivery.py` and the webhook delivery are not retryable from
`/ops`, because re-running an outbound push by hand while its claim reads
`claimed` risks the double-write those claims exist to prevent. This one is,
for the reason `reanonymise.py` gives about itself: the parked event is a
person's withdrawal not yet honoured, and that should be one click to try again
rather than a cursor rewind. The claim still protects it — a delivered
retraction refuses a second — and a retraction is in any case the one outbound
call whose repeat is harmless, because the second attempt finds the record
already gone.

## The link is redacted when the retraction succeeds

`crm_link.dedupe_key` is `tenant:email`. It loses the half that names a person
using `attribution.ledger.redact_dedupe_key` — the same function the ledger
uses, imported rather than copied, because two redactions are two chances to
disagree about which half that is and the disagreement shows up as an email
surviving a withdrawal.

What stays is the row, the `external_id`, the timestamp and what the CRM said.
That is the audit trail: "we pushed this person here, and on this date we went
and removed them", answerable without naming them.

## Not metered

`consumers/crm_delivery.py` meters each push as an `action_unit`, because a lead
delivered is work a client is buying. A retraction is not: it is the cost of
having taken the data in the first place, and putting it on the cost tile would
mean a client's unit economics get worse the more withdrawals they honour.

## An unretractable destination is a dead letter, deliberately

If an adapter has no `retract` capability, or the credential has gone, the event
fails and lands on `/ops` for a human. The alternative — logging it and moving
on — would make a withdrawal that silently did not happen look exactly like one
that did.
"""

from __future__ import annotations

import logging

from app import crm, db, repository
from app.attribution.ledger import redact_dedupe_key
from app.consumers.base import Consumer
from app.consumers.reanonymise import ALL_DESTINATIONS
from app.models import EventLog

log = logging.getLogger(__name__)

RETRACT = "crm.retract"

#: Not `handoff`: a stuck lead and a stuck withdrawal are different urgencies to
#: the operator reading `/ops`, which is the whole reason migration 0006 added
#: the column. One is a lead that has not arrived yet; the other is a person's
#: consent decision not yet carried out.
KIND = "retract"


class CrmRetractConsumer(Consumer):
    name = "crm_retract"
    handles = (RETRACT,)

    #: See the module docstring — the exception among the outbound consumers.
    retryable = True

    async def handle(self, event: EventLog) -> None:
        payload = event.payload
        contact_id = payload.get("contact_id")
        if not contact_id:
            raise ValueError(
                f"crm.retract seq={event.seq} names no contact_id — there is "
                "nothing to look up and nothing to retract"
            )

        destination = payload.get("destination") or ALL_DESTINATIONS
        provider = None if destination == ALL_DESTINATIONS else destination

        async with db.SessionLocal() as session:
            await db.scope_to_tenant(session, event.tenant_id)
            links = await repository.links_for_contact(
                session,
                tenant_id=event.tenant_id,
                contact_id=contact_id,
                provider=provider,
            )

        if not links:
            # Ordinary, and two different ordinary things: nothing was ever
            # pushed for this contact, or a replayed withdrawal is finding the
            # work already done. Neither needs a human.
            log.info(
                "crm_retract: nothing outstanding for contact %s (destination=%s)",
                contact_id,
                destination,
            )
            return

        failures: list[str] = []
        for link in links:
            try:
                await self._retract(event, link.id, link.provider)
            except Exception as exc:  # noqa: BLE001 — collected, then re-raised
                log.warning(
                    "crm_retract: %s failed for contact %s: %s",
                    link.provider,
                    contact_id,
                    exc,
                )
                failures.append(f"{link.provider}: {type(exc).__name__}: {exc}")

        if failures:
            raise RuntimeError("; ".join(failures))

    async def _retract(self, event: EventLog, link_id: int, provider: str) -> None:
        action_type = f"crm_retract:{provider}"

        async with db.SessionLocal() as session:
            await db.scope_to_tenant(session, event.tenant_id)

            claim = await repository.claim_dispatch(
                session,
                tenant_id=event.tenant_id,
                fired_event_id=event.event_id,
                rule_id=event.session_id,
                action_type=action_type,
                kind=KIND,
            )
            if claim is None:
                log.debug(
                    "crm_retract: %s already claimed for %s",
                    action_type,
                    event.event_id,
                )
                return
            await session.commit()
            await db.scope_to_tenant(session, event.tenant_id)

            # Re-read inside this session: the row may have been retracted by a
            # concurrent attempt between the listing and this claim.
            link = await repository.get_crm_link(session, link_id=link_id)
            if link is None or link.retracted_at is not None:
                await repository.complete_dispatch(
                    session,
                    dispatch_id=claim.id,
                    status="delivered",
                    detail=f"{provider} link was already retracted",
                )
                await session.commit()
                return

            # Deliberately not `active_only`: revoking an integration keeps the
            # credential precisely so a withdrawal arriving after an admin
            # disconnects the CRM can still be carried out.
            integration = await repository.get_integration(
                session, tenant_id=event.tenant_id, provider=provider
            )
            if integration is None:
                await repository.complete_dispatch(
                    session,
                    dispatch_id=claim.id,
                    status="failed",
                    detail=(
                        f"{provider} has no stored credential, so this contact "
                        "cannot be retracted from it"
                    ),
                )
                await session.commit()
                raise RuntimeError(
                    f"cannot retract from {provider}: no credential for tenant "
                    f"{event.tenant_id}"
                )

            try:
                adapter = crm.adapter_for(integration)
                if not adapter.capabilities().get("retract"):
                    raise RuntimeError(
                        f"{provider} cannot retract a pushed record; this "
                        "withdrawal needs to be carried out by hand"
                    )
                detail = await adapter.retract(link.external_id)
            except Exception as exc:  # noqa: BLE001 — recorded, then re-raised
                await repository.complete_dispatch(
                    session,
                    dispatch_id=claim.id,
                    status="failed",
                    detail=f"{type(exc).__name__}: {exc}"[:2000],
                )
                await session.commit()
                raise

            await repository.mark_link_retracted(
                session,
                link_id=link.id,
                dedupe_key=redact_dedupe_key(link.dedupe_key),
                detail=detail,
            )
            await repository.complete_dispatch(
                session,
                dispatch_id=claim.id,
                status="delivered",
                detail=detail[:2000],
            )
            await session.commit()
