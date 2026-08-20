"""
Camera calibration — and the one kind of it this system can honestly perform.

## The promise this file exists to keep

`privacy.md` §"Sensitive zones" has said since Phase 0 that an operator can draw
an opt-out polygon "on the calibration step" and that "pixels within that polygon
are masked before any model runs". The session wizard repeats it to the operator
during setup: *"All sensitive zones masked at pixel level."*

Nothing did it. There was no calibration step, no mask, and no code anywhere in
either track that touched a pixel before YOLO saw it. This endpoint and
`perception/mask.py` are that sentence, finally in code.

## Why three of the four kinds are refused

`event-bus-spec.md` §3 pins `calibration.updated` with
`kind: "homography" | "zone_map" | "reader_map" | "privacy_mask"`. Only the last
has a consumer:

- **`zone_map`** already has an endpoint. `POST /v1/sessions` writes zone
  geometry and appends `session.zones_updated`, which the tracker subscribes to.
  A second path writing the same geometry would give the system two ideas of
  what the current zones are, and the tracker would invalidate its cache on one
  of them.
- **`homography`** has no consumer. Zones are normalized image coordinates from
  end to end — `consumers/zones.py` divides detections by the frame size and
  compares — and nothing anywhere reads a pixel-to-floor-plan transform.
  Accepting one would put an event in the log that reads as a calibration
  somebody performed and nothing applied.
- **`reader_map`** maps RFID readers to zones. `rfid.read` is a registered
  namespace with no producer, so the map would describe hardware that never
  reports.

Each is refused *with its reason*, which is the same choice `/ops` makes when it
declines to retry a tracker dead-letter: a button that accepts the request and
does nothing is worse than one that explains itself. When a consumer for any of
them lands, the refusal becomes an implementation, and the shape of this file
does not change.

## Why the event id is random

The exception `event-bus-spec.md` §3 states explicitly, and the only place in
this codebase where a *derived* id would be the bug. Two recalibrations of one
camera are two distinct facts. An id derived from `camera_id` collapses them, the
bus dedupes the second as a replay, and the log is left claiming a mask was
applied at a time it was not — a false record in the one event that exists to be
an audit trail for masking.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from neo4j import AsyncSession as GraphSession
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.auth.principal import (
    Principal,
    require_mask_reader,
    require_operator,
    utcnow,
)
from app.db import get_session
from app.graph import repository as graph_repo
from app.graph.driver import get_graph_session
from app.schemas import CalibrationIn, CalibrationOut, EventIn, MaskOut

router = APIRouter(prefix="/v1/sessions", tags=["calibration"])

#: event-bus-spec.md §3. Producer: the calibration UI (an operator).
CALIBRATION_UPDATED = "calibration.updated"

#: Why each unsupported kind is refused, and what to do instead. Kept as data so
#: the refusal and the docstring above cannot drift apart, and so adding a
#: consumer means deleting one entry rather than hunting for a branch.
UNSUPPORTED: dict[str, tuple[int, str]] = {
    "zone_map": (
        status.HTTP_409_CONFLICT,
        "zone geometry is written by POST /v1/sessions, which appends "
        "session.zones_updated and invalidates the tracker's polygon cache. "
        "Two writers would give the system two ideas of what the zones are.",
    ),
    "homography": (
        status.HTTP_501_NOT_IMPLEMENTED,
        "nothing reads a homography: zones are normalized image coordinates "
        "end to end (consumers/zones.py). Accepting one would record a "
        "calibration that was never applied.",
    ),
    "reader_map": (
        status.HTTP_501_NOT_IMPLEMENTED,
        "rfid.read is a registered namespace with no producer, so a reader map "
        "would describe hardware that never reports.",
    ),
}


@router.post(
    "/{session_id}/cameras/{camera_id}/calibration",
    response_model=CalibrationOut,
    summary="Recalibrate one camera (privacy mask)",
)
async def post_calibration(
    session_id: str,
    camera_id: str,
    body: CalibrationIn,
    principal: Principal = Depends(require_operator),
    session: AsyncSession = Depends(get_session),
    graph: GraphSession = Depends(get_graph_session),
) -> CalibrationOut:
    """Set or clear a camera's opt-out polygon, and record that it changed.

    `run_activation` rather than `manage_org`: recalibrating is what an operator
    does when somebody knocks a tripod, and it has to be possible on the floor
    without waking an admin. A device key cannot reach here at all — masking is
    a decision about the room, and the camera is the thing being decided about.

    Graph first, bus second, for the same reason `POST /v1/sessions` orders it
    that way: an edge box that learns of a new revision before the new polygon
    has landed would fetch the old one and believe it current. This ordering's
    failure mode is milder — the mask is applied and the box picks it up on its
    next poll instead of immediately.
    """
    if body.kind in UNSUPPORTED:
        code, why = UNSUPPORTED[body.kind]
        raise HTTPException(status_code=code, detail=f"{body.kind}: {why}")
    if body.kind != "privacy_mask":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"unknown calibration kind {body.kind!r}; "
                "event-bus-spec.md §3 pins homography, zone_map, reader_map, "
                "privacy_mask"
            ),
        )

    existing = await graph_repo.camera_mask(
        graph,
        tenant_id=principal.tenant_id,
        session_id=session_id,
        camera_id=camera_id,
    )
    if existing is None:
        # A mask for a camera nobody declared would be a mask nothing fetches:
        # perception asks by the id it was started with, and a typo here would
        # produce a polygon sitting in the graph while the booth ran unmasked.
        # The wizard declares cameras; this refuses to invent one.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"camera {camera_id!r} is not declared on session {session_id!r} — "
                "add it to the session's `cameras` before calibrating it"
            ),
        )

    now = utcnow()
    revision = await graph_repo.set_privacy_mask(
        graph,
        tenant_id=principal.tenant_id,
        session_id=session_id,
        camera_id=camera_id,
        polygon=[list(point) for point in body.polygon] if body.polygon else None,
        updated_at=now,
    )

    event_id = uuid.uuid4()
    await repository.append_event(
        session,
        EventIn(
            # Random, not derived. See the module docstring — this is the one
            # place in the codebase where deriving the id is the bug.
            event_id=event_id,
            tenant_id=principal.tenant_id,
            session_id=session_id,
            type=CALIBRATION_UPDATED,
            payload={
                "camera_id": camera_id,
                "kind": body.kind,
                "revision": revision,
                "by": principal.subject,
                "note": body.note,
                # Whether the polygon was set or cleared, so a reader of the log
                # can tell "masked from here" from "stopped masking here"
                # without holding the graph's current state. The polygon itself
                # is deliberately absent: the log is replayed and exported, and
                # a booth's sensitive geometry does not need to be in every copy
                # of it to make this event useful.
                "masked": body.polygon is not None,
            },
            occurred_at=now,
        ),
    )

    return CalibrationOut(
        camera_id=camera_id, kind=body.kind, revision=revision, event_id=event_id
    )


@router.get(
    "/{session_id}/cameras/{camera_id}/mask",
    response_model=MaskOut,
    summary="Read one camera's privacy mask (perception fetches this)",
)
async def get_mask(
    session_id: str,
    camera_id: str,
    principal: Principal = Depends(require_mask_reader),
    graph: GraphSession = Depends(get_graph_session),
) -> MaskOut:
    """The polygon and its revision, for an edge box to apply before inference.

    `require_mask_reader` rather than `require_reader` because perception
    authenticates with a device key, and every other read in the system refuses
    those. The exception is argued in that dependency's docstring: what this
    returns is the instruction not to look.

    404 for a camera that was never declared, and it is load-bearing.
    `perception/mask.py` refuses to start on it, because the alternative — a
    typo in `--camera-id` reading as "this booth has no sensitive surface" — is
    a booth running unmasked with nothing on screen to say so.
    """
    mask = await graph_repo.camera_mask(
        graph,
        tenant_id=principal.tenant_id,
        session_id=session_id,
        camera_id=camera_id,
    )
    if mask is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"camera {camera_id!r} is not declared on session {session_id!r}"
            ),
        )
    return MaskOut(
        camera_id=camera_id, polygon=mask["polygon"], revision=mask["revision"]
    )
