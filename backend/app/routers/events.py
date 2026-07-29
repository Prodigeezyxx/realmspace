"""
The two endpoints that make the log usable: one to append, one to read forward.

Producers (perception, booth surfaces, the dashboard) POST here. Consumers that
live outside this process GET here. In-process consumers skip HTTP entirely and
call app.repository directly.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.db import get_session
from app.schemas import EventIn, EventOut

router = APIRouter(prefix="/events", tags=["events"])


@router.post(
    "",
    response_model=EventOut,
    status_code=status.HTTP_201_CREATED,
    summary="Append an event to the log (idempotent on event_id)",
)
async def post_event(
    event: EventIn,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> EventOut:
    """201 if this call created the row, 200 if the event_id was already in the
    log. Either way the body is the row that is in the database — a producer
    retrying after a timeout gets back the same seq it would have gotten the
    first time, so it can't tell whether its first attempt landed, and doesn't
    need to.
    """
    row, created = await repository.append_event(session, event)
    if not created:
        response.status_code = status.HTTP_200_OK
    return EventOut.model_validate(row)


@router.get(
    "",
    response_model=list[EventOut],
    summary="Read the log forward from a cursor",
)
async def get_events(
    tenant_id: str = Query(..., description="required — the log is never read unscoped"),
    since_seq: int = Query(0, ge=0, description="exclusive; returns seq > since_seq"),
    limit: int = Query(100, ge=1, le=repository.MAX_LIMIT),
    session_id: str | None = Query(None),
    type: str | None = Query(None, description="e.g. perception.detection"),
    session: AsyncSession = Depends(get_session),
) -> list[EventOut]:
    rows = await repository.read_events(
        session,
        tenant_id=tenant_id,
        since_seq=since_seq,
        limit=limit,
        session_id=session_id,
        type=type,
    )
    return [EventOut.model_validate(r) for r in rows]
