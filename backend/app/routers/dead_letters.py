"""
The human-in-the-loop queue: events no consumer could process.

event-bus-spec.md §5 has always said a repeatedly-failing event "surfaces in the
HITL review screen for a human" — and the table it surfaces from has existed
since migration 0001, with `resolved_at` and `attempts` ready for exactly this.
What was missing was any way to see it. `list_dead_letters` had no route, so a
parked event was invisible without a database client, and the stated acceptance
— *a forced failure lands in the queue and can be retried* — was unreachable.

## Why retry is not offered for every consumer

Every consumer here is idempotent; that is what makes replay safe. Retrying one
event *out of its sequence* is a narrower question, and the answer differs:

- **graph_writer** — a pure function of the event. Re-running one reproduces
  exactly what the original attempt would have done. Retryable.
- **tracker** — carries per-person position state built from the events before
  the parked one. A detection replayed against empty state yields a confident
  wrong answer, which is worse than the failure. Not retryable; the repair is a
  cursor rewind, which is an operator action rather than a button.
- **broadcast** — stateless, but its output is a push to whoever is connected
  *now*. Re-sending a stale event to today's sockets fixes nothing. Not
  retryable; a missed broadcast is repaired by the client's `since_seq`
  reconnect, which already exists.

Each consumer declares this itself (`Consumer.retryable`) with its reason on the
class, so the answer lives next to the code it is about rather than here.

## Tenant isolation

Nothing in this module filters by tenant, deliberately. `dead_letter` is under
**forced** row-level security and `get_principal` scopes the transaction, so a
row belonging to somebody else is not visible to any query in this file. That
matters more here than almost anywhere: the `error` column holds a traceback,
and a traceback can quote the event payload it choked on.
"""

from __future__ import annotations

import datetime as dt
import traceback

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.auth.principal import Principal, require_operator, require_reader
from app.consumers.base import Consumer
from app.consumers.run import CONSUMER_CLASSES
from app.db import get_session
from app.schemas import _to_camel

router = APIRouter(prefix="/v1/dead-letters", tags=["dead-letters"])


def _consumer_class(name: str) -> type[Consumer] | None:
    """Resolve a consumer by name, or None.

    Deliberately not `run.build()`, which raises **SystemExit** on an unknown
    name. That is right for a CLI and wrong in a request handler: SystemExit
    inherits from BaseException, so FastAPI's error handling does not catch it
    and the caller gets a dropped connection instead of a 404.
    """
    for cls in CONSUMER_CLASSES:
        if cls.name == name:
            return cls
    return None


class DeadLetterOut(BaseModel):
    """A parked failure, with enough of the event to diagnose it."""

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    id: int
    consumer: str
    event_seq: int
    error: str
    attempts: int
    created_at: dt.datetime
    resolved_at: dt.datetime | None

    #: Whether the retry button should be offered at all, and why not when not.
    retryable: bool
    retry_blocked_reason: str | None = None

    #: The event itself. Null when the log no longer holds it — possible if a
    #: cursor was reset far enough back, and worth showing as absent rather than
    #: as an empty object.
    event_type: str | None = None
    event_session_id: str | None = None
    event_payload: dict | None = None
    occurred_at: dt.datetime | None = None


class ActionResult(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    id: int
    resolved: bool
    #: On a failed retry, the new error. The row stays open with its attempt count
    #: bumped, so a human can see the retry was tried and what it hit.
    error: str | None = None


NOT_RETRYABLE = {
    "tracker": (
        "The tracker builds each person's position from the events before this "
        "one, so replaying it alone would produce a different result than the "
        "original attempt. Reprocess the stream from a rewound cursor instead."
    ),
    "broadcast": (
        "Broadcast pushes to whoever is connected now. Re-sending a stale event "
        "would put something out of order on a live screen; clients repair a "
        "missed frame with their own since_seq reconnect."
    ),
}


def _to_out(row, event, cls: type[Consumer] | None) -> DeadLetterOut:
    retryable = bool(cls and cls.retryable)
    return DeadLetterOut(
        id=row.id,
        consumer=row.consumer,
        event_seq=row.event_seq,
        error=row.error,
        attempts=row.attempts,
        created_at=row.created_at,
        resolved_at=row.resolved_at,
        retryable=retryable,
        retry_blocked_reason=(
            None
            if retryable
            else NOT_RETRYABLE.get(
                row.consumer, f"{row.consumer!r} is not registered in this process."
            )
        ),
        event_type=event.type if event else None,
        event_session_id=event.session_id if event else None,
        event_payload=event.payload if event else None,
        occurred_at=event.occurred_at if event else None,
    )


@router.get("", response_model=list[DeadLetterOut], summary="The HITL review queue")
async def list_queue(
    unresolved_only: bool = Query(True),
    consumer: str | None = Query(None),
    principal: Principal = Depends(require_reader),
    session: AsyncSession = Depends(get_session),
) -> list[DeadLetterOut]:
    """What failed, why, and whether anything can be done about it from here."""
    rows = await repository.list_dead_letters(
        session, consumer=consumer, unresolved_only=unresolved_only
    )

    out: list[DeadLetterOut] = []
    for row in rows:
        event = await repository.get_event_by_seq(
            session, tenant_id=principal.tenant_id, seq=row.event_seq
        )
        out.append(_to_out(row, event, _consumer_class(row.consumer)))
    return out


@router.post(
    "/{dead_letter_id}/retry",
    response_model=ActionResult,
    summary="Run the event through its consumer again",
)
async def retry(
    dead_letter_id: int,
    principal: Principal = Depends(require_operator),
    session: AsyncSession = Depends(get_session),
) -> ActionResult:
    """Operator-only: this re-executes a handler with real side effects.

    A failed retry is not an error response. The row stays open with its attempt
    count raised and the newest traceback attached, because "we tried and it
    still does not work" is a result worth recording — the alternative is an
    operator clicking the same button repeatedly with nothing to show for it.
    """
    row = await repository.get_dead_letter(session, id=dead_letter_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    if row.resolved_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="already resolved"
        )

    cls = _consumer_class(row.consumer)
    if cls is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no consumer named {row.consumer!r} in this process",
        )
    if not cls.retryable:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=NOT_RETRYABLE.get(
                row.consumer, f"{row.consumer!r} cannot be retried in isolation"
            ),
        )

    event = await repository.get_event_by_seq(
        session, tenant_id=principal.tenant_id, seq=row.event_seq
    )
    if event is None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=f"event seq={row.event_seq} is no longer in the log",
        )

    try:
        await cls().handle(event)
    except Exception:  # noqa: BLE001 — a failed retry is a result, not a crash
        row.attempts += 1
        row.error = traceback.format_exc()[:2000]
        return ActionResult(id=row.id, resolved=False, error=row.error)

    row.resolved_at = dt.datetime.now(dt.timezone.utc)
    return ActionResult(id=row.id, resolved=True)


@router.post(
    "/{dead_letter_id}/resolve",
    response_model=ActionResult,
    summary="Dismiss without retrying",
)
async def resolve(
    dead_letter_id: int,
    principal: Principal = Depends(require_operator),
    session: AsyncSession = Depends(get_session),
) -> ActionResult:
    """Close a failure a human has dealt with — or decided not to.

    Needed as much as retry: the tracker's failures cannot be retried from here
    at all, so without a dismiss the queue would fill with entries nobody can
    ever clear, and an un-clearable queue stops being read.
    """
    row = await repository.get_dead_letter(session, id=dead_letter_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")

    row.resolved_at = dt.datetime.now(dt.timezone.utc)
    return ActionResult(id=row.id, resolved=True)
