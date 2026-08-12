"""
`action.type: "log"` — do nothing, and say so.

Three real uses, none of them a placeholder:

- an operator trialling a rule before arming it, who wants to know how often it
  *would* have fired without a room full of staff being told to act on it;
- a rule whose value is the `rule.fired` row itself — a marker on the timeline
  for the report to read later;
- every test in `tests/test_rules.py`, which is about whether the evaluator
  decided correctly and not about whether Slack was reachable.

It still claims a `rule_dispatch` row like any other action. A log rule that
"fired twice" during a replay would be a bug worth catching here, where it is
cheap, rather than the first time somebody points a real action at it.
"""

from __future__ import annotations

import logging

from app.actions.context import DispatchContext

log = logging.getLogger(__name__)


async def deliver(ctx: DispatchContext) -> str:
    message = ctx.action.get("message") or ctx.firing.get("ruleName", "")
    detail = f"{ctx.firing.get('ruleId')}: {message}".strip()
    log.info("rule action log — %s", detail)
    return detail
