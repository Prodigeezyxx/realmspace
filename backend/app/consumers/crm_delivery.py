"""
The lead lands in the client's CRM.

`handoff.lead` → every CRM this tenant has connected. The webhook destination
(`consumers/handoff_delivery.py`) already sends the same event as raw JSON;
`integrations.md` §5 calls that "the contract, raw … also how *we* dogfood new
adapters before writing them", and this is the first adapter it was dogfooding
for.

## A second destination, not a rewrite

`handoff_delivery.py`'s `ACTION_TYPE` said what would happen next: *"a
destination that is added later claims under its own name, so one lead can go to
several places and each is claimed, retried and stranded independently."* That
is exactly this. The claim shape — write to `rule_dispatch` before the call goes
out, let the UNIQUE constraint decide — is the one Phase 3 built and Phase 4
widened, unchanged.

`action_type` is `crm:{provider}`, so a tenant with HubSpot and Pipedrive gets
two claims for one lead. One CRM being down cannot block the other, and a
stranded row names which.

## Failures are collected, not fatal to the whole event

Each destination is attempted even when an earlier one failed, because they are
independent and a HubSpot outage should not keep a lead out of Pipedrive. Only
then does the handler raise, so `base.Consumer` retries the event and
dead-letters it if it keeps failing — and on that retry the destinations that
already delivered refuse their own claims, so the retry reaches only the ones
that did not.

## Nothing configured is not a failure

Same as the webhook's unset URL: the handoff is on the log, and a CRM connected
next week reads it from seq 0. A tenant that has connected nothing has not asked
for anything to be delivered.

## The adapter is told what the CRM called this contact last time

`crm_link` already records it, for the withdrawal path. Handing it to `upsert`
as well is what lets the three adapters with no native upsert — Salesforce and
Dynamics upsert only against a key the client configured, Pipedrive not at all —
update rather than search. See `app/crm/base.py`, which states the whole
argument; here it is one read before the call.

The link is looked up live rather than carried on the event, because an admin
can revoke and reconnect a CRM between two stages of one lead, and the id from
the old connection would address a record in an org we are no longer talking to.

## A handoff the adapter cannot key on is delivered-with-nothing, not failed

`map()` returning `None` — a contact captured with no email — closes the claim as
delivered with the reason in `detail`. Parking it would put a row on `/ops` that
no human action could ever resolve: there is no email to add and nothing to retry.

## A handoff that was never about a person is not claimed at all

An anonymous handoff (`integrations.md` §2, no `contact` key) is different in
kind from the case above, and treating them alike was the wrong instinct. Those
are one per un-consented visitor at `session.ended`, so a busy day times two
connected CRMs is a thousand `rule_dispatch` rows all saying the same thing about
somebody no CRM was ever going to hear about. The claim exists to make an
outbound call happen exactly once; there is no outbound call here.

So it returns before claiming, and logs once for the event rather than once per
destination. The distinction is the presence of the `contact` key, not whether
the contact has an email — a handoff *about* somebody the adapter cannot key on
still gets its row, which is what an operator needs to see when a capture surface
starts dropping email addresses.
"""

from __future__ import annotations

import logging

from app import crm, db, repository
from app.consumers.base import Consumer
from app.cost import meter
from app.models import EventLog

log = logging.getLogger(__name__)

HANDOFF = "handoff.lead"

#: Same `kind` as the webhook delivery: on `/ops` these are stuck leads, and a
#: stuck lead and a stuck Slack post are the distinction that column exists for.
KIND = "handoff"


class CrmDeliveryConsumer(Consumer):
    name = "crm_delivery"
    handles = (HANDOFF,)

    #: Not retryable from `/ops`, for the same reason the webhook delivery and
    #: the rule dispatcher are not: the parked row's act is an outbound write to
    #: somebody else's CRM, and re-running one by hand while its claim still
    #: reads `claimed` is the ambiguity the stranded queue exists to have a
    #: human resolve instead.
    retryable = False

    async def handle(self, event: EventLog) -> None:
        if "contact" not in event.payload:
            log.debug(
                "crm_delivery: %s is an anonymous handoff; no CRM destination "
                "has anything to receive",
                event.event_id,
            )
            return

        async with db.SessionLocal() as session:
            await db.scope_to_tenant(session, event.tenant_id)
            providers = [
                row.provider
                for row in await repository.list_integrations(
                    session, tenant_id=event.tenant_id, active_only=True
                )
            ]

        if not providers:
            log.debug(
                "crm_delivery: tenant %s has no CRM connected; skipping",
                event.tenant_id,
            )
            return

        failures: list[str] = []
        for provider in providers:
            try:
                await self._deliver(event, provider)
            except Exception as exc:  # noqa: BLE001 — collected, then re-raised
                log.warning(
                    "crm_delivery: %s failed for %s: %s",
                    provider,
                    event.event_id,
                    exc,
                )
                failures.append(f"{provider}: {type(exc).__name__}: {exc}")

        if failures:
            raise RuntimeError("; ".join(failures))

    async def _deliver(self, event: EventLog, provider: str) -> None:
        action_type = f"crm:{provider}"

        async with db.SessionLocal() as session:
            await db.scope_to_tenant(session, event.tenant_id)

            claim = await repository.claim_dispatch(
                session,
                tenant_id=event.tenant_id,
                fired_event_id=event.event_id,
                # The session, not a rule — this row's cause is an activation's
                # lead. `kind` is what keeps that legible on `/ops`.
                rule_id=event.session_id,
                action_type=action_type,
                kind=KIND,
            )
            if claim is None:
                log.debug(
                    "crm_delivery: %s already claimed for %s",
                    action_type,
                    event.event_id,
                )
                return
            # Committed before the call goes out — see consumers/dispatch.py for
            # why this one commit cannot wait, and why the scope has to be
            # re-declared afterwards.
            await session.commit()
            await db.scope_to_tenant(session, event.tenant_id)

            integration = await repository.get_integration(
                session, tenant_id=event.tenant_id, provider=provider
            )
            if integration is None:
                # Revoked between the list above and here, or removed by hand.
                await repository.complete_dispatch(
                    session,
                    dispatch_id=claim.id,
                    status="failed",
                    detail=f"{provider} is no longer configured for this tenant",
                )
                await session.commit()
                return

            contact_id = (event.payload.get("contact") or {}).get("id")
            known_links = (
                await repository.links_for_contact(
                    session,
                    tenant_id=event.tenant_id,
                    contact_id=contact_id,
                    provider=provider,
                )
                if contact_id
                else []
            )

            try:
                adapter = crm.adapter_for(integration)
                payload = adapter.map(event.payload)
                if payload is None:
                    await repository.complete_dispatch(
                        session,
                        dispatch_id=claim.id,
                        status="delivered",
                        detail=(
                            f"{provider} had nothing to key this handoff on — no "
                            "email, so nothing was sent and nothing is missing"
                        ),
                    )
                    await session.commit()
                    return
                external_id = await adapter.upsert(
                    payload,
                    external_id=known_links[0].external_id if known_links else None,
                )
            except Exception as exc:  # noqa: BLE001 — recorded, then re-raised
                await repository.complete_dispatch(
                    session,
                    dispatch_id=claim.id,
                    status="failed",
                    detail=f"{type(exc).__name__}: {exc}"[:2000],
                )
                await session.commit()
                raise

            if external_id and contact_id:
                # The receipt a withdrawal will read. Written before the dispatch
                # is closed, in the same transaction: a link missing for a
                # contact that reached the CRM is a withdrawal that cannot be
                # carried out, which is worse than a duplicate claim.
                await repository.record_crm_link(
                    session,
                    tenant_id=event.tenant_id,
                    provider=provider,
                    contact_id=contact_id,
                    external_id=external_id,
                    dedupe_key=event.payload.get("dedupe_key") or "",
                    session_id=event.session_id,
                )

            await repository.complete_dispatch(
                session,
                dispatch_id=claim.id,
                status="delivered",
                detail=f"{provider} contact {external_id}"[:2000],
            )
            await meter(
                session,
                tenant_id=event.tenant_id,
                session_id=event.session_id,
                kind="action_unit",
                amount=1.0,
                unit="actions",
                occurred_at=event.occurred_at,
                cause=(action_type, str(event.event_id)),
                detail={
                    "actionType": action_type,
                    "stage": event.payload.get("stage"),
                },
            )
            await session.commit()
