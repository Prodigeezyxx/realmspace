"""
All SQL against the log lives here. The router stays thin: it validates input,
calls one of these, and shapes the response.

Keeping queries out of the router matters more than it looks — the Phase-1
consumers (tracker, graph writer) are not HTTP clients, they're loops in this
same process, and they will call read_events() directly with no router involved.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models import ConsumerCursor, DeadLetter, EventLog
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


# ── consumer cursors ──────────────────────────────────────────────────────────
#
# event-bus-spec.md §4: a consumer "reads from its cursor, processes
# idempotently, advances cursor, dead-letters on repeated failure". These are
# the cursor half of that contract.


async def get_cursor(
    session: AsyncSession, *, consumer: str, tenant_id: str
) -> int:
    """How far this consumer has read for this tenant. 0 if it has never run.

    Returning 0 rather than raising is deliberate: a brand-new consumer should
    start at the beginning of the log and replay everything, which is exactly
    what spec §5 promises when a consumer is added later.
    """
    result = await session.execute(
        select(ConsumerCursor.last_seq).where(
            ConsumerCursor.consumer == consumer,
            ConsumerCursor.tenant_id == tenant_id,
        )
    )
    return result.scalar_one_or_none() or 0


async def advance_cursor(
    session: AsyncSession, *, consumer: str, tenant_id: str, last_seq: int
) -> None:
    """Move a cursor forward. Upserts, because the first call has no row yet.

    GREATEST() on the update is a guard, not decoration: two loops for the same
    consumer must never be able to drag a cursor backwards and re-deliver events
    that were already handled. Cursors only ever move forward.
    """
    stmt = (
        pg_insert(ConsumerCursor)
        .values(consumer=consumer, tenant_id=tenant_id, last_seq=last_seq)
        .on_conflict_do_update(
            index_elements=["consumer", "tenant_id"],
            set_={
                "last_seq": func.greatest(ConsumerCursor.last_seq, last_seq),
                "updated_at": func.now(),
            },
        )
    )
    await session.execute(stmt)


async def reset_cursor(
    session: AsyncSession, *, consumer: str, tenant_id: str, to_seq: int = 0
) -> None:
    """Rewind a cursor so the consumer replays from there.

    This is the replay mechanism in spec §5 — "a consumer replays by resetting
    consumer_cursor.last_seq". Bypasses the forward-only guard in
    advance_cursor() on purpose; that guard protects against concurrent loops,
    not against a deliberate operator rewind.
    """
    stmt = (
        pg_insert(ConsumerCursor)
        .values(consumer=consumer, tenant_id=tenant_id, last_seq=to_seq)
        .on_conflict_do_update(
            index_elements=["consumer", "tenant_id"],
            set_={"last_seq": to_seq, "updated_at": func.now()},
        )
    )
    await session.execute(stmt)


# ── dead letters ──────────────────────────────────────────────────────────────


async def record_dead_letter(
    session: AsyncSession,
    *,
    consumer: str,
    event_seq: int,
    error: str,
    attempts: int,
) -> DeadLetter:
    """Park an event this consumer could not process.

    The cursor advances past it afterwards. A consumer that retried forever
    would wedge the whole pipeline behind one bad event; spec §5 says it
    surfaces in the HITL review screen for a human instead (roadmap P3).
    """
    row = DeadLetter(
        consumer=consumer, event_seq=event_seq, error=error[:2000], attempts=attempts
    )
    session.add(row)
    await session.flush()
    return row


async def list_dead_letters(
    session: AsyncSession, *, consumer: str | None = None, unresolved_only: bool = True
) -> list[DeadLetter]:
    """What the HITL review screen will read (roadmap P3)."""
    stmt = select(DeadLetter).order_by(DeadLetter.created_at.desc())
    if consumer is not None:
        stmt = stmt.where(DeadLetter.consumer == consumer)
    if unresolved_only:
        stmt = stmt.where(DeadLetter.resolved_at.is_(None))
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ── tenants ───────────────────────────────────────────────────────────────────


async def list_tenants(session: AsyncSession) -> list[str]:
    """Every tenant that has ever produced an event.

    Consumers loop over this to know what to poll. Reading it off the log keeps
    this item from needing a tenants table it would otherwise have to invent —
    an edge kit runs one tenant's activation at a time (multi-tenant.md §2), so
    this is a very short list in practice. A real registry is Phase 6.
    """
    result = await session.execute(
        select(EventLog.tenant_id).distinct().order_by(EventLog.tenant_id)
    )
    return list(result.scalars().all())
