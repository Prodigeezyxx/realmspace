"""
The two endpoints that make the log usable: one to append, one to read forward.

Producers (perception, booth surfaces, the dashboard) POST here. Consumers that
live outside this process GET here. In-process consumers skip HTTP entirely and
call app.repository directly.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.auth.principal import Principal, get_principal, require_reader
from app.db import get_session
from app.schemas import EventIn, EventOut

router = APIRouter(prefix="/events", tags=["events"])


#: The `postgres-track` exposes this endpoint at `/v1/events`. Aliasing it here
#: means one producer, pointed at one `--bus-url`, works against either backend
#: — `perception/realmspace.py` is a file both tracks share, and it should not
#: need to know which one it is talking to. Hidden from the schema so `/events`
#: stays the single documented path.
alias_router = APIRouter(include_in_schema=False)


@router.post(
    "",
    response_model=EventOut,
    status_code=status.HTTP_201_CREATED,
    summary="Append an event to the log (idempotent on event_id)",
)
@alias_router.post("/v1/events", response_model=EventOut, status_code=status.HTTP_201_CREATED)
async def post_event(
    event: EventIn,
    response: Response,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> EventOut:
    """201 if this call created the row, 200 if the event_id was already in the
    log. Either way the body is the row that is in the database — a producer
    retrying after a timeout gets back the same seq it would have gotten the
    first time, so it can't tell whether its first attempt landed, and doesn't
    need to.

    The body carries a `tenant_id`, and it must agree with the credential.
    Rejecting rather than silently rewriting it to the verified value: a
    producer configured for the wrong tenant should find out immediately, not
    have its events quietly re-homed and discover the gap in a report weeks
    later.
    """
    if event.tenant_id != principal.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"credential is for tenant {principal.tenant_id!r}, "
                f"event claims {event.tenant_id!r}"
            ),
        )

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
    since_seq: int = Query(0, ge=0, description="exclusive; returns seq > since_seq"),
    limit: int = Query(100, ge=1, le=repository.MAX_LIMIT),
    session_id: str | None = Query(None),
    type: str | None = Query(None, description="e.g. perception.detection"),
    principal: Principal = Depends(require_reader),
    session: AsyncSession = Depends(get_session),
) -> list[EventOut]:
    """Read your own tenant's log. There is no way to read anyone else's.

    **The `tenant_id` query parameter is gone.** That deletion is the fix, not
    an incidental tidy-up: while a caller could name the tenant, validating the
    name was the only thing standing between them and someone else's data, and
    validation you have to remember to write is validation you will one day
    forget. Now the tenant comes from the signed credential and there is no
    parameter left to forge.

    Device credentials are refused here — see `Principal.may_read`.
    """
    rows = await repository.read_events(
        session,
        tenant_id=principal.tenant_id,
        since_seq=since_seq,
        limit=limit,
        session_id=session_id,
        type=type,
    )
    return [EventOut.model_validate(r) for r in rows]
