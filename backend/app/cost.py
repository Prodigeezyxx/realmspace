"""
The cost meter — one way to say "this just spent money".

Roadmap Phase 3: "`cost.metered` emitters on anything that costs money (LLM
tokens, action units) + cost tile on `/live` — unit economics from real data."

## Why a helper rather than each caller appending its own event

Every spender is a different kind of thing — an LLM call in Ask, a Slack post
from a rule's dispatcher, an enrichment lookup in P4 — and the one property they
must share is the one each of them would get wrong differently: **the event_id
must be derived from the cause, not random.**

A cost event with a random id is double-counted on every replay, and unlike a
double-counted dwell it is double-counted in the direction that makes the
product look worse than it is, in the figure a client is most likely to
challenge. `consumers/ids.py` explains the mechanism in full.

So the meter takes the *cause* of the spend rather than an id, and derives one.
Two calls describing the same spend produce the same id and the bus dedupes the
second.

## Nothing calls this yet, on purpose

No code on this track spends money today: Ask is not wired to a provider and the
rule dispatchers are not built. This is registered ahead of its callers for the
same reason the Phase 3 event types were (`event-bus-spec.md` §3) — the first
person to need it should find a meter with a settled contract, not invent a
second way to write the same event.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.consumers.ids import derive_event_id
from app.models import EventLog
from app.schemas import EventIn

#: The kinds in `dashboard/src/lib/contracts/events.ts` (`CostMeteredPayload`).
#: A closed set here and there: unlike the event taxonomy, which is additive by
#: design, a cost kind nobody recognises cannot be summed into unit economics —
#: it would sit in the log looking like it had been counted.
CostKind = Literal["llm_tokens", "enrichment_credit", "storage", "other"]

EVENT_TYPE = "cost.metered"


async def meter(
    session: AsyncSession,
    *,
    tenant_id: str,
    session_id: str,
    kind: CostKind,
    amount: float,
    unit: str,
    occurred_at: dt.datetime,
    cause: tuple[str | int, ...],
    detail: dict[str, Any] | None = None,
) -> tuple[EventLog, bool]:
    """Record a spend on the bus. Returns `(row, created)` like `append_event`.

    `unit` is what `amount` counts — `"tokens"`, `"credits"`, or an ISO currency
    code such as `"USD"` when the spend is already money. The reader
    (`dashboard/src/lib/roi/cost.ts`) only totals a currency figure across events
    whose unit *is* a currency, because adding tokens to dollars produces a
    number that looks like unit economics and is not.

    `cause` is the identity of the thing that spent — for a rule dispatch,
    `(rule_id, rule_fired_event_id)`; for an LLM call, the id of the question
    being answered. It must be stable across a replay of the same input, which
    is exactly what makes the derived id safe. Passing something like a
    timestamp or a fresh uuid defeats the whole file.

    The caller owns the commit, matching `repository.append_event`, so a spend
    and whatever it paid for land in one transaction or neither does.
    """
    if amount < 0:
        raise ValueError(f"a spend cannot be negative: {amount} {unit}")
    if not unit:
        raise ValueError("unit is required — an amount with no unit sums into nothing")

    payload: dict[str, Any] = {"kind": kind, "amount": amount, "unit": unit}
    if detail:
        payload["detail"] = detail

    event = EventIn(
        event_id=derive_event_id("cost", tenant_id, session_id, kind, *cause),
        tenant_id=tenant_id,
        session_id=session_id,
        type=EVENT_TYPE,
        payload=payload,
        # The spend happened when the work did. Using now() here would put a
        # replayed cost at the time of the replay, which is how a session's
        # metered spend ends up attributed to a session that ran last week.
        occurred_at=occurred_at,
    )
    return await repository.append_event(session, event)
