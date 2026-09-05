"""
`action.type: "screen_swap"` — change what a screen in the room is showing.

The other half of the "Next-Step surface": where `staff_prompt` asks a person to
act, this acts directly. Same delivery path and same reasoning as
`staff_prompt.py` — appended to the bus rather than pushed at the hub, so a
screen that reconnects mid-session catches up from its cursor instead of showing
whatever it was showing before it dropped.

That reconnect case is the one that matters here. A missed staff prompt is a
missed message; a missed screen swap leaves a display stuck on the wrong content
for the rest of the activation, with nothing to correct it until another rule
fires.
"""

from __future__ import annotations

from app import repository
from app.actions.context import DispatchContext
from app.consumers.ids import derive_event_id
from app.schemas import EventIn

EVENT_TYPE = "rule.screen_swap"


async def deliver(ctx: DispatchContext) -> str:
    event = EventIn(
        event_id=derive_event_id(
            "action", ctx.tenant_id, ctx.session_id, str(ctx.fired_event_id), EVENT_TYPE
        ),
        tenant_id=ctx.tenant_id,
        session_id=ctx.session_id,
        type=EVENT_TYPE,
        payload={
            "screenId": ctx.action["screenId"],
            "contentId": ctx.action["contentId"],
            "ruleId": ctx.firing.get("ruleId"),
            "ruleName": ctx.firing.get("ruleName"),
        },
        occurred_at=ctx.occurred_at,
    )
    await repository.append_event(ctx.session, event)
    return f"screen {ctx.action['screenId']} → {ctx.action['contentId']}"
