"""
`action.type: "webhook"` — POST the firing to a URL the operator supplies.

The bring-your-own escape hatch. `integrations.md` promises a "Generic Webhook —
POST the JSON to a customer URL (HMAC-signed)" for Phase 4's lead handoffs; this
is the same signature scheme, arriving a phase early because a rule action needs
it first. Phase 4's adapter should use `sign` from here rather than growing a
second one.

## Why the body is signed

The URL is the only thing standing between a receiver and anyone who has seen
it — it travels in a rule document, through a browser, and into a database. A
signature lets the receiver tell a firing from this booth from a replay by
somebody who found the URL in a log.

`X-Realmspace-Event-Id` carries the derived `rule.fired` id, so a receiver can
dedupe on it. That matters because this dispatcher retries: a POST that times out
after the receiver processed it will arrive again, and only the receiver can
tell that the second one is the same event.

## Why an unset secret is a refusal rather than an unsigned POST

Falling back to sending unsigned would mean a deployment silently downgrades the
one protection the receiver has, and the receiver — who cannot see this config —
would have no way to notice. Refusing parks it on `/ops` with the reason.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import httpx

from app.actions.context import DispatchContext
from app.config import get_settings


def sign(secret: str, body: bytes) -> str:
    """`sha256=<hex>`, over the exact bytes sent.

    Over the bytes rather than over a dict, because the receiver verifies what
    arrived on the wire and any re-serialisation on either side (key order,
    whitespace) changes the digest while leaving the JSON equivalent. That
    mismatch is the classic way a webhook signature "randomly" fails.
    """
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


async def deliver(ctx: DispatchContext) -> str:
    settings = get_settings()
    action, firing = ctx.action, ctx.firing
    event_id = str(ctx.fired_event_id)

    secret = settings.webhook_signing_secret
    if not secret:
        raise RuntimeError(
            "rule action 'webhook' needs settings.webhook_signing_secret, which is "
            "unset — refusing to POST unsigned, because the receiver cannot tell "
            "an unsigned delivery from a forged one"
        )

    payload = {
        "eventId": event_id,
        "ruleId": firing.get("ruleId"),
        "ruleName": firing.get("ruleName"),
        "triggerType": firing.get("triggerType"),
        "matched": firing.get("matched", {}),
        # Whatever the operator put in the action, so their receiver can route
        # it. Last, so it cannot overwrite the fields above by naming one.
        **action.get("payload", {}),
    }
    # Serialise once and send those exact bytes — see `sign`.
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()

    async with httpx.AsyncClient(timeout=settings.action_timeout_seconds) as client:
        response = await client.post(
            action["url"],
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Realmspace-Signature": sign(secret, body),
                "X-Realmspace-Event-Id": event_id,
            },
        )
        response.raise_for_status()

    return f"POST {action['url']} ({response.status_code})"
