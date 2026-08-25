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

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from neo4j import AsyncSession as GraphSession
from sqlalchemy.ext.asyncio import AsyncSession

from app import plans, repository
from app.auth.principal import Principal, require_operator, require_reader, utcnow
from app.db import get_session
from app.graph import repository as graph_repo
from app.graph.driver import get_graph_session
from app.schemas import (
    CameraOut,
    EventIn,
    SessionConfigIn,
    SessionConfigOut,
    SessionGraphOut,
    SessionSummaryOut,
    TouchpointOut,
    ZoneConfig,
    ZoneDwell,
)

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])

#: Announces that a session's zones or measurement parameters changed. In the
#: `session.` namespace of event-bus-spec.md §3, which is additive-only.
ZONES_UPDATED = "session.zones_updated"


def _config_out(
    props: dict,
    zones: list[dict],
    surfaces: list[dict],
    cameras: list[dict],
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
        cameras=[CameraOut(**c) for c in cameras],
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
    # The plan check goes before the first write, not after it. `upsert_session`
    # rewrites the session's measurement parameters, so a save refused halfway
    # would have already changed the numbers the report divides by.
    #
    # `camera_count` and `cameras` are both counted because either alone can
    # carry a booth past its tier: the integer is what the twin and the drift
    # panel read, the list is what zones are keyed on, and a save that sets one
    # and not the other is ordinary.
    declared_cameras = max(
        config.camera_count or 0, len(config.cameras) if config.cameras else 0
    )
    if declared_cameras:
        plans.enforce(
            plan=await plans.plan_for(session, principal.tenant_id),
            limit_name="max_cameras",
            requested=declared_cameras,
            noun="cameras per activation",
            noun_singular="camera per activation",
        )

    # **Only the properties this request actually set.**
    #
    # The handler used to hand over all eighteen on every call, so a partial
    # save nulled everything it did not mention — and two real callers post
    # partial saves. `useCalibration.declare()` sends `{sessionId, cameras}` to
    # add a camera and `assignZone()` sends `{sessionId, zones}`, so declaring a
    # second camera mid-activation erased the activation cost the ROI ratio
    # divides by, the engagement threshold, the attribution model, the client
    # and the dates — with the report still rendering, quietly, without them.
    #
    # `model_fields_set` is what keeps "omitted" and "explicitly null"
    # different: the first leaves the stored value alone, the second clears it,
    # which is how an operator removes a cost they typed by mistake.
    props = {
        field: getattr(config, field)
        for field in config.model_fields_set & set(graph_repo.SESSION_DEFAULTS)
    }
    props = await graph_repo.upsert_session(
        graph,
        tenant_id=principal.tenant_id,
        session_id=config.session_id,
        props=props,
    )

    if config.zones is not None:
        # A zone owned by a camera that will not exist after this save is a
        # zone the tracker will never score a detection against — it collects
        # no dwell and reports as a part of the booth nobody visited, which
        # reads exactly like a finding. `SessionConfigIn` catches it when one
        # request declares both lists; this catches the two cases a schema
        # cannot see: zones posted alone against the stored camera set, and a
        # save whose `cameras` list prunes a camera some zone still names.
        owning = {z.camera_id for z in config.zones if z.camera_id}
        if owning:
            if config.cameras is not None:
                declared = {c.id for c in config.cameras}
            else:
                declared = {
                    c["id"]
                    for c in await graph_repo.cameras_for_session(
                        graph,
                        tenant_id=principal.tenant_id,
                        session_id=config.session_id,
                    )
                }
            unknown = sorted(owning - declared)
            if unknown:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "zones name cameras this session does not have: "
                        f"{', '.join(unknown)}. Declare them in `cameras`, or clear "
                        "`cameraId` to score the zone against every camera."
                    ),
                )

        for zone in config.zones:
            await graph_repo.upsert_zone(
                graph,
                tenant_id=principal.tenant_id,
                session_id=config.session_id,
                zone_id=zone.id,
                name=zone.name,
                type=zone.type,
                polygon=[list(point) for point in zone.polygon] if zone.polygon else None,
                camera_id=zone.camera_id,
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

    if config.cameras is not None:
        # Cameras are written here so `camera_id` means something before the
        # first frame arrives — `drift.detected` and the mask endpoint both key
        # on one, and a camera nobody declared is a typo the operator should
        # find at setup rather than in an empty drift panel.
        #
        # `upsert_camera` deliberately does not touch `privacy_mask`. A save
        # that listed a camera without its mask would otherwise clear it, and
        # the very next frame would reach the model unmasked.
        for camera in config.cameras:
            await graph_repo.upsert_camera(
                graph,
                tenant_id=principal.tenant_id,
                session_id=config.session_id,
                camera_id=camera.id,
                label=camera.label,
            )
        # The mirror of the check in the zones branch, and it catches the case
        # that one cannot: a save that lists cameras but no zones. Pruning
        # `cam-2` would orphan the zone drawn in its frame while the request
        # says nothing about zones at all, so the operator gets no hint that
        # they have just switched off a part of their booth.
        #
        # Read after the zone write above, so what is checked is the zone set
        # this request leaves behind rather than the one it found.
        stored = await graph_repo.zones_for_session(
            graph,
            tenant_id=principal.tenant_id,
            session_id=config.session_id,
            include_undrawn=True,
        )
        declared = {c.id for c in config.cameras}
        orphaned = sorted(
            {
                z["camera_id"]
                for z in stored
                if z.get("camera_id") and z["camera_id"] not in declared
            }
        )
        if orphaned:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "these zones' cameras would be removed by this save: "
                    f"{', '.join(orphaned)}. A zone owned by a camera that does not "
                    "exist is never scored against anything. Reassign those zones "
                    "first, or keep the camera."
                ),
            )

        await graph_repo.prune_cameras(
            graph,
            tenant_id=principal.tenant_id,
            session_id=config.session_id,
            keep_ids=[c.id for c in config.cameras],
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
    cameras = await graph_repo.cameras_for_session(
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

    return _config_out(props, zones, surfaces, cameras)


def _within(started_at: object, floor: dt.datetime) -> bool:
    """Is this activation inside the retention window?

    An **undated** session is kept: it is a configured activation nobody has
    run, and it holds no measurement to retain. So is one whose `started_at`
    does not parse — hiding a client's activation because of a date format is a
    worse failure than showing one a few days past the window.
    """
    if not started_at:
        return True
    try:
        when = dt.datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
    except ValueError:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return when >= floor


@router.get(
    "",
    response_model=list[SessionSummaryOut],
    summary="List this tenant's activations, newest first",
)
async def list_sessions(
    limit: int = Query(50, ge=1, le=200),
    principal: Principal = Depends(require_reader),
    session: AsyncSession = Depends(get_session),
    graph: GraphSession = Depends(get_graph_session),
) -> list[SessionSummaryOut]:
    """Which activations this tenant has run.

    Built for the report's benchmark, which compares an activation to the
    client's own history — `roi-framework.md` §2: *"the most useful benchmark is
    the client's own history"*. Answering that needs the four ROI layers for each
    earlier session, and those are defined once, in the browser. So this returns
    the **listing** and nothing derived: the caller reads each session's log and
    runs the same scorecard it runs for the current one. A visitor count on this
    response would be a second definition of "unique visitor", arriving where
    nobody would think to look for one.

    The tenant comes from the credential, as it does on `GET /events`. There is
    no tenant parameter, so there is nothing to forge, and a caller cannot
    enumerate anyone else's activations.

    Declared **before** `GET /{session_id}`: FastAPI matches routes in order, and
    the parameterised one would otherwise swallow the empty path.
    """
    rows = await graph_repo.sessions_for_tenant(
        graph, tenant_id=principal.tenant_id, limit=limit
    )

    # The plan's retention window. An activation older than it has no readable
    # events (`GET /events` clamps on the same floor), so listing it would hand
    # the benchmark an activation that scores as nothing — indistinguishable
    # from one that genuinely measured nothing, which the benchmark states
    # rather than renders as a zero.
    #
    # Filtered here rather than in Cypher because `started_at` is a string on
    # the node: an unparseable one keeps its session, which is the mild
    # direction to fail on a listing.
    floor = plans.retention_floor(await plans.plan_for(session, principal.tenant_id))
    if floor is not None:
        rows = [row for row in rows if _within(row.get("started_at"), floor)]

    return [
        SessionSummaryOut(
            session_id=row["id"],
            client=row.get("client"),
            campaign=row.get("campaign"),
            venue=row.get("venue"),
            city=row.get("city"),
            started_at=row.get("started_at"),
            ends_at=row.get("ends_at"),
            engaged_threshold_seconds=row.get("engaged_threshold_seconds") or 60.0,
            activation_cost=row.get("activation_cost"),
            currency=row.get("currency") or "USD",
            revenue_influenced=row.get("revenue_influenced"),
            qualified_leads=row.get("qualified_leads"),
        )
        for row in rows
    ]


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
    cameras = await graph_repo.cameras_for_session(
        graph, tenant_id=principal.tenant_id, session_id=session_id
    )
    return _config_out(props, zones, surfaces, cameras)


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
