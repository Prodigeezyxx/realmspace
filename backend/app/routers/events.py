"""
The two endpoints that make the log usable: one to append, one to read forward.

Producers (perception, booth surfaces, the dashboard) POST here. Consumers that
live outside this process GET here. In-process consumers skip HTTP entirely and
call app.repository directly.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
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


#: A batch is capped. An uncapped one lets a single request hold a transaction
#: open for as long as it likes, and the producers that want batching most — an
#: RFID bridge, a camera catching up after an outage — are exactly the ones that
#: would send ten thousand at once. They page instead.
MAX_BATCH = 500


class EventBatchIn(BaseModel):
    events: list[EventIn] = Field(min_length=1, max_length=MAX_BATCH)


@router.post(
    "/batch",
    response_model=list[EventOut],
    status_code=status.HTTP_200_OK,
    summary="Append many events in one request (idempotent on each event_id)",
)
@alias_router.post("/v1/events/batch", response_model=list[EventOut])
async def post_events(
    body: EventBatchIn,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> list[EventOut]:
    """One round trip for many events, ported from the `postgres-track`.

    The producers that need this are the ones a per-event POST punishes: an RFID
    bridge reading a bank of antennas, or `perception/bus_client.py` flushing a
    buffer after the wifi came back. At frame rate, a request each is most of the
    work.

    ## All or nothing, deliberately

    One transaction. If any event in the batch is rejected, none of them land.
    That is the opposite of the single-event endpoint's forgiveness, and it is
    the right trade here: a producer that sent 500 and got back "409 of them
    worked" has no reasonable next move, whereas one that got a clean rejection
    can fix the batch and resend. Idempotency on `event_id` makes the resend
    free.

    **Every event's tenant must match the credential**, checked before anything
    is written — the same rule as the single endpoint, and the reason it is
    checked up front is so a mixed-tenant batch cannot half-apply.

    Unlike the other track's version this is authenticated and runs under
    row-level security, so a batch cannot write outside the caller's tenant even
    if this code forgot to look.
    """
    wrong = [e for e in body.events if e.tenant_id != principal.tenant_id]
    if wrong:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"credential is for tenant {principal.tenant_id!r}; "
                f"{len(wrong)} of {len(body.events)} events claim another"
            ),
        )

    # Returned in the order they were sent, so a producer can match results to
    # what it posted without looking at ids.
    out: list[EventOut] = []
    for event in body.events:
        row, _ = await repository.append_event(session, event)
        out.append(EventOut.model_validate(row))
    return out


@router.get(
    "",
    response_model=list[EventOut],
    summary="Read the log forward from a cursor",
)
async def get_events(
    since_seq: int = Query(0, ge=0, description="exclusive; returns seq > since_seq"),
    limit: int = Query(100, ge=1, le=repository.MAX_LIMIT),
    session_id: str | None = Query(None),
    type: list[str] | None = Query(
        None,
        description=(
            "e.g. perception.detection. Repeatable — `?type=spatial.dwell&"
            "type=spatial.zone_enter` returns both, which is how a caller reads "
            "the spatial events without dragging a day of detections behind them."
        ),
    ),
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
        types=type,
    )
    return [EventOut.model_validate(r) for r in rows]
