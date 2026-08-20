"""
Camera calibration, as executable claims.

The load-bearing one is `test_two_recalibrations_of_one_camera_are_two_events`.
`calibration.updated` is the audit trail for when masking changed
(`event-bus-spec.md` §3), and it is the one event in this codebase whose id must
be **random**: every other derived event is deduped on a derived id, and doing
that here would make the bus swallow the second recalibration silently, leaving
the log claiming a mask was applied at a time it was not.

Second is `test_a_session_config_save_does_not_clear_a_mask`. The wizard posts
its whole camera set on every save, so if `upsert_camera` wrote `privacy_mask`
the next save would blank it — and the next frame would reach the model unmasked,
with nothing anywhere to say so.

Runs against real Postgres and real Neo4j, in `t_test`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from neo4j import AsyncSession as GraphSession
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.auth.models import ApiKey, AuthUser
from app.auth.tokens import generate_api_key, issue_token
from app.db import get_session
from app.graph import repository as graph_repo
from app.main import app
from tests.conftest import as_tenant

T = "t_test"
OTHER = "t_test_other"
S = "s_calib"
CAM = "cam-1"

#: The right half of the frame. Normalized 0..1, same space a zone is drawn in.
RIGHT_HALF = [[0.5, 0.0], [1.0, 0.0], [1.0, 1.0], [0.5, 1.0]]
LEFT_HALF = [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]

CALIBRATE = f"/v1/sessions/{S}/cameras/{CAM}/calibration"
MASK = f"/v1/sessions/{S}/cameras/{CAM}/mask"


@pytest.fixture(autouse=True)
async def _scope_to_test_tenant(
    db_session: AsyncSession, graph_session: GraphSession
) -> AsyncIterator[None]:
    """Work in `t_test`, and start from an empty graph.

    `graph_session` is requested for its side effect — it wipes the test tenants
    — and not for the handle. Without it a camera declared by one test is still
    declared for the next, and `mask_revision` keeps climbing across the file,
    so every assertion about a specific revision passes or fails depending on
    what ran before it.
    """
    await as_tenant(db_session, T)
    yield


def _make_client(db_session: AsyncSession, headers: dict[str, str]) -> AsyncClient:
    async def override_get_session() -> AsyncIterator[AsyncSession]:
        yield db_session
        await db_session.commit()

    app.dependency_overrides[get_session] = override_get_session
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=headers
    )


async def _user(db_session: AsyncSession, user_id: str, role: str, tenant: str) -> str:
    db_session.add(
        AuthUser(
            user_id=user_id,
            email=f"{user_id}@floats.demo",
            display_name=user_id,
            tenant_id=tenant,
            role=role,
        )
    )
    await db_session.commit()
    return issue_token(subject=user_id, tenant_id=tenant, role=role)


@pytest.fixture
async def operator(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    token = await _user(db_session, "u_calib_op", "operator", T)
    async with _make_client(db_session, {"Authorization": f"Bearer {token}"}) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def viewer(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    token = await _user(db_session, "u_calib_view", "viewer", T)
    async with _make_client(db_session, {"Authorization": f"Bearer {token}"}) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def camera(db_session: AsyncSession) -> AsyncIterator[str]:
    """A device credential for `t_test` — what perception authenticates with."""
    key_id, plaintext, key_hash = generate_api_key()
    db_session.add(
        ApiKey(key_id=key_id, key_hash=key_hash, tenant_id=T, label="test camera")
    )
    await db_session.commit()
    async with _make_client(db_session, {"X-API-Key": plaintext}) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def declared(operator: AsyncClient) -> AsyncClient:
    """A session with one camera declared. The precondition for calibrating."""
    response = await operator.post(
        "/v1/sessions", json={"sessionId": S, "cameras": [{"id": CAM, "label": "Front"}]}
    )
    assert response.status_code == 200, response.text
    return operator


# ── the mask, written and read ────────────────────────────────────────────────


async def test_a_drawn_mask_comes_back_to_the_camera_that_fetches_it(
    declared: AsyncClient, camera: AsyncClient
) -> None:
    """The whole point, end to end: an operator draws, perception reads.

    Perception uses a *device* key, which every other read in the system refuses
    ("device credentials are write-only"). This is the one exception, argued in
    `require_mask_reader`: what it returns is the instruction not to look.
    """
    written = await declared.post(CALIBRATE, json={"kind": "privacy_mask", "polygon": RIGHT_HALF})
    assert written.status_code == 200, written.text
    assert written.json()["revision"] == 1

    read = await camera.get(MASK)
    assert read.status_code == 200, read.text
    assert read.json()["polygon"] == [[0.5, 0.0], [1.0, 0.0], [1.0, 1.0], [0.5, 1.0]]
    assert read.json()["revision"] == 1


async def test_a_camera_with_no_mask_is_not_the_same_as_no_camera(
    declared: AsyncClient, camera: AsyncClient
) -> None:
    """`polygon: null` is a decision; 404 is a misconfiguration.

    `perception/mask.py` acts on the difference — the first runs, the second
    refuses to start — because a typo in `--camera-id` must not read as "this
    booth has no sensitive surface".
    """
    present = await camera.get(MASK)
    assert present.status_code == 200
    assert present.json()["polygon"] is None
    assert present.json()["revision"] == 0

    absent = await camera.get(f"/v1/sessions/{S}/cameras/cam-typo/mask")
    assert absent.status_code == 404


async def test_clearing_a_mask_still_bumps_the_revision(declared: AsyncClient) -> None:
    """Removing a mask is a calibration change, and the edge box has to learn of it.

    A cleared mask that left the revision alone would leave every running camera
    applying the old polygon forever — masking pixels the operator deliberately
    un-masked, which looks like nothing at all from the dashboard.
    """
    first = await declared.post(CALIBRATE, json={"kind": "privacy_mask", "polygon": RIGHT_HALF})
    cleared = await declared.post(CALIBRATE, json={"kind": "privacy_mask", "polygon": None})

    assert cleared.status_code == 200
    assert cleared.json()["revision"] == first.json()["revision"] + 1


# ── the audit trail ───────────────────────────────────────────────────────────


async def test_two_recalibrations_of_one_camera_are_two_events(
    declared: AsyncClient, db_session: AsyncSession
) -> None:
    """The random-id property, stated as a test so nobody "fixes" it.

    Every other derived event in this codebase gets an id derived from its cause,
    and the bus dedupes on it. Doing that here — deriving from `camera_id` — would
    collapse these two into one: the second recalibration would be swallowed as a
    replay and the log would say the mask had been what it was at 14:00 all
    afternoon.
    """
    await declared.post(CALIBRATE, json={"kind": "privacy_mask", "polygon": RIGHT_HALF})
    await declared.post(CALIBRATE, json={"kind": "privacy_mask", "polygon": LEFT_HALF})

    events = await repository.read_events(
        db_session, tenant_id=T, session_id=S, type="calibration.updated", limit=50
    )
    assert len(events) == 2
    assert len({e.event_id for e in events}) == 2
    assert [e.payload["revision"] for e in events] == [1, 2]


async def test_the_event_records_who_and_whether_but_not_the_polygon(
    declared: AsyncClient, db_session: AsyncSession
) -> None:
    """`masked` and `by`, no geometry.

    The log is replayed and exported. A booth's sensitive geometry does not need
    to be in every copy of it for the event to do its job, which is to say when
    masking changed and who changed it.
    """
    await declared.post(
        CALIBRATE,
        json={"kind": "privacy_mask", "polygon": RIGHT_HALF, "note": "tripod knocked"},
    )
    events = await repository.read_events(
        db_session, tenant_id=T, session_id=S, type="calibration.updated", limit=5
    )
    payload = events[0].payload
    assert payload["camera_id"] == CAM
    assert payload["masked"] is True
    assert payload["by"] == "u_calib_op"
    assert payload["note"] == "tripod knocked"
    assert "polygon" not in payload


# ── the three refusals ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "kind,expected_status,expected_in_detail",
    [
        ("zone_map", 409, "POST /v1/sessions"),
        ("homography", 501, "normalized image coordinates"),
        ("reader_map", 501, "no producer"),
    ],
)
async def test_the_unsupported_kinds_are_refused_with_the_reason(
    declared: AsyncClient, kind: str, expected_status: int, expected_in_detail: str
) -> None:
    """Each refusal names what is missing, rather than 501-ing blankly.

    The same choice `/ops` makes when it declines to retry a tracker dead-letter.
    A calibration accepted and not applied would be a false line in the audit
    trail this event exists to be.
    """
    response = await declared.post(CALIBRATE, json={"kind": kind, "polygon": RIGHT_HALF})
    assert response.status_code == expected_status, response.text
    assert expected_in_detail in response.json()["detail"]


async def test_a_refused_kind_writes_nothing(
    declared: AsyncClient, db_session: AsyncSession
) -> None:
    """Refused means refused — no event, no revision bump.

    Worth its own test because the refusal is a guard clause, and a guard clause
    moved below the write is a change that passes every test above this one.
    """
    await declared.post(CALIBRATE, json={"kind": "homography", "polygon": RIGHT_HALF})

    events = await repository.read_events(
        db_session, tenant_id=T, session_id=S, type="calibration.updated", limit=5
    )
    assert events == []


# ── preconditions and access ──────────────────────────────────────────────────


async def test_calibrating_an_undeclared_camera_is_refused(operator: AsyncClient) -> None:
    """A mask for a camera nobody declared is a mask nothing ever fetches.

    Perception asks by the id it was started with, so a typo here would leave a
    polygon in the graph while the booth ran unmasked. The wizard declares
    cameras; this refuses to invent one.
    """
    await operator.post("/v1/sessions", json={"sessionId": S})
    response = await operator.post(CALIBRATE, json={"kind": "privacy_mask", "polygon": RIGHT_HALF})
    assert response.status_code == 404
    assert "not declared" in response.json()["detail"]


async def test_a_viewer_cannot_recalibrate(declared: AsyncClient, viewer: AsyncClient) -> None:
    """`run_activation`, per multi-tenant.md §3 — a viewer is the client's stakeholder."""
    response = await viewer.post(CALIBRATE, json={"kind": "privacy_mask", "polygon": RIGHT_HALF})
    assert response.status_code == 403
    assert "run_activation" in response.json()["detail"]


async def test_a_device_key_can_read_a_mask_and_nothing_else(
    declared: AsyncClient, camera: AsyncClient
) -> None:
    """The exception is exactly one endpoint wide.

    A camera key is the credential most likely to walk out of a venue. It may
    read the instruction not to look; it may not read the log, and it may not
    decide what gets masked.
    """
    assert (await camera.get(MASK)).status_code == 200
    assert (await camera.get(f"/v1/sessions/{S}")).status_code == 403
    assert (
        await camera.post(CALIBRATE, json={"kind": "privacy_mask", "polygon": RIGHT_HALF})
    ).status_code == 403


async def test_a_mask_is_not_visible_across_tenants(
    declared: AsyncClient, db_session: AsyncSession
) -> None:
    """The tenant comes from the credential, so there is nothing to forge."""
    token = await _user(db_session, "u_calib_other", "operator", OTHER)
    await as_tenant(db_session, T)
    async with _make_client(db_session, {"Authorization": f"Bearer {token}"}) as outsider:
        assert (await outsider.get(MASK)).status_code == 404
    app.dependency_overrides.clear()


# ── the mask survives an ordinary save ────────────────────────────────────────


async def test_a_session_config_save_does_not_clear_a_mask(
    declared: AsyncClient, camera: AsyncClient
) -> None:
    """The wizard posts its whole camera set on every save.

    If `upsert_camera` wrote `privacy_mask`, a save that listed the camera
    without one would blank it and the next frame would reach the model
    unmasked — a privacy failure that looks exactly like a working save.
    """
    await declared.post(CALIBRATE, json={"kind": "privacy_mask", "polygon": RIGHT_HALF})

    await declared.post(
        "/v1/sessions",
        json={"sessionId": S, "cameras": [{"id": CAM, "label": "Front, renamed"}]},
    )

    after = await camera.get(MASK)
    assert after.json()["polygon"] is not None
    assert after.json()["revision"] == 1


async def test_a_camera_dropped_from_the_set_is_pruned(
    declared: AsyncClient, camera: AsyncClient
) -> None:
    """Same contract as zones — and deleting a camera deletes its mask.

    The right failure direction: an operator who removes a camera and adds it
    back redraws. The alternative is a mask surviving invisibly and being
    reapplied to a camera someone has since repointed at a different wall.
    """
    await declared.post(CALIBRATE, json={"kind": "privacy_mask", "polygon": RIGHT_HALF})
    await declared.post("/v1/sessions", json={"sessionId": S, "cameras": []})

    assert (await camera.get(MASK)).status_code == 404


async def test_two_cameras_with_one_id_are_refused(operator: AsyncClient) -> None:
    """MERGE would collapse them, and they would share one mask.

    Worse than the zone version of this bug: the mask drawn for the camera
    facing the payment terminal would also apply to the one facing the entrance.
    """
    response = await operator.post(
        "/v1/sessions",
        json={"sessionId": S, "cameras": [{"id": CAM}, {"id": CAM, "label": "dupe"}]},
    )
    assert response.status_code == 422
    assert "duplicate camera ids" in response.text


async def test_a_mask_outside_the_unit_square_is_refused(declared: AsyncClient) -> None:
    """Pixels where normalized coordinates were expected.

    The same check zones get, from the same function — a mask validated more
    loosely than a zone would be one that passes validation and covers nothing.
    """
    response = await declared.post(
        CALIBRATE,
        json={"kind": "privacy_mask", "polygon": [[0, 0], [640, 0], [640, 480]]},
    )
    assert response.status_code == 422
    assert "normalized booth coordinates" in response.text
