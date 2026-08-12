"""
What every action handler is given.

One shape rather than five signatures, so `registry[type].deliver(ctx)` is a
single call site in the dispatcher and adding a sixth action does not change it.
In its own module because `actions/__init__.py` imports the handlers to build the
registry, and a handler importing the context back from there would be a cycle.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class DispatchContext:
    """One firing, about to be acted on."""

    #: The `action` sub-document of the rule, as it was when the rule matched —
    #: copied into the firing by the evaluator rather than re-read from the
    #: rules table, so an edit between firing and dispatch does not change what
    #: the booth decided to do (consumers/rules.py, `_fired`).
    action: dict[str, Any]

    #: The whole `rule.fired` payload: ruleId, ruleName, triggerType,
    #: triggerSeq, condition, action, matched.
    firing: dict[str, Any]

    #: The derived id of the `rule.fired` event. The idempotency key for this
    #: dispatch, and what an outbound request quotes so a receiver can dedupe.
    fired_event_id: uuid.UUID

    tenant_id: str
    session_id: str

    #: When the rule fired, not now. An action that writes an event back onto the
    #: bus dates it from here, so a replay reproduces the original ordering.
    occurred_at: dt.datetime

    #: Tenant-scoped, already in the transaction that holds the dispatch claim.
    #: An action that appends an event uses this so the append and the claim
    #: commit together or not at all.
    session: AsyncSession
