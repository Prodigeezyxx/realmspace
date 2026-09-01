"""
The two routes a tablet at a touchpoint reaches, and the only ones that take one
of its tokens.

## Why a router of its own, as `routers/share.py` is

The obvious design is a third `Principal.kind` so `POST /v1/events` admits a
tablet unchanged. That is the wrong shape for the reason share.py gives about
`GET /events`, in its sharpest form: `POST /v1/events` takes the type and the
payload from the request, so a tablet holding that credential could append a
`consent.captured`, a `spatial.dwell`, or a hundred thousand of either.

So a token is not a credential. It reaches these two routes, which resolve it to
a `(tenant, session, surface)` triple and take **none of the three from the
request**. Posting as another touchpoint is unwritable rather than caught.

## What one POST is worth

One `surface.touched`, idempotent on the tablet's own `touchId`. No person: a
tablet has no camera, and who was standing there is `consumers/touch.py`'s
question. Nothing is read — `GET /` returns the button's own label and the id it
posts to, which is what the page needs to render and the whole of what a member
of the public standing at the stand may have.

## Everything unknown is a 404

Unknown, expired and revoked are one answer, with one message. `share.py` has
the argument: which of the three it was is the only thing a stranger holding a
guess could act on.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, status
from neo4j import AsyncSession as GraphSession
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository, touch
from app.consumers.ids import derive_event_id
from app.db import get_session, scope_to_tenant
from app.graph import repository as graph_repo
from app.graph.driver import get_graph_session
from app.models import SurfaceToken
from app.schemas import EventIn, EventOut, TabletOut, TouchpointIn

router = APIRouter(prefix="/v1/touch", tags=["touch"])

#: `surface.touched` — the raw fact a tablet can actually attest to. Pinned in
#: `event-bus-spec.md` §3.
TOUCHED = "surface.touched"

#: One message for every failure. See the module docstring.
GONE = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="this touchpoint link is not valid — it may have expired or been revoked",
)


async def _open(token: str, session: AsyncSession) -> SurfaceToken:
    """Resolve a token and scope the connection to its tenant.

    The order is `share._open`'s and matters for its reason: `surface_token` is
    outside RLS because the caller has no tenant until this lookup supplies one,
    so scoping is the first thing that happens after resolving, and the append
    below then runs under the same policy as any other producer's.
    """
    row = await touch.resolve(
        session, token=token, now=dt.datetime.now(dt.timezone.utc)
    )
    if row is None:
        raise GONE
    await scope_to_tenant(session, row.tenant_id)
    return row


@router.get("/{token}", response_model=TabletOut, summary="What this tablet is")
async def tablet(
    token: str,
    session: AsyncSession = Depends(get_session),
    graph: GraphSession = Depends(get_graph_session),
) -> TabletOut:
    """The button's label, read from the operator's own touchpoint config.

    Read from the `Surface` rather than stored on the token, so renaming a
    touchpoint in the wizard renames the button without re-minting anything and
    without an operator walking round the stand with a laptop.
    """
    row = await _open(token, session)

    surface = await graph_repo.surface(
        graph,
        tenant_id=row.tenant_id,
        session_id=row.session_id,
        surface_id=row.surface_id,
    )
    if surface is None:
        # A token for a touchpoint that has since been removed from the
        # activation. Same answer as a bad token, because from the tablet's side
        # they are the same thing: there is nothing here to press.
        raise GONE

    return TabletOut(
        surface_id=row.surface_id,
        label=surface.get("label") or row.surface_id,
        kind=surface.get("type") or "tap",
    )


@router.post(
    "/{token}",
    response_model=EventOut,
    status_code=status.HTTP_201_CREATED,
    summary="Record one interaction with this touchpoint",
)
async def touched(
    token: str,
    body: TouchpointIn,
    session: AsyncSession = Depends(get_session),
) -> EventOut:
    """One tap, appended to the log and nowhere else.

    The same division `POST /v1/consent` makes: recording is unconditional and
    acting on it is a consumer's job, with the bus's retries and dead-lettering
    behind it. A tablet whose graph is unreachable still records the tap.

    **No `anon_id`.** The tablet cannot know it, and inventing one — the person
    the operator thinks is standing there, the last visitor seen — would put a
    fact on an append-only log that nothing measured.
    """
    row = await _open(token, session)
    at = body.at or dt.datetime.now(dt.timezone.utc)

    appended, _ = await repository.append_event(
        session,
        EventIn(
            # Derived from the tablet's own touch id, so its retry over bad
            # venue wifi and this endpoint agree on what "the same tap" means.
            # The surface is in the id too: two tablets that mint the same
            # id are still two taps.
            event_id=derive_event_id(
                "touch", row.tenant_id, row.surface_id, body.touch_id
            ),
            tenant_id=row.tenant_id,
            session_id=row.session_id,
            type=TOUCHED,
            payload={
                "surface_id": row.surface_id,
                "kind": body.kind,
                "at": at.isoformat(),
                "touch_id": body.touch_id,
            },
            occurred_at=at,
        ),
    )

    await touch.note_use(
        session, token_id=row.id, now=dt.datetime.now(dt.timezone.utc)
    )
    await session.commit()
    return EventOut.model_validate(appended)
