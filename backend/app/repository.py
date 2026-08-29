"""
All SQL against the log lives here. The router stays thin: it validates input,
calls one of these, and shapes the response.

Keeping queries out of the router matters more than it looks — the Phase-1
consumers (tracker, graph writer) are not HTTP clients, they're loops in this
same process, and they will call read_events() directly with no router involved.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence

from sqlalchemy import BigInteger, delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models import (
    ConsumerCursor,
    CrmLink,
    DeadLetter,
    EventLog,
    Rule,
    RuleDispatch,
    TenantIntegration,
    TenantPurgeWatermark,
)
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
    types: Sequence[str] | None = None,
    occurred_after: dt.datetime | None = None,
) -> list[EventLog]:
    """Read the log forward from a cursor.

    This is exactly the shape every consumer polls with: "give me everything for
    my tenant after the last seq I handled, in order." Ordering by seq ASC is
    not cosmetic — spec §2 calls seq "the single source of truth for order", and
    a consumer that processed out of order would advance its cursor past events
    it never saw.

    tenant_id is required, not optional. There is deliberately no way to call
    this without scoping to one tenant (multi-tenant.md §2).

    `type` and `types` are one filter with two spellings, and the second is not
    convenience. A reader that wants the spatial events and not the detections
    — the report's benchmark, which computes a scorecard over several past
    activations — would otherwise have to pull `perception.detection` too, and
    that is almost the whole log: a day of detections against a few thousand
    spatial events. Passing both narrows to their union rather than to nothing,
    since a row cannot have two types and an AND would return an empty page
    that looks exactly like "no events".

    `occurred_after` is the plan's retention window and is passed by **routers
    only** — see `app/plans.retention_floor`. No consumer passes it, and none
    should: retention is how far back a client may read their own log, not how
    far back this system may process it, and clamping the shape every consumer
    polls with would make the tracker skip events and a replay build a different
    graph. It is a parameter here rather than a filter at the call site because
    dropping rows after the query would silently shorten a page and make `limit`
    and the `seq` cursor disagree.
    """
    stmt = (
        select(EventLog)
        .where(EventLog.tenant_id == tenant_id, EventLog.seq > since_seq)
        .order_by(EventLog.seq.asc())
        .limit(min(limit, MAX_LIMIT))
    )
    if session_id is not None:
        stmt = stmt.where(EventLog.session_id == session_id)
    wanted = {*(types or ()), *([type] if type is not None else ())}
    if wanted:
        stmt = stmt.where(EventLog.type.in_(sorted(wanted)))
    if occurred_after is not None:
        stmt = stmt.where(EventLog.occurred_at >= occurred_after)

    result = await session.execute(stmt)
    return list(result.scalars().all())


async def redact_event(
    session: AsyncSession, *, seq: int, payload: dict, at: dt.datetime
) -> None:
    """Rewrite one event's payload and stamp it erased. The only UPDATE on this
    table, anywhere.

    `EventLog`'s docstring says nothing ever updates a row here, and that is the
    design. GDPR Article 17 is the single thing that outranks it — see
    `app/erasure.py` for what is removed and `app/consumers/erasure.py` for the
    conditions under which it may happen at all.

    `seq`, `event_id`, `type`, `occurred_at` and `recorded_at` are untouched, so
    a replay after an erasure reproduces the same events in the same order with a
    name missing from a few of them. `at` is passed rather than taken from the
    clock because every row in one erasure should carry the same timestamp: the
    question an auditor asks is "what did this request remove", and a spread of
    timestamps across a long redaction makes that a range query for no reason.
    """
    await session.execute(
        update(EventLog)
        .where(EventLog.seq == seq)
        .values(payload=payload, redacted_at=at)
    )


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


#: Never emptied by a purge. These events *are* the record that the purging and
#: the erasing happened, and a purge that ate its own receipts would leave
#: nothing to audit — which is the one thing a retention policy has to be able to
#: prove it did.
PURGE_EXEMPT_PREFIXES = ("erasure.", "retention.")


async def purge_events(
    session: AsyncSession,
    *,
    tenant_id: str,
    before: dt.datetime,
    at: dt.datetime,
) -> tuple[int, int]:
    """Empty the payloads of everything older than `before`. Returns (rows, max seq).

    **The payload goes and the row stays**, which is the whole design. `seq`,
    `event_id`, `type`, `occurred_at` and `recorded_at` are untouched, so derived
    event ids still resolve, `dead_letter.event_seq` still points at something,
    and cursors stay contiguous. What is removed is everything a client's data
    actually *is*.

    **`occurred_at`, not `received_at`** — the same rule `plans.retention_floor`
    states and for the same reason: a batch buffered through an outage and
    replayed a week late should not earn a week of extra retention for having
    arrived late.

    Already-purged rows are skipped, so running it twice is a no-op rather than a
    second receipt claiming work it did not do.
    """
    exempt = [f"{prefix}%" for prefix in PURGE_EXEMPT_PREFIXES]
    conditions = [
        EventLog.tenant_id == tenant_id,
        EventLog.occurred_at < before,
        EventLog.purged_at.is_(None),
    ]
    for pattern in exempt:
        conditions.append(~EventLog.type.like(pattern))

    highest = (
        await session.execute(select(func.max(EventLog.seq)).where(*conditions))
    ).scalar_one_or_none() or 0

    result = await session.execute(
        update(EventLog).where(*conditions).values(payload={}, purged_at=at)
    )
    return result.rowcount or 0, highest


async def purge_watermark(session: AsyncSession, *, tenant_id: str) -> int:
    """How far this tenant has been purged. 0 when never."""
    row = (
        await session.execute(
            select(TenantPurgeWatermark.purged_before_seq).where(
                TenantPurgeWatermark.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    return row or 0


async def set_purge_watermark(
    session: AsyncSession, *, tenant_id: str, seq: int, through: dt.datetime
) -> None:
    """Move the watermark forward. Never backwards.

    A later purge with an earlier floor — which a tier downgrade would produce —
    must not un-forbid a replay of a window whose payloads are already gone.
    """
    stmt = (
        pg_insert(TenantPurgeWatermark)
        .values(tenant_id=tenant_id, purged_before_seq=seq, purged_through=through)
        .on_conflict_do_update(
            index_elements=["tenant_id"],
            set_={
                "purged_before_seq": func.greatest(
                    TenantPurgeWatermark.purged_before_seq, seq
                ),
                "purged_through": func.greatest(
                    TenantPurgeWatermark.purged_through, through
                ),
                "updated_at": func.now(),
            },
        )
    )
    await session.execute(stmt)


class PurgedWindow(RuntimeError):
    """Raised when a rewind would replay events whose payloads are gone."""


async def reset_cursor(
    session: AsyncSession, *, consumer: str, tenant_id: str, to_seq: int = 0
) -> None:
    """Rewind a cursor so the consumer replays from there.

    This is the replay mechanism in spec §5 — "a consumer replays by resetting
    consumer_cursor.last_seq". Bypasses the forward-only guard in
    advance_cursor() on purpose; that guard protects against concurrent loops,
    not against a deliberate operator rewind.

    **It does not bypass the purge watermark.** Retention empties payloads and
    leaves the rows, so a rewind into a purged window would feed consumers empty
    events and rebuild a wrong graph — quietly, because most consumers skip what
    they cannot read. Refusing here makes that replay unwritable rather than
    merely wrong; an operator who means it lifts the watermark deliberately,
    which is the decision they should be making.
    """
    floor = await purge_watermark(session, tenant_id=tenant_id)
    if floor and to_seq < floor:
        raise PurgedWindow(
            f"cannot rewind {consumer!r} to seq {to_seq} for {tenant_id!r}: "
            f"everything at or below seq {floor} has had its payload purged for "
            "retention, so a replay from there would rebuild a wrong graph. "
            "Lift tenant_purge_watermark deliberately if that is what you mean."
        )
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
    tenant_id: str,
    event_seq: int,
    error: str,
    attempts: int,
) -> DeadLetter:
    """Park an event this consumer could not process.

    The cursor advances past it afterwards. A consumer that retried forever
    would wedge the whole pipeline behind one bad event; spec §5 says it
    surfaces in the HITL review screen for a human instead (roadmap P3).

    ## One row per unresolved failure, not one per attempt

    This used to insert unconditionally, which meant the same event parking
    twice — after a cursor rewind, a replay, or a restart — appeared twice in
    the queue. A review screen showing one failure three times is a screen an
    operator learns to ignore, and the whole point of the queue is that somebody
    looks at it.

    So an existing *unresolved* row for the same (tenant, consumer, event) is
    updated: attempts accumulate and the error is refreshed to the most recent
    traceback, which is the one worth reading.

    A **resolved** row is deliberately left alone. If a human fixed something and
    the same event fails again afterwards, that is new information and deserves
    its own entry — silently reopening the old one would erase the fact that it
    had been dealt with once.
    """
    existing = (
        await session.execute(
            select(DeadLetter).where(
                DeadLetter.tenant_id == tenant_id,
                DeadLetter.consumer == consumer,
                DeadLetter.event_seq == event_seq,
                DeadLetter.resolved_at.is_(None),
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.attempts += attempts
        existing.error = error[:2000]
        await session.flush()
        return existing

    row = DeadLetter(
        consumer=consumer,
        tenant_id=tenant_id,
        event_seq=event_seq,
        error=error[:2000],
        attempts=attempts,
    )
    session.add(row)
    await session.flush()
    return row


async def get_dead_letter(session: AsyncSession, *, id: int) -> DeadLetter | None:
    """One parked failure by id.

    No `tenant_id` argument, unusually — and that is safe here rather than an
    oversight. `dead_letter` is under forced row-level security, so a row
    belonging to another tenant is invisible to this transaction whatever id is
    asked for. Adding a filter would be belt-and-braces on a table where the
    braces are enforced by the database; there is a test that proves it.
    """
    return (
        await session.execute(select(DeadLetter).where(DeadLetter.id == id))
    ).scalar_one_or_none()


async def get_event_by_seq(
    session: AsyncSession, *, tenant_id: str, seq: int
) -> EventLog | None:
    """The event a dead letter refers to, so a human can see what failed.

    A review queue listing `seq=418 — KeyError` tells an operator nothing they
    can act on. The type and payload are what make it diagnosable.
    """
    return (
        await session.execute(
            select(EventLog).where(
                EventLog.tenant_id == tenant_id, EventLog.seq == seq
            )
        )
    ).scalar_one_or_none()


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
    this is a very short list in practice.

    **The registry arrived in Phase 6 (migration 0011) and this still reads the
    log, deliberately.** They answer different questions: `tenant` says which
    organisations *exist*, and this says which have *work*. Pointing the consumer
    loop at the registry would make all sixteen consumers poll every organisation
    that ever signed up, forever, including the ones that signed up, looked
    around and never ran an activation — growing the per-poll cost with
    registrations rather than with usage.
    """
    # Via app_tenants(), a SECURITY DEFINER function added in migration 0003.
    # "which tenants exist" is inherently a cross-tenant question, so under
    # row-level security a direct SELECT here returns nothing. That function is
    # the one deliberate, narrow exemption: no arguments, tenant identifiers
    # only, never row data.
    result = await session.execute(text("SELECT tenant_id FROM app_tenants()"))
    return list(result.scalars().all())


async def read_window(
    session: AsyncSession,
    *,
    tenant_id: str,
    session_id: str,
    type: str,
    since: dt.datetime,
    until: dt.datetime,
    before_seq: int,
    limit: int = MAX_LIMIT,
    start_inclusive: bool = False,
    end_inclusive: bool = True,
) -> list[EventLog]:
    """Events of one type inside a window of **event time**, up to a seq bound.

    ## Two window shapes, and why the default is the exclusive one

    The rules evaluator asks for a **sliding** window ending at the event it is
    evaluating: "the last 30 seconds before this". There, an exclusive start is
    right — consecutive evaluations overlap, and an event sitting exactly on a
    boundary would otherwise be counted in both, which is how a threshold of
    five fires on four people.

    The insight agent asks for **fixed, contiguous** windows: `[10:00, 10:10)`,
    then `[10:10, 10:20)`. Both ends move for it. An exclusive start silently
    drops any event landing exactly on a boundary — including, every time, the
    session's very first event, which is where its first window starts. And an
    inclusive end counts an event sitting exactly on `10:10` in *both* windows,
    which is the same double-count from the other direction.

    So the two flags together: `start_inclusive=True, end_inclusive=False` gives
    `[since, until)`, and every event belongs to exactly one window — none lost
    between two, none counted in two.

    This is ADR-002 §1 in one query — the sliding window the rules evaluator uses,
    read from the log rather than held in a dictionary that a restart empties.

    ## Why `before_seq` is not optional

    It is what makes a replay reproduce the original run. The window is a range
    of *occurred_at*, and on a replay the log already contains events that
    happened inside that range but arrived after the event being evaluated. Read
    without a seq bound, a rule evaluated at seq 100 would see events from seq
    150 — the future, relative to the moment being replayed — and fire where the
    original run did not. Bounding by seq restricts the read to what the log
    actually held at that point.

    `since` is exclusive and `until` inclusive, so an event exactly `windowSec`
    old falls outside a window of that length and the boundary is not counted
    twice by two adjacent evaluations.

    Served by `event_log_tenant_session_type_occurred_idx` (migration 0004).
    """
    stmt = (
        select(EventLog)
        .where(
            EventLog.tenant_id == tenant_id,
            EventLog.session_id == session_id,
            EventLog.type == type,
            EventLog.occurred_at >= since
            if start_inclusive
            else EventLog.occurred_at > since,
            EventLog.occurred_at <= until
            if end_inclusive
            else EventLog.occurred_at < until,
            EventLog.seq <= before_seq,
        )
        .order_by(EventLog.seq.asc())
        .limit(min(limit, MAX_LIMIT))
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def last_event_of_type(
    session: AsyncSession,
    *,
    tenant_id: str,
    session_id: str,
    type: str,
    before_seq: int | None = None,
) -> EventLog | None:
    """The most recent event of a type, optionally strictly before a seq.

    The evaluator's `none` condition asks this: "when did anything of this type
    last happen here?" The seq bound is exclusive — the event being evaluated is
    not part of its own history — and it is what keeps a replay from seeing
    events that, at the point being replayed, had not arrived yet.

    ## `None` means no bound, and there is one caller that needs that

    A bound is wrong when the thing being looked for is **always written after
    its own cause**. `consumers/insights.py` asks "what was the last insight for
    this session?" while processing an event the insight was derived from — and
    an insight is appended at a higher seq than every event it summarises, so a
    bound of that event's seq can never see it. The lookup would return None
    forever and the consumer would recompute the first window on every event.

    This is the same trap ADR-002 records for rule cooldowns: *"a cooldown
    ordered on the firing's own seq never applies at all, because a firing is
    always appended after its cause."* There the fix was to order on the carried
    `triggerSeq`; here it is to drop the bound, which is safe because the
    unbounded read only suppresses duplicate work — what an insight *contains*
    is still computed under a seq bound, so a replay reproduces it exactly.
    """
    stmt = (
        select(EventLog)
        .where(
            EventLog.tenant_id == tenant_id,
            EventLog.session_id == session_id,
            EventLog.type == type,
        )
    )
    if before_seq is not None:
        stmt = stmt.where(EventLog.seq < before_seq)
    stmt = (
        stmt.order_by(EventLog.seq.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def last_rule_firing(
    session: AsyncSession,
    *,
    tenant_id: str,
    session_id: str,
    rule_id: str,
    before_trigger_seq: int,
) -> EventLog | None:
    """The last time one rule fired, for the cooldown check.

    ## Why this is keyed on `triggerSeq` rather than on the firing's own seq

    The obvious query — "the last `rule.fired` for this rule at a lower seq" — is
    wrong, and wrong in the direction that makes cooldown do nothing at all. A
    firing is appended *after* the event that caused it, so it always holds the
    higher seq. Evaluating a burst of dwells, every one of them looks for an
    earlier firing, finds that the firing sits ahead of it in the log, and
    concludes the rule has never fired. Cooldown silently never applies, and a
    crowd of five produces five Slack posts instead of one.

    Found by `test_a_replay_produces_the_same_firings`, which expected one firing
    and got six — one per dwell past the threshold.

    What locates a firing in the stream is the event that caused it, which the
    payload carries as `triggerSeq`. Ordering on that puts a firing where its
    cause is, which is where the cooldown question is actually being asked.

    It is also what makes a replay reproduce the original run. Replaying the
    input at seq 5, the firing it produced carries `triggerSeq: 5`, which is not
    *before* seq 5 — so a replayed evaluation does not find its own output and
    suppress itself into producing nothing. The input at seq 6 does find it,
    exactly as in the original run.
    """
    # ->> extracts JSONB as text; the cast is what makes 10 sort after 9.
    trigger_seq = EventLog.payload["triggerSeq"].astext.cast(BigInteger)
    stmt = (
        select(EventLog)
        .where(
            EventLog.tenant_id == tenant_id,
            EventLog.session_id == session_id,
            EventLog.type == "rule.fired",
            EventLog.payload["ruleId"].astext == rule_id,
            trigger_seq < before_trigger_seq,
        )
        .order_by(trigger_seq.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


# ── rules ─────────────────────────────────────────────────────────────────────
#
# ADR-002: a rule is a JSON document, stored per tenant, evaluated by exactly one
# evaluator. These are the document's storage half; app/consumers/rules.py is the
# evaluator and app/routers/rules.py is how an operator authors one.


async def list_rules(
    session: AsyncSession,
    *,
    tenant_id: str,
    trigger_type: str | None = None,
    enabled_only: bool = False,
) -> list[Rule]:
    """Rules for one tenant, optionally narrowed to one trigger type.

    The narrowed form is the evaluator's per-event query and is served by
    `rules_tenant_id_trigger_type_idx`. The wide form is the operator's list.
    Ordered by rule_id so a list endpoint and a preview agree on order.
    """
    stmt = select(Rule).where(Rule.tenant_id == tenant_id).order_by(Rule.rule_id)
    if trigger_type is not None:
        stmt = stmt.where(Rule.trigger_type == trigger_type)
    if enabled_only:
        stmt = stmt.where(Rule.enabled.is_(True))
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def rule_slots(
    session: AsyncSession, *, tenant_id: str, rule_id: str
) -> tuple[int, bool]:
    """`(how many rules this tenant holds, whether rule_id is one of them)`.

    Two scalars rather than `get_rule` plus `list_rules`, and that is not
    micro-optimisation. `upsert_rule` returns the ORM entity from a Core
    `RETURNING`, so loading the same row first puts a **stale** instance in the
    identity map and the upsert hands it back unchanged — an edit that saved
    correctly and reported the old values. Counting touches no entity.

    Used by the plan's agent limit (`app/plans.py`), which needs both: the cap
    applies to a new rule, and PUT is create-or-replace, so editing the second
    of two rules on a two-agent plan must not be refused for being the third.
    """
    row = (
        await session.execute(
            select(
                func.count(Rule.rule_id),
                func.count(Rule.rule_id).filter(Rule.rule_id == rule_id),
            ).where(Rule.tenant_id == tenant_id)
        )
    ).one()
    return int(row[0]), bool(row[1])


async def integration_slots(
    session: AsyncSession, *, tenant_id: str, provider: str
) -> tuple[int, bool]:
    """The same pair for `tenant_integration`, and for the same two reasons —
    `upsert_integration` also returns its entity from a Core RETURNING."""
    row = (
        await session.execute(
            select(
                func.count(TenantIntegration.provider),
                func.count(TenantIntegration.provider).filter(
                    TenantIntegration.provider == provider
                ),
            ).where(TenantIntegration.tenant_id == tenant_id)
        )
    ).one()
    return int(row[0]), bool(row[1])


async def get_rule(
    session: AsyncSession, *, tenant_id: str, rule_id: str
) -> Rule | None:
    """One rule. tenant_id is in the WHERE even though rule_id is the primary
    key: RLS would refuse a cross-tenant read anyway, but a 404 is a better
    answer than an empty result the caller has to interpret."""
    return (
        await session.execute(
            select(Rule).where(Rule.tenant_id == tenant_id, Rule.rule_id == rule_id)
        )
    ).scalar_one_or_none()


async def upsert_rule(
    session: AsyncSession,
    *,
    tenant_id: str,
    rule_id: str,
    name: str,
    trigger_type: str,
    trigger_zone_id: str | None,
    condition: dict,
    action: dict,
    enabled: bool,
    cooldown_sec: int,
) -> Rule:
    """Create or replace one rule document.

    Upsert rather than separate insert/update because the composer UI saves a
    whole document either way — an operator editing a rule is not sending a
    patch, they are sending the rule as it should now read.

    The conflict target is `rule_id` alone, which is the primary key. Adding
    tenant_id to it would not compile, and it is not needed: the WITH CHECK half
    of the RLS policy refuses a write that would land in another tenant, so a
    caller cannot overwrite a rule_id they do not own — they get a policy
    violation rather than someone else's rule.
    """
    values = {
        "rule_id": rule_id,
        "tenant_id": tenant_id,
        "name": name,
        "trigger_type": trigger_type,
        "trigger_zone_id": trigger_zone_id,
        "condition": condition,
        "action": action,
        "enabled": enabled,
        "cooldown_sec": cooldown_sec,
    }
    stmt = (
        pg_insert(Rule)
        .values(**values)
        .on_conflict_do_update(
            index_elements=["rule_id"],
            set_={
                k: v for k, v in values.items() if k not in ("rule_id", "tenant_id")
            }
            | {"updated_at": func.now()},
        )
        .returning(Rule)
    )
    return (await session.execute(stmt)).scalar_one()


async def delete_rule(session: AsyncSession, *, tenant_id: str, rule_id: str) -> bool:
    """Remove a rule. Returns whether there was one to remove.

    A real delete, not a soft one. `enabled: false` is already the way to stop a
    rule firing while keeping it, so a tombstone would be a third state with no
    meaning — and `rule.fired` rows on the bus are the durable record of what a
    rule did, which is what an audit actually needs.
    """
    result = await session.execute(
        delete(Rule).where(Rule.tenant_id == tenant_id, Rule.rule_id == rule_id)
    )
    return result.rowcount > 0


# ── tenant integrations (migration 0007) ──────────────────────────────────────


async def list_integrations(
    session: AsyncSession,
    *,
    tenant_id: str,
    active_only: bool = False,
    kind: str | None = None,
) -> list[TenantIntegration]:
    """Every integration this tenant has configured.

    `active_only` is the delivery consumer's question — "where does a lead go?" —
    and is served by `tenant_integration_active_idx`. The wide form is the admin
    screen, which needs to see a revoked row precisely because it is revoked.

    `kind` (migration 0010) is the other half of that question. The delivery
    consumer asks for `crm` and means it: without the filter, an AI provider key
    stored by an admin would be enrolled as a destination and a lead would be
    offered to it. `None` is the admin screen again, which shows both.
    """
    stmt = (
        select(TenantIntegration)
        .where(TenantIntegration.tenant_id == tenant_id)
        .order_by(TenantIntegration.provider)
    )
    if active_only:
        stmt = stmt.where(TenantIntegration.status == "active")
    if kind is not None:
        stmt = stmt.where(TenantIntegration.kind == kind)
    return list((await session.execute(stmt)).scalars().all())


async def get_integration(
    session: AsyncSession, *, tenant_id: str, provider: str, active_only: bool = False
) -> TenantIntegration | None:
    """One integration, or None."""
    stmt = select(TenantIntegration).where(
        TenantIntegration.tenant_id == tenant_id,
        TenantIntegration.provider == provider,
    )
    if active_only:
        stmt = stmt.where(TenantIntegration.status == "active")
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_integration_of_kind(
    session: AsyncSession,
    *,
    tenant_id: str,
    kind: str,
    active_only: bool = True,
) -> TenantIntegration | None:
    """The one credential of a kind this tenant has, or None.

    `get_integration` asks by provider name, which is the admin screen's
    question. This is the consumer's: *"is there an AI provider configured?"* —
    asked without knowing which, because the caller does not care and must not
    have to keep a list.

    The primary key is `(tenant, provider)`, so a tenant could in principle store
    two of a kind. The first by name wins, deterministically, rather than an
    arbitrary row — an activation whose answers changed provider between two
    questions would be very hard to explain.
    """
    rows = await list_integrations(
        session, tenant_id=tenant_id, active_only=active_only, kind=kind
    )
    return rows[0] if rows else None


async def upsert_integration(
    session: AsyncSession,
    *,
    tenant_id: str,
    provider: str,
    secret_ct: bytes,
    secret_hint: str,
    field_map: dict,
    kind: str = "crm",
) -> TenantIntegration:
    """Store or replace a tenant's credential for one provider.

    The ciphertext arrives already encrypted: this module does not import
    `app/secrets.py`, so there is no path by which a plaintext token reaches a
    SQL statement here even by mistake.

    Re-storing a credential clears `revoked_at` and the previous healthcheck.
    A key that was revoked and is now being replaced is a new credential, and
    carrying the old check forward would show a red status against a token
    nobody has tested yet.
    """
    values = {
        "tenant_id": tenant_id,
        "provider": provider,
        "secret_ct": secret_ct,
        "secret_hint": secret_hint,
        "field_map": field_map,
        "kind": kind,
        "status": "active",
    }
    stmt = (
        pg_insert(TenantIntegration)
        .values(**values)
        .on_conflict_do_update(
            index_elements=["tenant_id", "provider"],
            set_={
                k: v
                for k, v in values.items()
                if k not in ("tenant_id", "provider")
            }
            | {
                "updated_at": func.now(),
                "revoked_at": None,
                "last_check_at": None,
                "last_check_ok": None,
                "last_check_detail": None,
            },
        )
        .returning(TenantIntegration)
    )
    return (await session.execute(stmt)).scalar_one()


async def revoke_integration(
    session: AsyncSession, *, tenant_id: str, provider: str
) -> bool:
    """Stop using a credential, keeping the row. Returns whether one changed.

    Not a DELETE, and not for symmetry with `delete_rule` — the opposite of it.
    A rule's durable record is the `rule.fired` events it produced; a
    credential's is this row, and a `crm_link` naming a provider whose row has
    vanished cannot answer who we were when we pushed that contact.

    The ciphertext stays. Retracting a contact from a CRM after the credential
    was revoked still needs to authenticate to it, and a withdrawal arriving the
    day after an admin disconnects HubSpot is an ordinary sequence.
    """
    result = await session.execute(
        update(TenantIntegration)
        .where(
            TenantIntegration.tenant_id == tenant_id,
            TenantIntegration.provider == provider,
            TenantIntegration.status == "active",
        )
        .values(status="revoked", revoked_at=func.now(), updated_at=func.now())
    )
    return result.rowcount > 0


async def record_integration_check(
    session: AsyncSession,
    *,
    tenant_id: str,
    provider: str,
    ok: bool,
    detail: str,
) -> None:
    """Remember what `healthcheck()` said.

    Stored rather than only returned, so an admin opening the screen tomorrow
    sees that the token expired without having to press test — the failure this
    exists to catch is a credential that goes bad quietly between activations.
    """
    await session.execute(
        update(TenantIntegration)
        .where(
            TenantIntegration.tenant_id == tenant_id,
            TenantIntegration.provider == provider,
        )
        .values(
            last_check_at=func.now(),
            last_check_ok=ok,
            last_check_detail=detail[:2000],
        )
    )


# ── crm links (migration 0008) ────────────────────────────────────────────────


async def record_crm_link(
    session: AsyncSession,
    *,
    tenant_id: str,
    provider: str,
    contact_id: str,
    external_id: str,
    dedupe_key: str,
    session_id: str,
) -> CrmLink:
    """Remember that this contact now exists in this CRM.

    Upsert on `(tenant, provider, contact)`, because the two handoff stages of
    one visitor both push the same person — the same claim the shared
    `dedupe_key` makes at the CRM end, kept on this side too.

    A re-push after a retraction clears `retracted_at`: the contact is in the
    CRM again, and a row that still read "retracted" would tell the next
    withdrawal there was nothing to undo.
    """
    values = {
        "tenant_id": tenant_id,
        "provider": provider,
        "contact_id": contact_id,
        "external_id": external_id,
        "dedupe_key": dedupe_key,
        "session_id": session_id,
    }
    stmt = (
        pg_insert(CrmLink)
        .values(**values)
        .on_conflict_do_update(
            index_elements=["tenant_id", "provider", "contact_id"],
            set_={
                "external_id": external_id,
                "dedupe_key": dedupe_key,
                "session_id": session_id,
                "updated_at": func.now(),
                "retracted_at": None,
                "retract_detail": None,
            },
        )
        .returning(CrmLink)
    )
    return (await session.execute(stmt)).scalar_one()


async def links_for_contact(
    session: AsyncSession,
    *,
    tenant_id: str,
    contact_id: str,
    provider: str | None = None,
    include_retracted: bool = False,
) -> list[CrmLink]:
    """Everywhere this contact was pushed.

    `provider=None` is `crm.retract`'s `destination: "all"` — every destination
    that actually received something, which is the question that event could not
    answer until this table existed.

    Retracted links are excluded by default: a replayed withdrawal should find
    nothing left to do rather than call a CRM again for a record it already
    removed.
    """
    stmt = select(CrmLink).where(
        CrmLink.tenant_id == tenant_id, CrmLink.contact_id == contact_id
    )
    if provider is not None:
        stmt = stmt.where(CrmLink.provider == provider)
    if not include_retracted:
        stmt = stmt.where(CrmLink.retracted_at.is_(None))
    return list((await session.execute(stmt.order_by(CrmLink.provider))).scalars().all())


async def get_crm_link(session: AsyncSession, *, link_id: int) -> CrmLink | None:
    """One link by id, re-read inside the session that will act on it.

    The retract consumer lists links in one session and claims in another, and
    a concurrent attempt can retract a row in between. Re-reading is what keeps
    it from calling a CRM about a record it has already removed.
    """
    return (
        await session.execute(select(CrmLink).where(CrmLink.id == link_id))
    ).scalar_one_or_none()


async def mark_link_retracted(
    session: AsyncSession, *, link_id: int, dedupe_key: str, detail: str
) -> None:
    """Close a link and take the person's name out of it.

    `dedupe_key` arrives already redacted — this module does not decide what
    redaction means, `attribution/ledger.py` does, and there is exactly one
    definition of it.
    """
    await session.execute(
        update(CrmLink)
        .where(CrmLink.id == link_id)
        .values(
            retracted_at=func.now(),
            retract_detail=detail[:2000],
            dedupe_key=dedupe_key,
            updated_at=func.now(),
        )
    )


# ── dispatches ────────────────────────────────────────────────────────────────


async def claim_dispatch(
    session: AsyncSession,
    *,
    tenant_id: str,
    fired_event_id: uuid.UUID,
    rule_id: str,
    action_type: str,
    kind: str = "rule",
) -> RuleDispatch | None:
    """Take exclusive ownership of carrying out one action, or return None.

    This is the whole of ADR-002 §3's "a retry after a timeout cannot
    double-post", and it has to be a database write rather than a check: posting
    to Slack is not idempotent, and no amount of care on this side makes it so.
    The UNIQUE on (fired_event_id, action_type) is what decides.

    ## Three existing states, three different answers

    The first version was `ON CONFLICT DO NOTHING`, which reads well and is
    wrong. It treats every existing row as "somebody else has this", including
    the row a *failed* attempt just wrote — so the dispatcher's own second
    attempt returned quietly, the exception never came back, and
    `base.Consumer` recorded a success. A Slack outage silently produced no
    message and no dead letter. (`test_a_failing_slack_post_lands_in_the_dead_letter_queue`.)

    So the conflict is resolved on the existing `status`:

    - **`delivered`** — refuse. This is the double-post case the table exists
      for, and the one a replay hits.
    - **`failed`** — take it back and try again, counting the attempt. Nothing
      was delivered, so there is nothing to duplicate.
    - **`claimed`** — refuse. A process died between the claim and the outcome,
      and whether the message arrived is genuinely unknown. Retrying would risk
      the double-post; the honest state is the one `complete_dispatch` describes,
      and it needs a human rather than a guess.

    A `DO UPDATE … WHERE` whose condition fails returns no row, so all three
    answers come out of one statement with no read-then-write race between them.
    """
    stmt = (
        pg_insert(RuleDispatch)
        .values(
            tenant_id=tenant_id,
            fired_event_id=fired_event_id,
            rule_id=rule_id,
            action_type=action_type,
            kind=kind,
            status="claimed",
            attempts=1,
        )
        .on_conflict_do_update(
            index_elements=["fired_event_id", "action_type"],
            set_={
                "status": "claimed",
                "attempts": RuleDispatch.attempts + 1,
                "completed_at": None,
            },
            where=RuleDispatch.status == "failed",
        )
        .returning(RuleDispatch)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def complete_dispatch(
    session: AsyncSession,
    *,
    dispatch_id: int,
    status: str,
    detail: str | None = None,
) -> None:
    """Record how a claimed dispatch ended: `delivered` or `failed`.

    A row left at `claimed` is not a bug to clean up — it is a dispatch whose
    process died between the claim and the outcome, and the honest reading is
    "we do not know whether Slack got it". Leaving it says so; overwriting it
    with `failed` on restart would be a guess, and a retry on that guess is the
    double-post this table exists to prevent.
    """
    await session.execute(
        update(RuleDispatch)
        .where(RuleDispatch.id == dispatch_id)
        .values(status=status, detail=detail, completed_at=func.now())
    )


async def get_dispatch(
    session: AsyncSession, *, tenant_id: str, fired_event_id: uuid.UUID, action_type: str
) -> RuleDispatch | None:
    """The dispatch for one firing and action, if it exists. For `/ops` and for
    tests asking whether an action ran exactly once."""
    return (
        await session.execute(
            select(RuleDispatch).where(
                RuleDispatch.tenant_id == tenant_id,
                RuleDispatch.fired_event_id == fired_event_id,
                RuleDispatch.action_type == action_type,
            )
        )
    ).scalar_one_or_none()


async def get_dispatch_by_id(
    session: AsyncSession, *, dispatch_id: int
) -> RuleDispatch | None:
    """One dispatch by primary key. No tenant filter, and none is needed —
    `rule_dispatch` is under forced RLS, so a row belonging to another tenant is
    not visible to this query at all."""
    return (
        await session.execute(
            select(RuleDispatch).where(RuleDispatch.id == dispatch_id)
        )
    ).scalar_one_or_none()


async def list_stranded_dispatches(
    session: AsyncSession, *, older_than_seconds: float, limit: int = 200
) -> list[RuleDispatch]:
    """Dispatches stuck at `claimed`, oldest first.

    `claim_dispatch` refuses these and explains why: nobody can say whether the
    message arrived, so the row is left alone rather than guessed at. That is the
    right call and it is not an end state — without a human it blocks its
    `(fired_event_id, action_type)` pair forever.

    ## Why there is an age cutoff

    A row is at `claimed` for the whole duration of every normal dispatch — the
    claim is committed *before* the call goes out, which is the entire mechanism.
    Listing every `claimed` row would put each Slack post currently in flight on
    the operator's screen as a problem to solve, and a queue that is mostly false
    alarms is a queue that stops being read.

    `older_than_seconds` is therefore not a tuning knob but a definition: longer
    than any dispatch could legitimately still be running (see
    `settings.stranded_dispatch_after_seconds`, which derives it from the action
    timeout and the retry schedule).
    """
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=older_than_seconds)
    return list(
        (
            await session.execute(
                select(RuleDispatch)
                .where(
                    RuleDispatch.status == "claimed",
                    RuleDispatch.created_at < cutoff,
                )
                .order_by(RuleDispatch.created_at)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )


async def resolve_dispatch(
    session: AsyncSession,
    *,
    dispatch_id: int,
    status: str,
    resolved_by: str,
    detail: str | None = None,
) -> None:
    """Record a human's answer to "did the message actually arrive?".

    `status` is `delivered` or `failed`, and the two mean different things to the
    rest of the system:

    - **`delivered`** — closed. `claim_dispatch` already refuses a `delivered`
      row, so the pair stays protected against a replay double-posting.
    - **`failed`** — releases it. `claim_dispatch` takes a `failed` row back,
      because nothing was delivered and there is nothing to duplicate.

    Note what `failed` does *not* do: it does not re-send anything by itself. The
    dispatcher's cursor is long past that firing, so a redelivery needs the
    firing replayed — a cursor rewind, which is deliberately an operator action
    and not a button, for the same reason `DispatchConsumer.retryable` is False.
    Marking it `failed` removes the block; it does not decide to act again.

    `resolved_by` is written even though `status` and `completed_at` would carry
    the outcome on their own, because a human's "I checked the channel" and the
    dispatcher's 200 from Slack are different evidence for the same word.
    """
    await session.execute(
        update(RuleDispatch)
        .where(RuleDispatch.id == dispatch_id)
        .values(
            status=status,
            detail=detail,
            completed_at=func.now(),
            resolved_by=resolved_by,
            resolved_at=func.now(),
        )
    )
