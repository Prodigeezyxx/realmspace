"""
`action.type: "slack"` — post a message to a channel.

The acceptance criterion for the whole phase names this one: *"when 5 people
dwell at entrance 30s → ping Slack" fires live in `< 3s`*.

## Where the credential is, and is not

The rule document names a `channel`. It does not carry a webhook URL, and it
must not: a rule is authored in a browser, stored per tenant, and returned by
`GET /v1/rules`, so a credential inside one would be readable by every operator
who can list rules and would be sitting in a JSONB column in a backup. The URL
is deployment configuration (`settings.slack_webhook_url`).

An incoming webhook URL is bound to one channel at Slack's end, so `channel` is
advisory — it goes in the payload for the readers who honour it and is recorded
in the dispatch detail either way. A deployment that needs several channels
needs several URLs, which is a settings change rather than a spec change.

## Why an unconfigured Slack raises

Nothing is more useless than a dispatcher that reports success for a message
nobody received. With no URL configured this raises, `base.Consumer` retries and
then parks it in the `/ops` queue with the reason attached — an operator sees
"slack_webhook_url is not set" next to the rule that wanted it.
"""

from __future__ import annotations

import httpx

from app.actions.context import DispatchContext
from app.config import get_settings


async def deliver(ctx: DispatchContext) -> str:
    settings = get_settings()
    action, firing = ctx.action, ctx.firing

    url = settings.slack_webhook_url
    if not url:
        raise RuntimeError(
            "rule action 'slack' needs settings.slack_webhook_url, which is unset — "
            f"rule {firing.get('ruleId')!r} wanted channel {action.get('channel')!r}"
        )

    body = {
        "channel": action["channel"],
        "text": action["message"],
        # Enough context to act on without opening the dashboard. An alert that
        # says only "5 at entrance" leaves whoever reads it on a conference floor
        # with no way to tell which rule or which room it is about.
        "attachments": [
            {
                "text": (
                    f"rule {firing.get('ruleName')} ({firing.get('ruleId')}) · "
                    f"{firing.get('triggerType')} · "
                    f"matched {firing.get('matched', {}).get('observed')}"
                )
            }
        ],
    }

    async with httpx.AsyncClient(timeout=settings.action_timeout_seconds) as client:
        response = await client.post(url, json=body)
        # raise_for_status rather than checking a status code: a 4xx from Slack
        # is a permanent failure (a bad URL, a revoked hook) and a 5xx is
        # transient, and base.Consumer's backoff handles the second correctly
        # while parking the first after the same three attempts. Distinguishing
        # them here would mean deciding which Slack errors are worth retrying,
        # which is a table that goes stale.
        response.raise_for_status()

    return f"posted to {action['channel']} ({response.status_code})"
