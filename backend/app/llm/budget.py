"""
A ceiling on what this deployment spends with a model vendor.

Added 2026-08-31, the day after the provider, because of what the key we were
given actually reports: `GET /api/v1/key` answers `limit: null` — no spend cap —
and three people share it. `consumers/insights.py` calls a model **on a timer**
(`insight_interval_minutes`, default 10). An uncapped timer against an uncapped
credential is the one combination worth refusing to ship.

## Not a plan limit, and the difference matters

`PlanLimits.max_ask_queries` exists, is surfaced on `GET /v1/plan`, and fires for
nobody — because no tier states a number. `gtm.md`'s Pavilion row says
*"unlimited Ask the Room"* and the Booth row does not mention Ask at all, so
filling that field in would be inventing a commercial figure on a client's
behalf. `app/plans.py` already declines to do that for `max_integrations`.

This is the other kind of limit: **ours, about our own key**, and unset by
default. Nothing changes for a deployment that does not configure it, which is
what makes it safe to add without deciding anything about pricing.

## Why exhausting it is not an error

Every one of the three spenders already has a deterministic floor it falls back
to when a provider raises — `catalogue.Entry.phrase` for Ask,
`prompts.deterministic_draft` for the SDR, `prompts.deterministic_insight` for
the insight consumer. A spent budget is that same situation and reuses that same
floor. It must not dead-letter an event: the measurement is fine and only the
prose is missing, and a consumer that failed here would stall a cursor over a
sentence.

## The month, on `occurred_at`

`plans.retention_floor` sets the precedent — every window in this system is
measured on when the work happened, not when a row was written, so a batch
replayed late counts against the month it was spent in.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.cost import LlmSpender, spent_tokens


def month_start(now: dt.datetime | None = None) -> dt.datetime:
    """The first instant of the current calendar month, UTC.

    Calendar month rather than a rolling 30 days because that is the unit a
    vendor invoices in, and a budget that disagreed with the bill it is meant to
    protect would be worse than none.
    """
    at = now or dt.datetime.now(dt.timezone.utc)
    return at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def within_budget(
    session: AsyncSession,
    *,
    tenant_id: str,
    spender: LlmSpender,
    now: dt.datetime | None = None,
) -> tuple[bool, str]:
    """`(may_call, reason)` — whether a model call may go out.

    `reason` is empty when it may. When it may not it is a sentence for a person:
    what the budget is, what has gone, and that the deterministic answer is what
    they are getting instead. Every surface here shows an answer's basis, so a
    budget refusal has to be legible in the same place.
    """
    budget = get_settings().llm_monthly_token_budget
    if budget is None:
        return True, ""

    since = month_start(now)
    spent = await spent_tokens(session, tenant_id=tenant_id, since=since)
    if spent < budget:
        return True, ""

    return False, (
        f"this deployment's model budget for {since:%B} is spent "
        f"({spent:,} of {budget:,} tokens), so the {spender} answer is the "
        "measured one written without a model"
    )


__all__ = ["month_start", "within_budget"]
