"""
Handoff delivery — the lead leaves the building.

Reads `handoff.lead` and POSTs it, signed, to the URL the deployment configured.
`integrations.md` §5 calls this "the contract, raw", and says it "is also how
*we* dogfood new adapters before writing them" — a HubSpot adapter that maps
fields differently from this one is a bug this destination will have already
exposed.

## Separate from the attribution consumer

Same split as `rules` → `dispatch`, for the same reason: the consumer that
decides a lead exists should not be holding a socket open to somebody else's
server while it does. A destination that is down delays delivery and nothing
else — the handoff is already on the log, and this consumer catches up from its
cursor whenever the endpoint comes back.

## Idempotency is the Phase 3 claim, not the receiver's problem

`app/actions/webhook.py` argues that a receiver can dedupe on
`X-Realmspace-Event-Id`, and it can. That is a fallback, not the mechanism. A
POST that times out after the receiver processed it is ambiguous from here, and
the honest way to resolve it is the one `rule_dispatch` already implements:
claim the delivery in our database before the call goes out, and let the UNIQUE
constraint decide. Migration 0006 widened that table to say which kind of cause
a row is about, so a stuck lead is legible on `/ops` next to a stuck Slack post.

## The signature comes from the rule action, deliberately

`sign` is imported from `app/actions/webhook.py`, whose docstring asked for
exactly that: "Phase 4's adapter should use `sign` from here rather than growing
a second one." Two implementations of an HMAC scheme is two chances to disagree
about what bytes are covered, and a receiver cannot debug the difference.

An unset secret refuses rather than sending unsigned, for the reason that file
gives: a deployment would silently downgrade the only protection the receiver
has, and the receiver cannot see the config that did it.
"""

from __future__ import annotations

import json
import logging

import httpx

from app import db, repository
from app.actions.webhook import sign
from app.config import get_settings
from app.consumers.base import Consumer
from app.cost import meter
from app.models import EventLog

log = logging.getLogger(__name__)

HANDOFF = "handoff.lead"

#: The `action_type` this delivery claims under. A destination that is added
#: later claims under its own name, so one lead can go to several places and
#: each is claimed, retried and stranded independently.
ACTION_TYPE = "handoff_webhook"

#: Distinguishes these rows from rule dispatches on `/ops` (migration 0006).
KIND = "handoff"


class HandoffDeliveryConsumer(Consumer):
    name = "handoff_delivery"
    handles = (HANDOFF,)

    #: Not retryable from `/ops`, for the same reason the dispatcher is not: the
    #: parked row's action is an outbound POST, and re-running one by hand while
    #: its claim still reads `claimed` is precisely the ambiguity the stranded
    #: queue exists to have a human resolve instead.
    retryable = False

    async def handle(self, event: EventLog) -> None:
        settings = get_settings()

        url = settings.handoff_webhook_url
        if not url:
            # Nothing configured is not a failure. A deployment with no
            # destination yet still builds handoffs — they are on the log, and a
            # destination added next week reads them from seq 0. Raising here
            # would fill `/ops` with parked leads nobody asked to deliver.
            log.debug("handoff_delivery: no handoff_webhook_url configured; skipping")
            return

        secret = settings.webhook_signing_secret
        if not secret:
            raise RuntimeError(
                "handoff delivery needs settings.webhook_signing_secret, which is "
                "unset — refusing to POST a lead unsigned, because the receiver "
                "cannot tell an unsigned delivery from a forged one"
            )

        async with db.SessionLocal() as session:
            await db.scope_to_tenant(session, event.tenant_id)

            claim = await repository.claim_dispatch(
                session,
                tenant_id=event.tenant_id,
                fired_event_id=event.event_id,
                # The session, not a rule — this row's cause is an activation's
                # lead rather than a firing. `kind` is what keeps the two
                # readable apart.
                rule_id=event.session_id,
                action_type=ACTION_TYPE,
                kind=KIND,
            )
            if claim is None:
                log.debug(
                    "handoff_delivery: already claimed for %s", event.event_id
                )
                return
            # Visible to any other attempt before the call goes out — see
            # consumers/dispatch.py, which explains why this one commit cannot
            # wait for the end, and why the scope has to be re-declared after it.
            await session.commit()
            await db.scope_to_tenant(session, event.tenant_id)

            body = json.dumps(
                event.payload, separators=(",", ":"), sort_keys=True
            ).encode()

            try:
                async with httpx.AsyncClient(
                    timeout=settings.action_timeout_seconds
                ) as client:
                    response = await client.post(
                        url,
                        content=body,
                        headers={
                            "Content-Type": "application/json",
                            "X-Realmspace-Signature": sign(secret, body),
                            "X-Realmspace-Event-Id": str(event.event_id),
                            # The receiver's own upsert key. Both stages of one
                            # lead carry the same value, which is what makes the
                            # final handoff an update rather than a second lead.
                            "X-Realmspace-Dedupe-Key": str(
                                event.payload.get("dedupe_key", "")
                            ),
                        },
                    )
                    response.raise_for_status()
            except Exception as exc:  # noqa: BLE001 — recorded, then re-raised
                await repository.complete_dispatch(
                    session,
                    dispatch_id=claim.id,
                    status="failed",
                    detail=f"{type(exc).__name__}: {exc}"[:2000],
                )
                await session.commit()
                raise

            await repository.complete_dispatch(
                session,
                dispatch_id=claim.id,
                status="delivered",
                detail=f"POST {url} ({response.status_code})"[:2000],
            )
            await meter(
                session,
                tenant_id=event.tenant_id,
                session_id=event.session_id,
                kind="action_unit",
                amount=1.0,
                unit="actions",
                occurred_at=event.occurred_at,
                cause=(ACTION_TYPE, str(event.event_id)),
                detail={"actionType": ACTION_TYPE, "stage": event.payload.get("stage")},
            )
            await session.commit()
