"""
Session configuration — the zones an activation is measured against, and the
parameters its ROI is computed from.

## Why this endpoint had to exist before Phase 2 could start

The tracker reads its zone polygons from the graph (`consumers/tracker.py` →
`zones_for_session`). Nothing wrote them. `upsert_session` and `upsert_zone`
existed in `graph/repository.py` from the start, but the only callers in the
repo were tests — so on a fresh system the tracker fetched an empty list,
returned early, and emitted no `spatial.*` events at all. No zone enters, no
dwell, no graph edges, and therefore no metric in `roi-framework.md` with
anything behind it. Every Phase 2 number is downstream of this handler.

## Why the measurement parameters live here and not in the report

roi-framework.md §5: "the strongest ROI comes from planning it into the
activation, not bolting it on" — the zone weights, the engagement threshold, the
cost and the attribution model are set *before* doors open, with the client, so
the resulting number is pre-agreed rather than argued about afterwards. A
parameter the report chose for itself would be a parameter chosen to flatter the
result. These are stored on the Session node at setup time and only read later.

## Zone edits mid-session

The tracker caches polygons for `tracker_zone_cache_seconds` (30s by default),
because a graph round trip per detection at 20fps would be absurd. That cache
has to be dropped when an operator redraws a zone, or dwell is attributed to the
old shape until it expires. A write here appends `session.zones_updated` to the
bus and the tracker invalidates on reading it — which is what the TODO in
`tracker.zones_for` asked for ("no event in the §3 taxonomy announces a zone
edit; when one exists, subscribe to it").

Going through the bus rather than calling the consumer directly is what makes it
work at all in the deployment that matters: `python -m app.consumers.run tracker`
runs the tracker in its own process, where an in-process method call reaches
nothing.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from neo4j import AsyncSession as GraphSession
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.auth.principal import Principal, require_operator, require_reader, utcnow
from app.db import get_session
from app.graph import repository as graph_repo
from app.graph.driver import get_graph_session
from app.schemas import (
    EventIn,
    SessionConfigIn,
    SessionConfigOut,
    SessionGraphOut,
    TouchpointOut,
    ZoneConfig,
    ZoneDwell,
)

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])

#: Announces that a session's zones or measurement parameters changed. In the
#: `session.` namespace of event-bus-spec.md §3, which is additive-only.
ZONES_UPDATED = "session.zones_updated"


def _config_out(
    props: dict, zones: list[dict], surfaces: list[dict]
) -> SessionConfigOut:
    """A Session node's properties plus its zones, as the wire shape.

    Field-by-field rather than `SessionConfigOut(**props)`: the graph is
    schema-optional, so a node written by an older version of this code — or by
    hand during a demo — can carry properties this model has never heard of, and
    splatting them in raises a validation error on a session that is otherwise
    perfectly readable. Named fields degrade to the defaults instead.
    """
    return SessionConfigOut(
        session_id=props["id"],
        client=props.get("client"),
        campaign=props.get("campaign"),
        venue=props.get("venue"),
        city=props.get("city"),
        started_at=props.get("started_at"),
        ends_at=props.get("ends_at"),
        booth_width_m=props.get("booth_width_m"),
        booth_depth_m=props.get("booth_depth_m"),
        camera_count=props.get("camera_count"),
        engaged_threshold_seconds=props.get("engaged_threshold_seconds") or 60.0,
        activation_cost=props.get("activation_cost"),
        currency=props.get("currency") or "USD",
        attribution_model=props.get("attribution_model") or "influenced",
        attribution_window_days=props.get("attribution_window_days") or 90,
        revenue_influenced=props.get("revenue_influenced"),
        qualified_leads=props.get("qualified_leads"),
        anonymous_handoffs=bool(props.get("anonymous_handoffs")),
        insight_interval_minutes=props.get("insight_interval_minutes") or 10,
        zones=[ZoneConfig(**z) for z in zones],
        touchpoints=[TouchpointOut(**s) for s in surfaces],
    )


@router.post(
    "",
    response_model=SessionConfigOut,
    status_code=status.HTTP_200_OK,
    summary="Create or update a session's zones and measurement parameters",
)
async def put_session_config(
    config: SessionConfigIn,
    principal: Principal = Depends(require_operator),
    session: AsyncSession = Depends(get_session),
    graph: GraphSession = Depends(get_graph_session),
) -> SessionConfigOut:
    """Write the session and its zones, then tell the tracker to re-read them.

    The tenant comes from the credential, as everywhere else — there is no
    `tenant_id` in the body to disagree with it and therefore nothing to forge.

    Ordering is deliberate: graph first, bus event second. The other way round,
    the tracker could invalidate its cache and re-fetch the *old* polygons
    before the write landed, and then hold them for another full TTL — the exact
    staleness the event exists to prevent. This ordering's failure mode is the
    milder one: if the append fails, the zones are correct and the tracker picks
    them up when the TTL expires.
    """
    props = await graph_repo.upsert_session(
        graph,
        tenant_id=principal.tenant_id,
        session_id=config.session_id,
        client=config.client,
        campaign=config.campaign,
        venue=config.venue,
        city=config.city,
        started_at=config.started_at,
        ends_at=config.ends_at,
        booth_width_m=config.booth_width_m,
        booth_depth_m=config.booth_depth_m,
        camera_count=config.camera_count,
        engaged_threshold_seconds=config.engaged_threshold_seconds,
        activation_cost=config.activation_cost,
        currency=config.currency,
        attribution_model=config.attribution_model,
        attribution_window_days=config.attribution_window_days,
        revenue_influenced=config.revenue_influenced,
        qualified_leads=config.qualified_leads,
        anonymous_handoffs=config.anonymous_handoffs,
        insight_interval_minutes=config.insight_interval_minutes,
    )

    if config.zones is not None:
        for zone in config.zones:
            await graph_repo.upsert_zone(
                graph,
                tenant_id=principal.tenant_id,
                session_id=config.session_id,
                zone_id=zone.id,
                name=zone.name,
                type=zone.type,
                polygon=[list(point) for point in zone.polygon] if zone.polygon else None,
                color=zone.color,
                capacity=zone.capacity,
                weight=zone.weight,
                funnel_order=zone.funnel_order,
            )
        await graph_repo.prune_zones(
            graph,
            tenant_id=principal.tenant_id,
            session_id=config.session_id,
            keep_ids=[z.id for z in config.zones],
        )

    if config.touchpoints is not None:
        # Surfaces are written here for the same reason zones are: the graph
        # writer refuses an interaction for a surface nobody configured, so
        # without this every touchpoint reading would be dropped on arrival.
        for touchpoint in config.touchpoints:
            await graph_repo.upsert_surface(
                graph,
                tenant_id=principal.tenant_id,
                session_id=config.session_id,
                surface_id=touchpoint.id,
                label=touchpoint.label,
                type=touchpoint.type,
                zone_id=touchpoint.zone_id,
                active=touchpoint.active,
            )
        await graph_repo.prune_surfaces(
            graph,
            tenant_id=principal.tenant_id,
            session_id=config.session_id,
            keep_ids=[t.id for t in config.touchpoints],
        )

    zones = await graph_repo.zones_for_session(
        graph,
        tenant_id=principal.tenant_id,
        session_id=config.session_id,
        include_undrawn=True,
    )
    surfaces = await graph_repo.surfaces_for_session(
        graph, tenant_id=principal.tenant_id, session_id=config.session_id
    )

    await repository.append_event(
        session,
        EventIn(
            # Random, not derived: this is a producer writing about something
            # that genuinely just happened. Two edits to the same zones are two
            # events, and collapsing them onto one id would lose the second.
            event_id=uuid.uuid4(),
            tenant_id=principal.tenant_id,
            session_id=config.session_id,
            type=ZONES_UPDATED,
            payload={
                "zone_ids": [z["id"] for z in zones],
                "zone_count": len(zones),
                "by": principal.subject,
            },
            occurred_at=utcnow(),
        ),
    )

    return _config_out(props, zones, surfaces)


@router.get(
    "/{session_id}",
    response_model=SessionConfigOut,
    summary="Read a session's configuration",
)
async def get_session_config(
    session_id: str,
    principal: Principal = Depends(require_reader),
    graph: GraphSession = Depends(get_graph_session),
) -> SessionConfigOut:
    """404 if the session was never configured.

    Distinguishing that from "configured but empty" matters downstream: a report
    that cannot tell them apart will render zeroes for a session nobody set up
    and present them as findings.
    """
    props = await graph_repo.session_config(
        graph, tenant_id=principal.tenant_id, session_id=session_id
    )
    if props is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"session {session_id!r} has no configuration",
        )

    zones = await graph_repo.zones_for_session(
        graph,
        tenant_id=principal.tenant_id,
        session_id=session_id,
        include_undrawn=True,
    )
    surfaces = await graph_repo.surfaces_for_session(
        graph, tenant_id=principal.tenant_id, session_id=session_id
    )
    return _config_out(props, zones, surfaces)


@router.get(
    "/{session_id}/graph",
    response_model=SessionGraphOut,
    summary="Graph-derived aggregates for a session",
)
async def get_session_graph(
    session_id: str,
    principal: Principal = Depends(require_reader),
    graph: GraphSession = Depends(get_graph_session),
) -> SessionGraphOut:
    """Unique people and dwell per zone — what the graph knows better than the log.

    Not a general metrics endpoint. The rest of the scorecard is computed from
    the event log the caller can already read at `GET /events`, by
    `dashboard/src/lib/roi/scorecard.ts`, which already implements all four
    layers of roi-framework.md §2. Re-deriving those formulas here in a second
    language would give us two definitions of "engagement rate" and no way to
    notice when they stopped agreeing.

    No 404: a session with no configuration and no people is a legitimate
    all-zeroes answer here, and a live tile polling this during setup should get
    an empty scorecard rather than an error.
    """
    zones = await graph_repo.zones_for_session(
        graph, tenant_id=principal.tenant_id, session_id=session_id, include_undrawn=True
    )
    dwell = await graph_repo.dwell_by_zone(
        graph, tenant_id=principal.tenant_id, session_id=session_id
    )
    people = await graph_repo.people_in_session(
        graph, tenant_id=principal.tenant_id, session_id=session_id
    )

    return SessionGraphOut(
        session_id=session_id,
        unique_people=people,
        zones=[ZoneConfig(**z) for z in zones],
        dwell_by_zone=[ZoneDwell(**row) for row in dwell],
    )
