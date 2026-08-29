"""
The three reads a shared report needs, and the only routes that take a token.

## Why this is a separate router rather than a third kind of Principal

The obvious design is to make a share token authenticate like a user or a
device, so `require_reader` admits it and every existing read endpoint works
unchanged. That is the wrong shape, and the reason is `GET /events`: it takes a
`session_id` query parameter, so a token minted for one activation would read
any other simply by asking. A capability check would have to be remembered on
every reader endpoint that exists and every one added later — and
`routers/events.py` already records what that costs, about a different
parameter: "validation you have to remember to write is validation you will one
day forget."

So a token is not a credential at all. It reaches these three routes, each of
which resolves it to a `(tenant_id, session_id)` pair and takes **neither from
the request**. Reading a second activation is unwritable rather than caught,
which is the shape `docs/adr/003-nl-query-catalogue.md` chose for the same class
of problem when it made a tenant-omitting query impossible to express.

## What a leaked URL exposes

One activation's report, with contact details stripped. `erasure.redact` runs
over every event whose type is in `erasure.PII_TYPES` — the same function and
the same vocabulary the erasure job and the ledger's read-time redaction use, so
this inherits a classification somebody else maintains rather than keeping a
second list that could drift.

Not the live feed, not the twin, not Ask, not the ledger, not `/ops`, and no
write path. The surface is these three functions.

## Everything unknown is a 404

Unknown, expired and revoked are one answer. Telling a stranger which of the
three their guess was would be the only information they could act on.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from neo4j import AsyncSession as GraphSession
from sqlalchemy.ext.asyncio import AsyncSession

from app import erasure, plans, repository, share
from app.db import get_session, scope_to_tenant
from app.graph import repository as graph_repo
from app.graph.driver import get_graph_session
from app.models import ReportShare
from app.routers.sessions import _config_out
from app.schemas import EventOut, SessionConfigOut, SessionGraphOut, ZoneConfig, ZoneDwell

router = APIRouter(prefix="/v1/share", tags=["share"])

#: One message for every failure. See the module docstring.
GONE = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="this link is not valid — it may have expired or been revoked",
)


async def _open(
    token: str, session: AsyncSession, *, count_view: bool = False
) -> ReportShare:
    """Resolve a token, scope the connection to its tenant, and hand back the row.

    The order matters. `report_share` is outside RLS — migration 0013 has the
    argument, and it comes down to the reader having no tenant until this lookup
    supplies one — so the *first* thing after resolving is to scope the session,
    and every read below then runs under the same policy as any other caller's.
    """
    now = dt.datetime.now(dt.timezone.utc)
    row = await share.resolve(session, token=token, now=now)
    if row is None:
        raise GONE

    await scope_to_tenant(session, row.tenant_id)
    if count_view:
        await share.note_view(session, share_id=row.id, now=now)
        await session.commit()
        # The scope is set on the connection, and committing ends the
        # transaction it was set in. Re-declare it before anything reads.
        await scope_to_tenant(session, row.tenant_id)
    return row


@router.get("/{token}", response_model=SessionConfigOut, summary="A shared report's session")
async def shared_config(
    token: str,
    session: AsyncSession = Depends(get_session),
    graph: GraphSession = Depends(get_graph_session),
) -> SessionConfigOut:
    """The activation, as `/report` reads it.

    Counting the view here rather than on the events read: this is the call the
    page makes first and exactly once, where the events endpoint is paged and
    would count one opening several times.
    """
    row = await _open(token, session, count_view=True)

    props = await graph_repo.session_config(
        graph, tenant_id=row.tenant_id, session_id=row.session_id
    )
    if props is None:
        # A link to an activation nobody configured. Same answer as a bad token,
        # because from outside they are the same thing: there is no report here.
        raise GONE

    zones = await graph_repo.zones_for_session(
        graph, tenant_id=row.tenant_id, session_id=row.session_id, include_undrawn=True
    )
    surfaces = await graph_repo.surfaces_for_session(
        graph, tenant_id=row.tenant_id, session_id=row.session_id
    )
    cameras = await graph_repo.cameras_for_session(
        graph, tenant_id=row.tenant_id, session_id=row.session_id
    )
    return _config_out(props, zones, surfaces, cameras)


@router.get("/{token}/events", response_model=list[EventOut], summary="Its events, redacted")
async def shared_events(
    token: str,
    response: Response,
    since_seq: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=repository.MAX_LIMIT),
    type: list[str] | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> list[EventOut]:
    """The log for this activation, with the people taken out.

    **The session is the row's, not the caller's.** There is no `session_id`
    parameter here and that absence is the security property — see the module
    docstring.

    **Redacted by classification, not by field.** Every type in
    `erasure.PII_TYPES` goes through `erasure.redact`, so a payload that grows a
    new contact field is covered by the vocabulary the erasure job already
    maintains. A new PII-carrying event *type* that nobody classifies is the
    failure mode, which is why `tests/test_share.py` asserts over the tuple
    rather than over a list of names.

    **Retention-clamped**, unlike `/v1/handoffs` and `/v1/ledger`. Those two
    refuse the clamp because one is a delivery cursor whose contract is no gaps
    and the other is the audit artifact; a client's copy of a report is neither.
    """
    row = await _open(token, session)

    floor = plans.retention_floor(await plans.plan_for(session, row.tenant_id))
    if floor is not None:
        response.headers["X-Retention-Floor"] = floor.isoformat()

    rows = await repository.read_events(
        session,
        tenant_id=row.tenant_id,
        since_seq=since_seq,
        limit=limit,
        session_id=row.session_id,
        types=type,
        occurred_after=floor,
    )

    out: list[EventOut] = []
    for event in rows:
        model = EventOut.model_validate(event)
        cleaned = erasure.redact(model.type, model.payload or {})
        if cleaned is not None:
            model = model.model_copy(update={"payload": cleaned})
        out.append(model)
    return out


@router.get("/{token}/graph", response_model=SessionGraphOut, summary="Its graph summary")
async def shared_graph(
    token: str,
    session: AsyncSession = Depends(get_session),
    graph: GraphSession = Depends(get_graph_session),
) -> SessionGraphOut:
    """Unique people and dwell per zone — anonymous by construction.

    Nothing here has ever carried a name: the graph's identified half lives on
    `Contact`, and this reads `Person`, `Zone` and their dwell edges.
    """
    row = await _open(token, session)

    zones = await graph_repo.zones_for_session(
        graph, tenant_id=row.tenant_id, session_id=row.session_id, include_undrawn=True
    )
    dwell = await graph_repo.dwell_by_zone(
        graph, tenant_id=row.tenant_id, session_id=row.session_id
    )
    people = await graph_repo.people_in_session(
        graph, tenant_id=row.tenant_id, session_id=row.session_id
    )
    return SessionGraphOut(
        session_id=row.session_id,
        unique_people=people,
        zones=[ZoneConfig(**z) for z in zones],
        dwell_by_zone=[ZoneDwell(**r) for r in dwell],
    )
