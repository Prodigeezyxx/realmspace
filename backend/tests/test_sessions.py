"""
Session configuration, as executable claims.

The load-bearing one is `test_a_session_configured_through_the_api_produces_spatial_events`.
Everything else here checks a detail; that one checks the reason the endpoint
exists. Before it, `upsert_zone` had no caller outside the test suite, so a real
deployment started with an empty zone list, the tracker returned early on every
detection, and the entire spatial pipeline produced nothing while looking
perfectly healthy. It is the test that would have caught that.

Second in importance is `test_redrawing_a_zone_takes_effect_immediately`, which
is written so it fails if the cache invalidation event is removed — the cache is
warmed by a real tracker pass first, which is the only condition under which the
bug it guards against can appear.

Runs against real Postgres and real Neo4j, in `t_test`, which the graph fixture
wipes. Not `t_floats`: that is the dev tenant with real data in it.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from neo4j import AsyncSession as GraphSession
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.auth.models import ApiKey, AuthUser
from app.auth.tokens import generate_api_key, issue_token
from app.consumers.graph_writer import GraphWriterConsumer
from app.consumers.tracker import TrackerConsumer
from app.db import get_session
from app.graph import repository as graph_repo
from app.main import app
from app.schemas import EventIn
from tests.conftest import as_tenant

T = "t_test"
OTHER = "t_test_other"
S = "s_config"

BASE = dt.datetime(2026, 8, 3, 10, 0, 0, tzinfo=dt.timezone.utc)

LEFT_POLY = [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]
RIGHT_POLY = [[0.5, 0.0], [1.0, 0.0], [1.0, 1.0], [0.5, 1.0]]

FRAME_W, FRAME_H = 1000, 1000
LEFT_PX = [200, 400, 300, 600]   # centroid (250, 500) → 0.25 → left half


def zone(zone_id: str, name: str, polygon: list, **extra) -> dict:
    body = {"id": zone_id, "name": name, "type": "experience", "polygon": polygon}
    body.update(extra)
    return body


def config_body(zones: list[dict] | None = None, **extra) -> dict:
    body: dict = {"sessionId": S}
    if zones is not None:
        body["zones"] = zones
    body.update(extra)
    return body


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
async def _scope_to_test_tenant(db_session: AsyncSession) -> AsyncIterator[None]:
    """These tests work in `t_test`, not the default `t_floats`."""
    await as_tenant(db_session, T)
    yield


def _make_client(db_session: AsyncSession, token: str) -> AsyncClient:
    async def override_get_session() -> AsyncIterator[AsyncSession]:
        yield db_session
        await db_session.commit()

    app.dependency_overrides[get_session] = override_get_session
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


async def _user(db_session: AsyncSession, user_id: str, role: str, tenant: str) -> str:
    """Create a user in `tenant` and return a token for them."""
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
    """An operator in `t_test` — the role that configures an activation."""
    token = await _user(db_session, "u_op", "operator", T)
    async with _make_client(db_session, token) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def viewer(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    token = await _user(db_session, "u_view", "viewer", T)
    async with _make_client(db_session, token) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def outsider(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """An operator in a *different* tenant. Exists only to be unable to see."""
    token = await _user(db_session, "u_other", "operator", OTHER)
    async with _make_client(db_session, token) as ac:
        yield ac
    app.dependency_overrides.clear()


async def detect(
    db_session: AsyncSession, *, at: dt.datetime, bbox: list[int] = LEFT_PX
) -> None:
    await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(),
            tenant_id=T,
            session_id=S,
            type="perception.detection",
            payload={
                "person_id": "P-001",
                "bbox": bbox,
                "confidence": 0.9,
                "frame_width": FRAME_W,
                "frame_height": FRAME_H,
            },
            occurred_at=at,
        ),
    )
    await db_session.commit()


async def stay(
    db_session: AsyncSession,
    *,
    at: dt.datetime,
    bbox: list[int] = LEFT_PX,
    seconds: float = 5,
    step: float = 2.5,
) -> None:
    """Detections across a stay, as a camera produces them.

    One detection is not a visit any more: the tracker's confirm window
    (`tracker_zone_confirm_seconds`) requires a zone to be held before it is
    believed, which is what stops someone on a boundary generating a visit per
    frame. Tests feed the same shape production sees.
    """
    t = at
    end = at + dt.timedelta(seconds=seconds)
    while True:
        await detect(db_session, at=t, bbox=bbox)
        if t >= end:
            break
        t = min(t + dt.timedelta(seconds=step), end)


async def types_in_log(db_session: AsyncSession) -> list[str]:
    rows = await repository.read_events(db_session, tenant_id=T, since_seq=0, limit=200)
    return [r.type for r in rows]


# ── the reason this endpoint exists ───────────────────────────────────────────


async def test_a_session_configured_through_the_api_produces_spatial_events(
    operator: AsyncClient, db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """Config → detection → spatial event. The hole this endpoint closes.

    Nothing else in the suite covers this path, because every other test seeds
    zones by calling `upsert_zone` directly. That is exactly what hid the
    problem: the graph was always pre-populated by hand, so no test ever ran the
    tracker against a system configured the way a real one is.
    """
    posted = await operator.post(
        "/v1/sessions",
        json=config_body([zone("z_left", "Entrance", LEFT_POLY, type="entry")]),
    )
    assert posted.status_code == 200, posted.text

    await stay(db_session, at=BASE)
    await TrackerConsumer().run_once()

    assert "spatial.zone_enter" in await types_in_log(db_session)


async def test_redrawing_a_zone_takes_effect_immediately(
    operator: AsyncClient, db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """A zone edit invalidates the tracker's cache without waiting for the TTL.

    The first tracker pass is not scenery — it warms the polygon cache. Without
    it the second pass would fetch the new zones anyway and the test would pass
    with the invalidation deleted, which is the failure mode that makes a test
    worse than no test.

    After the redraw, the person has not moved but the zone has moved out from
    under them, so the tracker must emit the exit and the dwell.
    """
    await operator.post(
        "/v1/sessions", json=config_body([zone("z_left", "Entrance", LEFT_POLY)])
    )
    await stay(db_session, at=BASE)

    tracker = TrackerConsumer()
    await tracker.run_once()
    assert "spatial.zone_enter" in await types_in_log(db_session)
    assert tracker._zones, "the first pass should have cached the polygons"

    # Same zone id, opposite half of the frame. The person standing on the left
    # is now outside it.
    await operator.post(
        "/v1/sessions", json=config_body([zone("z_left", "Entrance", RIGHT_POLY)])
    )
    await stay(db_session, at=BASE + dt.timedelta(seconds=10))
    await tracker.run_once()

    types = await types_in_log(db_session)
    assert "session.zones_updated" in types
    assert "spatial.zone_exit" in types, "the redraw did not invalidate the cache"
    assert "spatial.dwell" in types


# ── configuration round trip ──────────────────────────────────────────────────


async def test_config_round_trips_including_the_measurement_parameters(
    operator: AsyncClient, graph_session: GraphSession
) -> None:
    """The ROI parameters are what make this more than a zone editor."""
    await operator.post(
        "/v1/sessions",
        json=config_body(
            [zone("z_a", "Mirror Room", LEFT_POLY, weight=2.5, funnelOrder=1)],
            venue="Test Hall",
            engagedThresholdSeconds=45,
            activationCost=12000,
            currency="GBP",
            attributionModel="linear",
        ),
    )

    got = await operator.get(f"/v1/sessions/{S}")
    assert got.status_code == 200
    body = got.json()

    assert body["venue"] == "Test Hall"
    assert body["engagedThresholdSeconds"] == 45
    assert body["activationCost"] == 12000
    assert body["currency"] == "GBP"
    assert body["attributionModel"] == "linear"

    (z,) = body["zones"]
    assert z["weight"] == 2.5
    assert z["funnelOrder"] == 1
    assert z["polygon"] == [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]


async def test_operator_supplied_figures_round_trip_and_default_to_absent(
    operator: AsyncClient, graph_session: GraphSession
) -> None:
    """Influenced revenue is typed in by a human, and absent is not zero.

    Nothing measures it until Phase 4's CRM attribution. A session that has not
    been given one must come back with `None` so the report can say "unknown"
    — reporting a 0 would make an unmeasured activation look like a failed one.
    """
    await operator.post("/v1/sessions", json=config_body([zone("z_a", "A", LEFT_POLY)]))
    body = (await operator.get(f"/v1/sessions/{S}")).json()
    assert body["revenueInfluenced"] is None
    assert body["qualifiedLeads"] is None

    await operator.post(
        "/v1/sessions",
        json=config_body(None, revenueInfluenced=50400, qualifiedLeads=318),
    )
    body = (await operator.get(f"/v1/sessions/{S}")).json()
    assert body["revenueInfluenced"] == 50400
    assert body["qualifiedLeads"] == 318


async def test_touchpoints_become_surfaces_an_interaction_can_land_on(
    operator: AsyncClient, db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """Config → interaction → graph edge, the Engagement layer's other half.

    The graph writer refuses an interaction for a surface nobody configured, so
    without the wizard publishing its touchpoints every reading a real kiosk
    sent would be dropped on arrival — the same shape of hole that zones had
    before `POST /v1/sessions` existed.
    """
    await operator.post(
        "/v1/sessions",
        json=config_body(
            [zone("z_a", "Mirror Room", LEFT_POLY)],
            touchpoints=[
                {"id": "sf_mirror", "label": "AR Mirror", "type": "ar_mirror", "zoneId": "z_a"}
            ],
        ),
    )

    body = (await operator.get(f"/v1/sessions/{S}")).json()
    assert [t["id"] for t in body["touchpoints"]] == ["sf_mirror"]
    assert body["touchpoints"][0]["triggerCount"] == 0

    # A touchpoint reports somebody using it.
    await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(),
            tenant_id=T,
            session_id=S,
            type="surface.interaction",
            payload={"anon_id": "P-001", "surface_id": "sf_mirror", "kind": "ar"},
            occurred_at=BASE,
        ),
    )
    await db_session.commit()
    await GraphWriterConsumer().run_once()

    after = (await operator.get(f"/v1/sessions/{S}")).json()
    assert after["touchpoints"][0]["triggerCount"] == 1


async def test_replaying_an_interaction_does_not_inflate_the_count(
    operator: AsyncClient, db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """A sponsor's usage figure must survive the pipeline being run twice.

    The graph is a separate store from the log, so a crash between a graph write
    and the cursor advance guarantees redelivery. If the counter moved on every
    pass, every restart would quietly hand the sponsor a better number.
    """
    await operator.post(
        "/v1/sessions",
        json=config_body(
            [zone("z_a", "A", LEFT_POLY)],
            touchpoints=[{"id": "sf_quiz", "label": "Scent Quiz", "type": "quiz"}],
        ),
    )
    await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(),
            tenant_id=T,
            session_id=S,
            type="surface.interaction",
            payload={"anon_id": "P-001", "surface_id": "sf_quiz", "kind": "game"},
            occurred_at=BASE,
        ),
    )
    await db_session.commit()

    writer = GraphWriterConsumer()
    await writer.run_once()
    await repository.reset_cursor(db_session, consumer=writer.name, tenant_id=T)
    await db_session.commit()
    await writer.run_once()

    body = (await operator.get(f"/v1/sessions/{S}")).json()
    assert body["touchpoints"][0]["triggerCount"] == 1


async def test_a_removed_touchpoint_stops_appearing(
    operator: AsyncClient, graph_session: GraphSession
) -> None:
    await operator.post(
        "/v1/sessions",
        json=config_body(
            [zone("z_a", "A", LEFT_POLY)],
            touchpoints=[
                {"id": "sf_a", "label": "A", "type": "screen"},
                {"id": "sf_b", "label": "B", "type": "rfid"},
            ],
        ),
    )
    await operator.post(
        "/v1/sessions",
        json=config_body(
            [zone("z_a", "A", LEFT_POLY)],
            touchpoints=[{"id": "sf_a", "label": "A", "type": "screen"}],
        ),
    )

    body = (await operator.get(f"/v1/sessions/{S}")).json()
    assert [t["id"] for t in body["touchpoints"]] == ["sf_a"]


async def test_duplicate_touchpoint_ids_are_refused(
    operator: AsyncClient, graph_session: GraphSession
) -> None:
    r = await operator.post(
        "/v1/sessions",
        json=config_body(
            [zone("z_a", "A", LEFT_POLY)],
            touchpoints=[
                {"id": "sf_a", "label": "A", "type": "screen"},
                {"id": "sf_a", "label": "B", "type": "rfid"},
            ],
        ),
    )
    assert r.status_code == 422
    assert "duplicate touchpoint ids" in r.text


async def test_defaults_match_the_scorecard_the_dashboard_already_computes(
    operator: AsyncClient, graph_session: GraphSession
) -> None:
    """A session configured with nothing still has usable parameters.

    60s and weight 1.0 are the defaults in dashboard/src/lib/roi/scorecard.ts.
    If these drift apart, the same session scores differently depending on which
    side computed it, and nothing announces the disagreement.
    """
    await operator.post("/v1/sessions", json=config_body([zone("z_a", "A", LEFT_POLY)]))
    body = (await operator.get(f"/v1/sessions/{S}")).json()

    assert body["engagedThresholdSeconds"] == 60.0
    assert body["attributionModel"] == "influenced"
    assert body["zones"][0]["weight"] == 1.0


async def test_a_deleted_zone_stops_collecting_dwell(
    operator: AsyncClient, graph_session: GraphSession
) -> None:
    """Re-posting without a zone removes it, rather than orphaning it.

    An orphaned zone is worse than a missing one: the tracker keeps scoring
    detections against a shape nobody can see, and the report shows dwell in a
    zone the client has been told does not exist.
    """
    await operator.post(
        "/v1/sessions",
        json=config_body(
            [zone("z_a", "A", LEFT_POLY), zone("z_b", "B", RIGHT_POLY)]
        ),
    )
    await operator.post(
        "/v1/sessions", json=config_body([zone("z_a", "A", LEFT_POLY)])
    )

    body = (await operator.get(f"/v1/sessions/{S}")).json()
    assert [z["id"] for z in body["zones"]] == ["z_a"]


async def test_omitting_zones_leaves_them_alone(
    operator: AsyncClient, graph_session: GraphSession
) -> None:
    """Setting the cost later must not silently wipe the zones."""
    await operator.post(
        "/v1/sessions", json=config_body([zone("z_a", "A", LEFT_POLY)])
    )
    await operator.post("/v1/sessions", json=config_body(None, activationCost=500))

    body = (await operator.get(f"/v1/sessions/{S}")).json()
    assert [z["id"] for z in body["zones"]] == ["z_a"]
    assert body["activationCost"] == 500


async def test_zones_come_back_in_funnel_order(
    operator: AsyncClient, graph_session: GraphSession
) -> None:
    """The funnel is an ordering, so the API returns one (roi-framework.md §5)."""
    await operator.post(
        "/v1/sessions",
        json=config_body(
            [
                zone("z_product", "Product", RIGHT_POLY, funnelOrder=2),
                zone("z_entry", "Entry", LEFT_POLY, funnelOrder=0),
            ]
        ),
    )
    body = (await operator.get(f"/v1/sessions/{S}")).json()
    assert [z["id"] for z in body["zones"]] == ["z_entry", "z_product"]


# ── validation ────────────────────────────────────────────────────────────────


async def test_a_pixel_polygon_is_refused(
    operator: AsyncClient, graph_session: GraphSession
) -> None:
    """Pixels where normalized coordinates belong would match nothing, forever.

    The tracker divides every detection by the frame size before comparing, so a
    polygon at (200, 400) is outside the unit square and contains nobody. Silent
    in production; a 422 here.
    """
    r = await operator.post(
        "/v1/sessions",
        json=config_body([zone("z_a", "A", [[200, 400], [300, 400], [300, 600]])]),
    )
    assert r.status_code == 422
    assert "normalized" in r.text


async def test_a_two_point_polygon_is_refused(
    operator: AsyncClient, graph_session: GraphSession
) -> None:
    r = await operator.post(
        "/v1/sessions", json=config_body([zone("z_a", "A", [[0.0, 0.0], [0.5, 0.5]])])
    )
    assert r.status_code == 422
    assert "at least 3 points" in r.text


async def test_duplicate_zone_ids_are_refused(
    operator: AsyncClient, graph_session: GraphSession
) -> None:
    """MERGE would silently collapse them and lose one of the operator's zones."""
    r = await operator.post(
        "/v1/sessions",
        json=config_body([zone("z_a", "A", LEFT_POLY), zone("z_a", "B", RIGHT_POLY)]),
    )
    assert r.status_code == 422
    assert "duplicate zone ids" in r.text


async def test_a_zone_can_be_saved_before_it_is_drawn(
    operator: AsyncClient, graph_session: GraphSession
) -> None:
    """The wizard lets an operator name a zone before drawing it, and this
    endpoint passes `include_undrawn=True` precisely so it comes back.

    It could not. An undrawn zone was read back with `polygon: []`, which
    `ZoneConfig` rejects — rightly, a shape with no points contains nobody — so
    the *response* raised after the write had already landed. The operator got a
    500, and because an unhandled 500 carries no CORS headers the browser could
    not read it either: the wizard reported "the bus is unreachable" about a
    backend that was up and had just stored their session.
    """
    r = await operator.post(
        "/v1/sessions",
        json=config_body(
            [zone("z_drawn", "Drawn", LEFT_POLY), zone("z_later", "Not yet", None)]
        ),
    )
    assert r.status_code == 200, r.text

    got = await operator.get(f"/v1/sessions/{S}")
    assert got.status_code == 200
    zones = {z["id"]: z for z in got.json()["zones"]}
    assert zones["z_later"]["polygon"] is None
    assert zones["z_drawn"]["polygon"] == [list(p) for p in LEFT_POLY]


async def test_an_unhandled_error_still_answers_with_cors_headers(
    db_session: AsyncSession, graph_session: GraphSession, monkeypatch
) -> None:
    """A 500 the browser is allowed to read.

    Starlette's ServerErrorMiddleware is outside CORSMiddleware, so an
    exception that reaches it returns a 500 with no `access-control-allow-origin`
    and every browser turns that into a network error — the client cannot tell
    "your request was rejected" from "nothing is listening". The app registers a
    handler so the response comes back through CORS instead.
    """
    from app.graph import repository as graph_repo

    async def boom(*args, **kwargs):
        raise RuntimeError("graph exploded")

    monkeypatch.setattr(graph_repo, "upsert_session", boom)

    # raise_app_exceptions=False so the transport returns the app's 500 response
    # instead of re-raising the exception in the test — the browser's view of
    # this request is the entire point, and a browser gets a response.
    token = await _user(db_session, "u_op_boom", "operator", T)

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        yield db_session
        await db_session.commit()

    app.dependency_overrides[get_session] = override_get_session
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        r = await client.post(
            "/v1/sessions",
            json=config_body([zone("z_a", "A", LEFT_POLY)]),
            headers={"Origin": "http://localhost:3000"},
        )
    app.dependency_overrides.clear()

    assert r.status_code == 500
    assert r.json()["detail"] == "internal error — see the backend log"
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"


async def test_an_unconfigured_session_is_a_404_not_an_empty_config(
    operator: AsyncClient, graph_session: GraphSession
) -> None:
    """"Never set up" and "set up with nothing" must stay distinguishable."""
    r = await operator.get("/v1/sessions/s_nonexistent")
    assert r.status_code == 404


# ── who may configure ─────────────────────────────────────────────────────────


async def test_a_viewer_cannot_configure_an_activation(
    viewer: AsyncClient, graph_session: GraphSession
) -> None:
    r = await viewer.post(
        "/v1/sessions", json=config_body([zone("z_a", "A", LEFT_POLY)])
    )
    assert r.status_code == 403


async def test_a_device_key_cannot_configure_an_activation(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """A camera feeds an activation; it does not get to define how it is scored.

    This is the credential most likely to walk out of a venue, and the zone
    weights are what the ROI report multiplies by.
    """
    key_id, plaintext, key_hash = generate_api_key()
    db_session.add(
        ApiKey(key_id=key_id, key_hash=key_hash, tenant_id=T, label="test camera")
    )
    await db_session.commit()

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        yield db_session
        await db_session.commit()

    app.dependency_overrides[get_session] = override_get_session
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": plaintext},
    ) as ac:
        r = await ac.post(
            "/v1/sessions", json=config_body([zone("z_a", "A", LEFT_POLY)])
        )
    app.dependency_overrides.clear()

    assert r.status_code == 403


async def test_another_tenants_configuration_is_invisible(
    operator: AsyncClient, outsider: AsyncClient, graph_session: GraphSession
) -> None:
    """The graph cannot enforce this, so it has to be tested rather than assumed.

    Neo4j Community has no row-level security (graph/schema.py). The only thing
    standing between one tenant and another's zones is that every repository
    function takes a tenant_id from the credential — which is a property of the
    code, and properties of code need a test.
    """
    await operator.post(
        "/v1/sessions", json=config_body([zone("z_a", "A", LEFT_POLY)])
    )

    assert (await outsider.get(f"/v1/sessions/{S}")).status_code == 404

    graph = await outsider.get(f"/v1/sessions/{S}/graph")
    assert graph.status_code == 200
    assert graph.json()["zones"] == []
    assert graph.json()["uniquePeople"] == 0


# ── aggregates ────────────────────────────────────────────────────────────────


async def test_graph_endpoint_reports_dwell_and_unique_people(
    operator: AsyncClient, db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """The two numbers the graph knows better than the event log does."""
    await operator.post(
        "/v1/sessions",
        json=config_body([zone("z_a", "Mirror Room", LEFT_POLY, weight=2.0)]),
    )

    await graph_repo.upsert_person(
        graph_session,
        tenant_id=T,
        session_id=S,
        anon_id="P-001",
        first_seen=BASE.isoformat(),
        last_seen=BASE.isoformat(),
    )
    await graph_repo.link_dwelled_in(
        graph_session,
        tenant_id=T,
        session_id=S,
        anon_id="P-001",
        zone_id="z_a",
        duration=90.0,
        started_at=BASE.isoformat(),
        ended_at=(BASE + dt.timedelta(seconds=90)).isoformat(),
    )

    body = (await operator.get(f"/v1/sessions/{S}/graph")).json()
    assert body["uniquePeople"] == 1
    assert body["dwellByZone"] == [
        {"zoneId": "z_a", "zone": "Mirror Room", "avgDwell": 90.0, "visitors": 1}
    ]
    assert body["zones"][0]["weight"] == 2.0


# ── zones and the camera that owns them (multi-camera fusion) ─────────────────


async def test_a_zone_can_name_the_camera_it_was_drawn_in(
    operator: AsyncClient, graph_session: GraphSession
) -> None:
    """Round trip, and the default that keeps every existing zone working.

    A zone with no `cameraId` is scored against every camera. That has to be the
    default rather than a migration, because it is what every zone drawn before
    cameras had owners already is.
    """
    res = await operator.post(
        "/v1/sessions",
        json=config_body(
            zones=[
                zone("z_left", "Entrance", LEFT_POLY, cameraId="cam-1"),
                zone("z_right", "Lounge", RIGHT_POLY),
            ],
            cameras=[{"id": "cam-1"}, {"id": "cam-2"}],
        ),
    )
    assert res.status_code == 200, res.text

    by_id = {z["id"]: z for z in res.json()["zones"]}
    assert by_id["z_left"]["cameraId"] == "cam-1"
    assert by_id["z_right"]["cameraId"] is None


async def test_a_zone_naming_an_undeclared_camera_is_refused(
    operator: AsyncClient, graph_session: GraphSession
) -> None:
    """Accepting it would mean a zone the tracker never scores anything against.

    It collects no dwell and reports as a part of the booth nobody visited,
    which is indistinguishable from a real finding — so the typo has to be
    caught here, at setup, where the operator can still see what they typed.
    """
    res = await operator.post(
        "/v1/sessions",
        json=config_body(
            zones=[zone("z_left", "Entrance", LEFT_POLY, cameraId="cam-typo")],
            cameras=[{"id": "cam-1"}],
        ),
    )
    assert res.status_code == 422, res.text
    assert "cam-typo" in res.text


async def test_a_camera_id_may_not_contain_the_namespace_separator(
    operator: AsyncClient, graph_session: GraphSession
) -> None:
    """`/` separates the camera from the track id it namespaces.

    Allowed inside a camera id, the join stops being reversible and two
    different people can produce the same key — the collision the namespace
    exists to prevent, one level up.
    """
    res = await operator.post(
        "/v1/sessions",
        json=config_body(cameras=[{"id": "cam/1"}]),
    )
    assert res.status_code == 422, res.text


async def test_removing_a_camera_a_zone_still_names_is_refused(
    operator: AsyncClient, graph_session: GraphSession
) -> None:
    """The case a schema validator cannot see, because the zones are stored.

    A later save that prunes `cam-2` would otherwise orphan the zone drawn in
    its frame — silently, since the request that removes the camera says
    nothing about zones at all.
    """
    first = await operator.post(
        "/v1/sessions",
        json=config_body(
            zones=[zone("z_right", "Lounge", RIGHT_POLY, cameraId="cam-2")],
            cameras=[{"id": "cam-1"}, {"id": "cam-2"}],
        ),
    )
    assert first.status_code == 200, first.text

    # Only the camera set is posted. The stored zone keeps its owner, and
    # nothing in this request mentions it — which is exactly why the refusal has
    # to come from the stored state rather than from the request body.
    res = await operator.post(
        "/v1/sessions",
        json=config_body(cameras=[{"id": "cam-1"}]),
    )
    assert res.status_code == 409, res.text
    assert "cam-2" in res.text

    # And the camera is still there: a refused save changes nothing.
    still = await operator.get(f"/v1/sessions/{S}")
    assert {c["id"] for c in still.json()["cameras"]} == {"cam-1", "cam-2"}


async def test_a_save_that_moves_a_zone_off_a_camera_may_then_remove_it(
    operator: AsyncClient, graph_session: GraphSession
) -> None:
    """The way out of the refusal above, so it is a guard and not a trap."""
    await operator.post(
        "/v1/sessions",
        json=config_body(
            zones=[zone("z_right", "Lounge", RIGHT_POLY, cameraId="cam-2")],
            cameras=[{"id": "cam-1"}, {"id": "cam-2"}],
        ),
    )

    res = await operator.post(
        "/v1/sessions",
        json=config_body(
            zones=[zone("z_right", "Lounge", RIGHT_POLY)],
            cameras=[{"id": "cam-1"}],
        ),
    )
    assert res.status_code == 200, res.text
    assert res.json()["zones"][0]["cameraId"] is None
    assert {c["id"] for c in res.json()["cameras"]} == {"cam-1"}


# ── the listing the report's benchmark reads ──────────────────────────────────


async def test_sessions_are_listed_newest_first(
    operator: AsyncClient, graph_session: GraphSession
) -> None:
    """A tenant's activations, in the order a report wants to compare them.

    Nulls last rather than first: a session created but never dated is real and
    belongs in the list, but it is not the most recent thing that happened.

    Dated **relative to now** rather than with literals. The listing is clamped
    to the plan's retention window (`app/plans.py`), and fixed dates would put
    this test's activations outside it as the calendar moved — passing today and
    failing in November for a reason that has nothing to do with ordering.
    """
    now = dt.datetime.now(dt.timezone.utc)
    for session_id, started in (
        ("s_march", (now - dt.timedelta(days=60)).isoformat()),
        ("s_july", (now - dt.timedelta(days=7)).isoformat()),
        ("s_undated", None),
    ):
        await graph_repo.upsert_session(
            graph_session,
            tenant_id=T,
            session_id=session_id,
            venue="Test Hall",
            started_at=started,
        )

    res = await operator.get("/v1/sessions")
    assert res.status_code == 200, res.text
    assert [s["sessionId"] for s in res.json()] == ["s_july", "s_march", "s_undated"]


async def test_the_listing_carries_settings_and_no_measurements(
    operator: AsyncClient, graph_session: GraphSession
) -> None:
    """What was agreed, never what was counted.

    A visitor count here would be a second definition of "unique visitor" —
    the one thing `GET /{id}/graph` says in writing it will not create. The
    benchmark gets its figures by running the browser's scorecard over each
    session's log, so this only has to say which sessions exist.
    """
    await graph_repo.upsert_session(
        graph_session,
        tenant_id=T,
        session_id=S,
        client="Aperture",
        campaign="Summer Launch",
        venue="Test Hall",
        activation_cost=12000.0,
        revenue_influenced=38400.0,
        qualified_leads=42,
        engaged_threshold_seconds=45.0,
        currency="GBP",
    )

    row = (await operator.get("/v1/sessions")).json()[0]
    assert row["client"] == "Aperture"
    assert row["activationCost"] == 12000.0
    assert row["revenueInfluenced"] == 38400.0
    assert row["engagedThresholdSeconds"] == 45.0
    assert row["currency"] == "GBP"
    # The measured half is deliberately absent.
    assert "uniquePeople" not in row
    assert "engagementRate" not in row


async def test_a_tenant_cannot_enumerate_another_tenants_activations(
    operator: AsyncClient, outsider: AsyncClient, graph_session: GraphSession
) -> None:
    """The listing names every activation a client has run — a client list.

    There is no tenant parameter to forge; the scope comes from the credential,
    as it does on `GET /events`.
    """
    await graph_repo.upsert_session(
        graph_session, tenant_id=T, session_id=S, venue="Test Hall"
    )

    assert [s["sessionId"] for s in (await operator.get("/v1/sessions")).json()] == [S]
    assert (await outsider.get("/v1/sessions")).json() == []


async def test_listing_the_empty_case_is_a_list_and_not_a_404(
    operator: AsyncClient, graph_session: GraphSession
) -> None:
    """A tenant who has run nothing yet has run nothing, which is an answer.

    The report distinguishes "we have not looked" from "there is nothing" and a
    404 here would collapse the two.
    """
    res = await operator.get("/v1/sessions")
    assert res.status_code == 200
    assert res.json() == []
