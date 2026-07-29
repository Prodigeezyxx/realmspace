"""
All SQL against the log lives here. The router stays thin: it validates input,
calls one of these, and shapes the response.

Keeping queries out of the router matters more than it looks — the Phase-1
consumers (tracker, graph writer) are not HTTP clients, they're loops in this
same process, and they will call read_events() directly with no router involved.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert 

from app.models import EventLog
from app.schemas import EventIn

# A consumer polling in a tight loop should not be able to ask for the whole log
# in one request.
MAX_LIMIT = 1000


async def append_event(session: AsyncSession, event: EventIn) -> tuple[EventLog, bool]:
    """Append one event to the log, idempotently.

    From docs/event-bus-spec.md §2: "event_id is UNIQUE. Re-inserting a
    duplicate is a no-op." A producer whose POST times out cannot tell whether
    the write landed, so it retries with the same producer-assigned event_id and
    this turns the retry into a no-op instead of a second row.

    A duplicate never modifies the row already there — the log is append-only,
    so history is not rewritten even if a producer resends different data under
    an event_id it already used.

    ON CONFLICT DO NOTHING rather than catching IntegrityError: one round trip
    in the common case, and no half-failed transaction to recover from. Note
    that a skipped insert still consumes a BIGSERIAL value, so seq is ordered
    but not contiguous — consumers must poll `seq > last_seq`, never
    `seq = last_seq + 1`.

    Returns (row, created) where `row` is whatever is now in the database and
    `created` is True only if this call inserted it. seq and recorded_at are
    assigned by Postgres. The caller (get_session) owns the commit.
    """
    stmt = (
        pg_insert(EventLog)
        .values(**event.model_dump())
        .on_conflict_do_nothing(index_elements=["event_id"])
        .returning(EventLog)
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is not None:
        return row, True

    existing = await session.execute(
        select(EventLog).where(EventLog.event_id == event.event_id)
    )
    return existing.scalar_one(), False


async def read_events(
    session: AsyncSession,
    *,
    tenant_id: str,
    since_seq: int = 0,
    limit: int = 100,
    session_id: str | None = None,
    type: str | None = None,
) -> list[EventLog]:
    """Read the log forward from a cursor.

    This is exactly the shape every consumer polls with: "give me everything for
    my tenant after the last seq I handled, in order." Ordering by seq ASC is
    not cosmetic — spec §2 calls seq "the single source of truth for order", and
    a consumer that processed out of order would advance its cursor past events
    it never saw.

    tenant_id is required, not optional. There is deliberately no way to call
    this without scoping to one tenant (multi-tenant.md §2).
    """
    stmt = (
        select(EventLog)
        .where(EventLog.tenant_id == tenant_id, EventLog.seq > since_seq)
        .order_by(EventLog.seq.asc())
        .limit(min(limit, MAX_LIMIT))
    )
    if session_id is not None:
        stmt = stmt.where(EventLog.session_id == session_id)
    if type is not None:
        stmt = stmt.where(EventLog.type == type)

    result = await session.execute(stmt)
    return list(result.scalars().all())
