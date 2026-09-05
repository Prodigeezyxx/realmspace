"""
`action.type: "staff_prompt"` — tell a human on the floor to do something.

The "Next-Step surface" of the roadmap's Phase 3 goal, and the action the `< 3s`
SLA is really about: Slack can be a second late without anyone noticing, but a
prompt to greet the group at the entrance is worth nothing after they have left.

## Why this appends an event instead of pushing to the hub directly

`hub.py` is in-process fan-out, and `hub.broadcast` would work from here. It
would also mean the prompt exists only for the sockets connected at that instant:
a tablet that reconnects ten seconds later has no way to learn about it, because
its `since_seq` catch-up reads the log and the prompt was never in the log.

Appending puts it on the bus, where `BroadcastConsumer` — which handles
everything — carries it to the room, and where it is still there for the
reconnect, the report, and the question "what did we actually tell staff to do?".

## Why a type of its own rather than the browser reading `rule.fired`

`rule.fired` already reaches the browser (broadcast handles every type), so the
dashboard *could* look at each firing and check whether its action is a staff
prompt. That would put a third implementation of the rule spec in the browser,
which is the split-brain ADR-002 exists to end. A distinct type means the client
subscribes to `rule.staff_prompt` and renders it, knowing nothing about rules.

`rule.` is already an accepted namespace (`schemas.EVENT_NAMESPACES`), so this
needs no contract change — which is the whole point of §3's pre-registration.
"""

from __future__ import annotations

from app import repository
from app.actions.context import DispatchContext
from app.consumers.ids import derive_event_id
from app.schemas import EventIn

EVENT_TYPE = "rule.staff_prompt"


async def deliver(ctx: DispatchContext) -> str:
    event = EventIn(
        # Derived from the firing and the action type, so a retried dispatch
        # writes the same event rather than a second prompt about the same
        # moment. The claim in rule_dispatch normally prevents a second attempt
        # from getting this far; this is the belt to its braces, and the reason
        # a crash between claim and commit is harmless.
        event_id=derive_event_id(
            "action", ctx.tenant_id, ctx.session_id, str(ctx.fired_event_id), EVENT_TYPE
        ),
        tenant_id=ctx.tenant_id,
        session_id=ctx.session_id,
        type=EVENT_TYPE,
        payload={
            "message": ctx.action["message"],
            "zoneId": ctx.action.get("zoneId"),
            "priority": ctx.action.get("priority", "normal"),
            # So the surface can say which rule asked for this, and so an
            # operator can find and disable a prompt that keeps firing.
            "ruleId": ctx.firing.get("ruleId"),
            "ruleName": ctx.firing.get("ruleName"),
        },
        occurred_at=ctx.occurred_at,
    )
    await repository.append_event(ctx.session, event)
    return f"prompt queued: {ctx.action['message'][:60]}"
