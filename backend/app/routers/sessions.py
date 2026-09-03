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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import kiosk, plans, repository, share, touch
from app.auth.principal import Principal, require_operator, require_reader, utcnow
from app.db import get_session
from app.graph import repository as graph_repo
from app.models import ConsentToken, ReportShare, SurfaceToken
from app.graph.driver import get_graph_session
from app.schemas import (
    ShareCreate,
    ShareCreated,
    ShareOut,
    CameraOut,
    EventIn,
    SessionConfigIn,
    SessionConfigOut,
    SessionGraphOut,
    SessionSummaryOut,
    KioskCreate,
    KioskTokenCreated,
    KioskTokenOut,
    TabletCreate,
    TouchpointOut,
    TouchpointTokenCreated,
    TouchpointTokenOut,
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
        consent_copy=props.get("consent_copy"),
        consent_copy_version=props.get("consent_copy_version"),
        consent_tier=props.get("consent_tier") or "T2",
        consent_basis=props.get("consent_basis") or "explicit_optin",
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


# ── Share links ──────────────────────────────────────────────────────────────
#
# Behind `require_operator` rather than `require_admin`: `multi-tenant.md` §3
# puts "runs activations" with Operator, and handing a client their report is
# the last step of running one, not an act of managing the organisation.
#
# The reader half is `routers/share.py`, which no credential reaches. Its
# docstring has the argument for why a token is not a Principal.


def _share_out(row) -> ShareOut:
    """One row, without the token — there is no field on `ShareOut` for it."""
    now = utcnow()
    return ShareOut(
        id=row.id,
        session_id=row.session_id,
        label=row.label,
        hint=row.hint,
        created_by=row.created_by,
        created_at=row.created_at,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        last_viewed_at=row.last_viewed_at,
        view_count=row.view_count,
        # Derived from the two columns that decide it, so a stored `active` can
        # never disagree with them.
        active=row.revoked_at is None and row.expires_at > now,
    )


@router.post(
    "/{session_id}/share",
    response_model=ShareCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Mint a link a client can open without an account",
)
async def create_share(
    session_id: str,
    body: ShareCreate,
    principal: Principal = Depends(require_operator),
    db: AsyncSession = Depends(get_session),
) -> ShareCreated:
    """Returns the token **once**. Nothing can read it back afterwards.

    That is the design and not a gap: the row stores a sha256, so a database
    dump contains no working link, and the operator's copy is the URL now in
    their hand. If they lose it they mint another and revoke this one, which is
    also the correct response to having sent it to the wrong address.
    """
    row, token = await share.create(
        db,
        tenant_id=principal.tenant_id,
        session_id=session_id,
        created_by=principal.subject,
        label=body.label,
        expires_in_days=body.expires_in_days,
        now=utcnow(),
    )
    await db.commit()
    await db.refresh(row)
    return ShareCreated(**_share_out(row).model_dump(), token=token)


@router.get(
    "/{session_id}/shares",
    response_model=list[ShareOut],
    summary="Links minted for this activation",
)
async def list_shares(
    session_id: str,
    principal: Principal = Depends(require_operator),
    db: AsyncSession = Depends(get_session),
) -> list[ShareOut]:
    """Revoked and expired links are listed too.

    "Was this link ever live, and has anybody opened it" is a question about the
    activation, and hiding the dead ones would answer it wrongly by omission.
    """
    rows = (
        await db.execute(
            select(ReportShare)
            .where(
                ReportShare.tenant_id == principal.tenant_id,
                ReportShare.session_id == session_id,
            )
            .order_by(ReportShare.created_at.desc())
        )
    ).scalars().all()
    return [_share_out(row) for row in rows]


@router.delete(
    "/{session_id}/shares/{share_id}",
    response_model=ShareOut,
    summary="Revoke a link",
)
async def revoke_share(
    session_id: str,
    share_id: str,
    principal: Principal = Depends(require_operator),
    db: AsyncSession = Depends(get_session),
) -> ShareOut:
    """Marks it revoked. The row stays.

    `report_share` is outside RLS (migration 0013), so the tenant match here is
    written out rather than enforced by a policy — and `tests/test_share.py`
    asserts that one organisation cannot revoke another's link.
    """
    row = (
        await db.execute(
            select(ReportShare).where(
                ReportShare.id == share_id,
                ReportShare.tenant_id == principal.tenant_id,
                ReportShare.session_id == session_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no share {share_id!r} on session {session_id!r}",
        )

    if row.revoked_at is None:
        row.revoked_at = utcnow()
        await db.commit()
        await db.refresh(row)
    return _share_out(row)


# ── touchpoint tablets ────────────────────────────────────────────────────────
#
# The producer half of `surface.interaction`, which has had every reader since
# Phase 2 and nothing emitting it. A tablet at the touchpoint runs `/touch` and
# posts one `surface.touched` per tap; `consumers/touch.py` decides whether it
# can name who pressed it. These three routes are the operator's side — minting,
# listing and withdrawing. The tablet's side is `routers/touch.py`, which no
# credential reaches.


def _tablet_out(row) -> TouchpointTokenOut:
    """One row, without the token — there is no field on it for one."""
    now = utcnow()
    return TouchpointTokenOut(
        id=row.id,
        session_id=row.session_id,
        surface_id=row.surface_id,
        label=row.label,
        hint=row.hint,
        created_by=row.created_by,
        created_at=row.created_at,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        last_used_at=row.last_used_at,
        use_count=row.use_count,
        active=row.revoked_at is None and row.expires_at > now,
    )


@router.post(
    "/{session_id}/surfaces/{surface_id}/tablet",
    response_model=TouchpointTokenCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Mint a link for a tablet at one touchpoint",
)
async def create_tablet(
    session_id: str,
    surface_id: str,
    body: TabletCreate,
    principal: Principal = Depends(require_operator),
    db: AsyncSession = Depends(get_session),
    graph: GraphSession = Depends(get_graph_session),
) -> TouchpointTokenCreated:
    """Returns the token **once**, like a share link and for its reasons.

    The touchpoint has to exist first. Minting for an unconfigured surface would
    hand back a URL that renders nothing and posts taps `consumers/touch.py`
    then declines — an operator would find that out at the stand, in front of
    the client, which is the failure the report's dead "Export PDF" button
    taught this repo to design against.
    """
    surface = await graph_repo.surface(
        graph,
        tenant_id=principal.tenant_id,
        session_id=session_id,
        surface_id=surface_id,
    )
    if surface is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"no touchpoint {surface_id!r} on activation {session_id!r} — "
                "add it in the session's touchpoints first"
            ),
        )

    row, token = await touch.create(
        db,
        tenant_id=principal.tenant_id,
        session_id=session_id,
        surface_id=surface_id,
        created_by=principal.subject,
        label=body.label,
        expires_in_days=body.expires_in_days,
        now=utcnow(),
    )
    await db.commit()
    await db.refresh(row)
    return TouchpointTokenCreated(**_tablet_out(row).model_dump(), token=token)


@router.get(
    "/{session_id}/tablets",
    response_model=list[TouchpointTokenOut],
    summary="Tablets minted for this activation",
)
async def list_tablets(
    session_id: str,
    principal: Principal = Depends(require_operator),
    db: AsyncSession = Depends(get_session),
) -> list[TouchpointTokenOut]:
    """Revoked and expired ones are listed too, for `list_shares`' reason: "was
    this tablet ever live, and is it still being pressed" is a question about
    the activation, and hiding the dead ones answers it wrongly by omission."""
    rows = await touch.for_session(
        db, tenant_id=principal.tenant_id, session_id=session_id
    )
    return [_tablet_out(row) for row in rows]


@router.delete(
    "/{session_id}/tablets/{token_id}",
    response_model=TouchpointTokenOut,
    summary="Revoke a tablet",
)
async def revoke_tablet(
    session_id: str,
    token_id: str,
    principal: Principal = Depends(require_operator),
    db: AsyncSession = Depends(get_session),
) -> TouchpointTokenOut:
    """Marks it revoked. The row stays, and the taps it already recorded stay
    too — a withdrawn credential does not unmake the measurements it produced.

    `surface_token` is outside RLS (migration 0015), so the tenant match is
    written out rather than enforced by a policy, and there is a test asserting
    one organisation cannot revoke another's tablet.
    """
    row = (
        await db.execute(
            select(SurfaceToken).where(
                SurfaceToken.id == token_id,
                SurfaceToken.tenant_id == principal.tenant_id,
                SurfaceToken.session_id == session_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no tablet {token_id!r} on session {session_id!r}",
        )

    if row.revoked_at is None:
        row.revoked_at = utcnow()
        await db.commit()
        await db.refresh(row)
    return _tablet_out(row)


# ── consent kiosks ────────────────────────────────────────────────────────────
#
# The producer half of `consent.captured`, which has had every reader since
# Phase 4 and whose only producer was `curl`. A phone at a plinth runs
# `/consent` and posts one `consent.given` per visitor;
# `consumers/kiosk_consent.py` decides whether it can name who gave it. These
# three routes are the operator's side — minting, listing and withdrawing. The
# visitor's side is `routers/kiosk.py`, which no credential reaches.


def _kiosk_out(row) -> KioskTokenOut:
    """One kiosk, without the token — there is no field on it for one."""
    now = utcnow()
    return KioskTokenOut(
        id=row.id,
        session_id=row.session_id,
        surface_id=row.surface_id,
        label=row.label,
        hint=row.hint,
        created_by=row.created_by,
        created_at=row.created_at,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        last_used_at=row.last_used_at,
        use_count=row.use_count,
        active=row.revoked_at is None and row.expires_at > now,
    )


@router.post(
    "/{session_id}/surfaces/{surface_id}/kiosk",
    response_model=KioskTokenCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Mint a link for a consent kiosk at one surface",
)
async def create_kiosk(
    session_id: str,
    surface_id: str,
    body: KioskCreate,
    principal: Principal = Depends(require_operator),
    db: AsyncSession = Depends(get_session),
    graph: GraphSession = Depends(get_graph_session),
) -> KioskTokenCreated:
    """Returns the token **once**, like a tablet's and for its reasons.

    **Two refusals, both of them things an operator would otherwise discover at
    the stand.** The surface has to exist, as it does for a tablet. And the
    activation has to have its consent wording set: `copy_version` is the
    load-bearing field of a consent record (`event-bus-spec.md` §3), so a kiosk
    minted without one would hand back a URL that renders the 409 in
    `routers/kiosk.py` to every visitor who scanned it. That is the failure the
    report's dead "Export PDF" button taught this repo to design against — the
    operator finds out in front of the client.
    """
    surface = await graph_repo.surface(
        graph,
        tenant_id=principal.tenant_id,
        session_id=session_id,
        surface_id=surface_id,
    )
    if surface is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"no surface {surface_id!r} on activation {session_id!r} — "
                "add it in the session's touchpoints first"
            ),
        )

    props = await graph_repo.session_config(
        graph, tenant_id=principal.tenant_id, session_id=session_id
    )
    if not (props or {}).get("consent_copy_version") or not (props or {}).get(
        "consent_copy"
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "this activation has no consent wording set. A capture surface "
                "that cannot say which wording it displayed has not really "
                "captured consent — set `consentCopy` and `consentCopyVersion` "
                "on the activation first."
            ),
        )

    row, token = await kiosk.create(
        db,
        tenant_id=principal.tenant_id,
        session_id=session_id,
        surface_id=surface_id,
        created_by=principal.subject,
        label=body.label,
        expires_in_days=body.expires_in_days,
        now=utcnow(),
    )
    await db.commit()
    await db.refresh(row)
    return KioskTokenCreated(**_kiosk_out(row).model_dump(), token=token)


@router.get(
    "/{session_id}/kiosks",
    response_model=list[KioskTokenOut],
    summary="Consent kiosks minted for this activation",
)
async def list_kiosks(
    session_id: str,
    principal: Principal = Depends(require_operator),
    db: AsyncSession = Depends(get_session),
) -> list[KioskTokenOut]:
    """Revoked and expired ones are listed too, for `list_tablets`' reason:
    "was this kiosk ever live, and is it still being scanned" is a question
    about the activation, and hiding the dead ones answers it wrongly by
    omission."""
    rows = await kiosk.for_session(
        db, tenant_id=principal.tenant_id, session_id=session_id
    )
    return [_kiosk_out(row) for row in rows]


@router.delete(
    "/{session_id}/kiosks/{token_id}",
    response_model=KioskTokenOut,
    summary="Revoke a consent kiosk",
)
async def revoke_kiosk(
    session_id: str,
    token_id: str,
    principal: Principal = Depends(require_operator),
    db: AsyncSession = Depends(get_session),
) -> KioskTokenOut:
    """Marks it revoked. The row stays, and so do the consents it recorded — a
    withdrawn credential does not unmake the permissions people gave through it,
    and unmaking them would be the one thing nobody may do on this path.

    `consent_token` is outside RLS (migration 0016), so the tenant match is
    written out rather than enforced by a policy, and there is a test asserting
    one organisation cannot revoke another's kiosk.
    """
    row = (
        await db.execute(
            select(ConsentToken).where(
                ConsentToken.id == token_id,
                ConsentToken.tenant_id == principal.tenant_id,
                ConsentToken.session_id == session_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no kiosk {token_id!r} on session {session_id!r}",
        )

    if row.revoked_at is None:
        row.revoked_at = utcnow()
        await db.commit()
        await db.refresh(row)
    return _kiosk_out(row)
