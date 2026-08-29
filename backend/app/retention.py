"""
What a retention purge may remove, and the three things that stop it.

`gtm.md` sells "30-day data retention" and "90-day retention". `app/plans.py`
turned that into a clamp on what a client may *read*, which is half the promise:
the rows stayed, so the data outlived the window and was merely invisible to the
person who paid for it. This is the other half.

## The payload goes; the row stays

`repository.purge_events` empties payloads and leaves `seq`, `event_id`, `type`
and both timestamps. That is not squeamishness about deleting — it is what keeps
the rest of the system truthful. Derived event ids still resolve,
`dead_letter.event_seq` still points at a row, cursors stay contiguous, and a
replay produces the same ids instead of a parallel set of duplicates.

What it costs is that a purged row still exists, so "we deleted it" would be a
promise the database contradicts. `privacy.md` says skeleton, not nothing.

## Three refusals, and the first is the sharp one

A purge is destructive and irreversible, so the interesting part is when it
declines. All three raise and let `base.Consumer` retry with backoff, landing on
`/ops` with the reason if they persist — the shape `consumers/erasure.py` argues
for, and for the same reason: the conditions are properties of the moment the
work is attempted, not of the moment it was requested.

  1. **A consumer has not caught up.** Emptying an event nothing has processed
     means the graph never receives it, and the activation is quietly short of
     the data its own report is built from. There is no error and no gap — just
     a smaller number. This is the direct analogue of erasure's "refuse until
     the retraction has landed".

  2. **An erasure is in flight.** Both rewrite log rows and only one of them is
     somebody's legal right. The purge waits rather than racing it.

  3. **An unresolved dead letter points into the window.** A parked event whose
     payload is gone can never be retried, and `/ops` would hold a row nobody
     can act on — the "queue full of things nobody can clear" that page was
     designed to avoid.

## What is never purged

`erasure.*` and `retention.*` — see `repository.PURGE_EXEMPT_PREFIXES`. They are
the record that the purging and the erasing happened, and a retention policy that
ate its own receipts would have nothing left to prove it ran.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ConsumerCursor, DeadLetter, EventLog

#: Requested by an admin, done by the consumer.
PURGE_REQUESTED = "retention.purge_requested"
#: The receipt: counts, ids and the floor. Never content.
PURGED = "retention.purged"

ERASURE_REQUESTED = "erasure.requested"
ERASURE_COMPLETED = "erasure.completed"


class NotYet(RuntimeError):
    """A condition that will pass on its own. Raised so the consumer retries."""


async def highest_purgeable_seq(
    session: AsyncSession, *, tenant_id: str, before: dt.datetime
) -> int:
    """The last seq the purge would touch, or 0.

    Computed before anything is written, because two of the three checks below
    are about *this* seq and the third reads the same window.
    """
    from app.repository import PURGE_EXEMPT_PREFIXES

    stmt = select(func.max(EventLog.seq)).where(
        EventLog.tenant_id == tenant_id,
        EventLog.occurred_at < before,
        EventLog.purged_at.is_(None),
    )
    for prefix in PURGE_EXEMPT_PREFIXES:
        stmt = stmt.where(~EventLog.type.like(f"{prefix}%"))
    return (await session.execute(stmt)).scalar_one_or_none() or 0


async def refuse_if_consumers_are_behind(
    session: AsyncSession, *, tenant_id: str, through_seq: int, consumers: list[str]
) -> None:
    """The sharpest check: nothing may be emptied before it has been read.

    A consumer with no cursor row at all counts as behind — it has read nothing,
    which is exactly the case where purging first loses the most.
    """
    rows = (
        await session.execute(
            select(ConsumerCursor.consumer, ConsumerCursor.last_seq).where(
                ConsumerCursor.tenant_id == tenant_id
            )
        )
    ).all()
    seen = {name: last for name, last in rows}

    behind = sorted(
        name for name in consumers if seen.get(name, 0) < through_seq
    )
    if behind:
        raise NotYet(
            f"{len(behind)} consumer(s) have not reached seq {through_seq} for "
            f"{tenant_id!r}: {', '.join(behind)}. Purging now would empty events "
            "they never processed, and the activation would be quietly short of "
            "the data its own report is built from."
        )


async def refuse_if_an_erasure_is_in_flight(
    session: AsyncSession, *, tenant_id: str
) -> None:
    """Both rewrite log rows; only one of them is somebody's legal right."""
    requested = (
        await session.execute(
            select(func.count())
            .select_from(EventLog)
            .where(EventLog.tenant_id == tenant_id, EventLog.type == ERASURE_REQUESTED)
        )
    ).scalar_one()
    completed = (
        await session.execute(
            select(func.count())
            .select_from(EventLog)
            .where(EventLog.tenant_id == tenant_id, EventLog.type == ERASURE_COMPLETED)
        )
    ).scalar_one()

    if requested > completed:
        raise NotYet(
            f"{requested - completed} erasure request(s) are still in flight for "
            f"{tenant_id!r}. Retention waits for Article 17, not the other way round."
        )


async def refuse_if_a_dead_letter_points_into_the_window(
    session: AsyncSession, *, tenant_id: str, through_seq: int
) -> None:
    """A parked event with no payload can never be retried."""
    stuck = (
        await session.execute(
            select(func.count())
            .select_from(DeadLetter)
            .where(
                DeadLetter.tenant_id == tenant_id,
                DeadLetter.event_seq <= through_seq,
                DeadLetter.resolved_at.is_(None),
            )
        )
    ).scalar_one()

    if stuck:
        raise NotYet(
            f"{stuck} unresolved dead letter(s) point at or below seq "
            f"{through_seq} for {tenant_id!r}. Emptying those payloads would "
            "leave rows on /ops that nobody can ever retry."
        )
