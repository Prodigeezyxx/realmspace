"""
What a rule does when it fires — the five action types of ADR-002.

One module per action, each exposing `deliver(action, firing) -> str`, returning
a short line about what happened for the `rule_dispatch` row and raising on
failure. Raising rather than returning a status is deliberate: `base.Consumer`
already owns retry, exponential backoff and dead-lettering, and an action that
swallowed its own failure would keep all of that from happening — the operator's
Slack post would vanish without appearing in the `/ops` queue that exists to
catch exactly this.

`registry` maps `action.type` to a handler. It is a closed set here and in
`schemas.RuleAction`, unlike the event taxonomy: an action type nothing
dispatches is a rule that looks armed and does nothing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.actions import log, screen_swap, slack, staff_prompt, webhook

#: Signature: (action document, rule.fired payload, event) -> detail line.
Deliver = Callable[..., Awaitable[str]]

registry: dict[str, Any] = {
    "slack": slack,
    "webhook": webhook,
    "screen_swap": screen_swap,
    "staff_prompt": staff_prompt,
    "log": log,
}

#: Actions that spend something. `screen_swap` and `staff_prompt` go out over a
#: WebSocket this process is already running, and `log` writes a log line — none
#: of those cost anything, and metering them at zero would put three lines
#: reading "0" on an operator's cost tile.
METERED = ("slack", "webhook")

__all__ = ["registry", "METERED", "Deliver"]
