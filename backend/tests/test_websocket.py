"""
The live WebSocket, as executable claims.

**Why this module is synchronous while every other suite is async.** WebSockets
need Starlette's `TestClient`, which runs the app in its own event loop on a
background thread; httpx's ASGI transport cannot do WS at all. That thread
boundary is not incidental — the socket object belongs to the app's loop, so a
broadcast triggered from pytest's loop would be sending on a socket owned by
another one. Everything here therefore goes through the app: events are POSTed
over HTTP and delivered by the real consumers started by the real lifespan.

That makes these genuine end-to-end tests rather than unit tests with a socket
bolted on, at the cost of some timing. See `_expect` for how that is handled
without arbitrary sleeps.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.config import get_settings
from app.main import app

T = "t_ws"
OTHER = "t_ws_other"
S = "s_ws"

BASE = dt.datetime(2026, 7, 31, 12, 0, 0, tzinfo=dt.timezone.utc)


def _test_url() -> str:
    url = get_settings().test_database_url
    assert url and "test" in url, "refusing to run against a non-test database"
    return url


@pytest.fixture(autouse=True)
def consumer_wiring():
    """Override conftest's autouse fixture — it must not run for this module.

    That fixture opens the shared Neo4j driver on pytest's event loop. Here the
    app has its own loop (TestClient runs it on a background thread) and its
    lifespan opens the driver itself. Letting both happen leaves the consumers
    using a driver bound to the wrong loop, which surfaces as "Future attached
    to a different loop" during teardown.

    A module-level fixture of the same name shadows the conftest one, so this is
    the ordinary way to opt out.
    """
    yield


@pytest.fixture
def ws_client():
    """A TestClient with the real lifespan running, pointed at the test database.

    The lifespan starts the actual consumers, which is what makes a POSTed event
    come back out of the socket. `app.db.SessionLocal` is swapped rather than
    injected because consumers are loops, not request handlers — there is no
    dependency to override.
    """
    from app import db as app_db

    # NullPool on purpose. A pooled asyncpg connection belongs to the event loop
    # that opened it, and here two loops are in play: pytest's (for setup) and
    # the TestClient's (for the app). Pooling would leave connections owned by
    # one loop being closed from another — "Future attached to a different loop".
    # Not pooling in a test costs nothing.
    # The app role, so the consumers run under the RLS policies exactly as they
    # do in production. TRUNCATE below goes through a separate owner connection,
    # because the app role deliberately cannot truncate.
    engine = create_async_engine(_test_url(), poolclass=NullPool)
    admin_engine = create_async_engine(
        _test_url().replace("realmspace_app@", "antoniorobles@"), poolclass=NullPool
    )
    original = app_db.SessionLocal
    app_db.SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    creds: dict[str, str] = {}

    async def reset() -> None:
        """Wipe, then mint the credentials these tests authenticate with.

        Auth is not optional any more, so a socket test that did not
        authenticate would only ever prove the handshake gets refused.
        """
        from app.auth.models import ApiKey, AuthUser
        from app.auth.tokens import generate_api_key, issue_token

        async with async_sessionmaker(admin_engine, expire_on_commit=False)() as s:
            await s.execute(
                text(
                    "TRUNCATE event_log, consumer_cursor, dead_letter, "
                    "auth_user, api_key RESTART IDENTITY"
                )
            )
            user = AuthUser(user_id="u_ws", email="ws@floats.demo",
                            display_name="WS", tenant_id=T, role="admin")
            s.add(user)
            key_id, plaintext, key_hash = generate_api_key()
            s.add(ApiKey(key_id=key_id, key_hash=key_hash, tenant_id=T, label="ws test"))
            await s.commit()

        creds["token"] = issue_token(subject="u_ws", tenant_id=T, role="admin")
        creds["key"] = plaintext

    asyncio.run(reset())

    # `with` runs startup and shutdown, so the consumers really are live.
    with TestClient(app, headers={"Authorization": f"Bearer {creds['token']}"}) as client:
        # stash for building socket URLs and for the negative tests
        client.token = creds["token"]          # type: ignore[attr-defined]
        client.api_key = creds["key"]          # type: ignore[attr-defined]
        yield client

    app_db.SessionLocal = original
    asyncio.run(engine.dispose())
    asyncio.run(admin_engine.dispose())  # no-op with NullPool, but keeps intent clear


def make_event(**overrides) -> dict:
    event = {
        "event_id": str(uuid.uuid4()),
        "tenant_id": T,
        "session_id": S,
        "type": "perception.detection",
        "payload": {"person_id": "P-001", "bbox": [10, 10, 90, 180],
                    "frame_width": 1280, "frame_height": 720},
        "occurred_at": BASE.isoformat(),
    }
    event.update(overrides)
    return event


def _expect(ws, predicate, *, limit: int = 40):
    """Read frames until one satisfies `predicate`, or give up.

    The socket carries traffic from every consumer, so the frame you want is not
    necessarily the next one. Draining until a match — with a bound — is both
    more robust and faster than sleeping and hoping.
    """
    for _ in range(limit):
        frame = ws.receive_json()
        if predicate(frame):
            return frame
    raise AssertionError(f"no matching frame in {limit} frames")


# ── the handshake ─────────────────────────────────────────────────────────────


def test_connecting_yields_a_hello(ws_client: TestClient) -> None:
    with ws_client.websocket_connect(f"/v1/ws/{T}/{S}?token={ws_client.token}") as ws:
        hello = ws.receive_json()
    assert hello["type"] == "hello"
    assert hello["tenantId"] == T
    assert hello["sessionId"] == S
    assert hello["replay"] == []


def test_hello_replays_what_the_client_missed(ws_client: TestClient) -> None:
    """A client connecting mid-session gets the backlog, not an empty feed."""
    for i in range(3):
        assert ws_client.post("/events", json=make_event(payload={"i": i})).status_code == 201

    with ws_client.websocket_connect(f"/v1/ws/{T}/{S}?token={ws_client.token}") as ws:
        hello = ws.receive_json()

    assert [e["payload"]["i"] for e in hello["replay"]] == [0, 1, 2]


def test_since_seq_is_a_real_cursor(ws_client: TestClient) -> None:
    """The difference from the other track: reconnect from where you got to.

    Replaying from 0 every time means a browser that drops wifi re-processes
    everything it already had. Passing the last seq it saw gives no gap and no
    duplicates — the same cursor discipline the consumers use, on the last hop.
    """
    for i in range(3):
        ws_client.post("/events", json=make_event(payload={"i": i}))

    with ws_client.websocket_connect(f"/v1/ws/{T}/{S}?token={ws_client.token}") as ws:
        first_seq = ws.receive_json()["replay"][0]["seq"]

    with ws_client.websocket_connect(f"/v1/ws/{T}/{S}?since_seq={first_seq}&token={ws_client.token}") as ws:
        hello = ws.receive_json()

    assert hello["sinceSeq"] == first_seq
    assert [e["payload"]["i"] for e in hello["replay"]] == [1, 2]


# ── live delivery ─────────────────────────────────────────────────────────────


def test_an_event_posted_after_connect_arrives(ws_client: TestClient) -> None:
    """The whole point of the item: POST here, appears in the browser."""
    with ws_client.websocket_connect(f"/v1/ws/{T}/{S}?token={ws_client.token}") as ws:
        assert ws.receive_json()["type"] == "hello"

        event = make_event(payload={"marker": "live"})
        assert ws_client.post("/events", json=event).status_code == 201

        frame = _expect(
            ws,
            lambda f: f["type"] == "event"
            and f["event"]["payload"].get("marker") == "live",
        )

    assert frame["event"]["eventId"] == event["event_id"]


def test_wire_shape_matches_the_dashboard_contract(ws_client: TestClient) -> None:
    """dashboard/src/lib/contracts/events.ts declares itself canonical: camelCase
    keys, ms-epoch numbers. A mismatch means POD 3 writes a mapper for this
    track and not the other, which biases the comparison on nothing."""
    with ws_client.websocket_connect(f"/v1/ws/{T}/{S}?token={ws_client.token}") as ws:
        ws.receive_json()
        ws_client.post("/events", json=make_event(payload={"marker": "shape"}))
        frame = _expect(
            ws,
            lambda f: f["type"] == "event"
            and f["event"]["payload"].get("marker") == "shape",
        )

    e = frame["event"]
    for key in ("seq", "eventId", "tenantId", "sessionId", "type", "payload",
                "occurredAt", "recordedAt"):
        assert key in e, f"missing canonical field {key}"
    for absent in ("event_id", "tenant_id", "occurred_at", "recorded_at"):
        assert absent not in e, f"snake_case {absent} leaked onto the wire"
    assert isinstance(e["occurredAt"], int)
    assert isinstance(e["recordedAt"], int)


# ── isolation ─────────────────────────────────────────────────────────────────


def test_a_socket_never_sees_another_tenants_events(ws_client: TestClient) -> None:
    """The worst failure this endpoint could have: one client watching another
    tenant's live feed.

    Written as a positive assertion rather than "wait and hope nothing comes" —
    the other tenant's event is posted FIRST, then our own as a sentinel. If
    isolation leaked, the foreign event would arrive before the sentinel.
    """
    with ws_client.websocket_connect(f"/v1/ws/{T}/{S}?token={ws_client.token}") as ws:
        ws.receive_json()

        # Two things matter about the ordering here, both learned the hard way:
        #
        # 1. The foreign event is inserted straight into the log rather than
        #    POSTed. Auth refuses a cross-tenant write with 403, so posting it
        #    would create nothing and this test would pass while testing air.
        # 2. It is inserted *after* the socket is connected. Inserting it before
        #    means the broadcast consumer has already passed it by the time
        #    anyone is listening — which also passes, and also proves nothing.
        #
        # Both were verified by breaking the hub on purpose and checking this
        # test fails.
        _insert_foreign_event()
        # Wait until the broadcast consumer has definitely handled it. Without
        # this the test is a race it usually wins for the wrong reason:
        # list_tenants() returns tenants sorted, so "mine" (t_ws) is broadcast
        # ahead of the foreign event (t_ws_other) in the same pass and arrives
        # first even when the hub is leaking.
        _wait_until_broadcast_passed(OTHER)

        ws_client.post("/events", json=make_event(payload={"marker": "mine"}))
        frame = _expect(ws, lambda f: f["type"] == "event")

    assert frame["event"]["tenantId"] == T
    assert frame["event"]["payload"]["marker"] == "mine"


def _wait_until_broadcast_passed(tenant_id: str, *, tries: int = 100) -> None:
    """Block until the broadcast consumer's cursor for `tenant_id` has moved.

    Polling the real cursor rather than sleeping a guessed interval: it is both
    faster in the common case and deterministic in the slow one.
    """
    import time

    from app import db as app_db
    from app import repository

    async def cursor() -> int:
        async with app_db.SessionLocal() as s:
            # consumer_cursor is under RLS too, so reading another tenant's
            # progress means declaring that tenant. An unscoped read would
            # quietly return 0 forever and this helper would time out.
            await app_db.scope_to_tenant(s, tenant_id)
            return await repository.get_cursor(
                s, consumer="broadcast", tenant_id=tenant_id
            )

    for _ in range(tries):
        if asyncio.run(cursor()) > 0:
            return
        time.sleep(0.05)
    raise AssertionError(f"broadcast consumer never processed {tenant_id}")


def _insert_foreign_event() -> None:
    """Put an event for another tenant into the log, bypassing the API."""
    from app import db as app_db
    from app import repository
    from app.schemas import EventIn

    async def go() -> None:
        async with app_db.SessionLocal() as s:
            # Acting as the other tenant, because RLS refuses the write
            # otherwise — the app role may only write the tenant it declares.
            await app_db.scope_to_tenant(s, OTHER)
            await repository.append_event(
                s,
                EventIn(
                    event_id=uuid.uuid4(), tenant_id=OTHER, session_id=S,
                    type="perception.detection",
                    payload={"marker": "foreign", "person_id": "P-9",
                             "bbox": [1, 1, 2, 2], "frame_width": 10, "frame_height": 10},
                    occurred_at=BASE,
                ),
            )
            await s.commit()

    asyncio.run(go())


def test_a_socket_never_sees_another_sessions_events(ws_client: TestClient) -> None:
    """Same technique, one level down. Two activations for the same tenant must
    not bleed into each other's live view."""
    with ws_client.websocket_connect(f"/v1/ws/{T}/{S}?token={ws_client.token}") as ws:
        ws.receive_json()

        ws_client.post("/events", json=make_event(session_id="s_elsewhere",
                                                  payload={"marker": "other_session"}))
        ws_client.post("/events", json=make_event(payload={"marker": "mine"}))

        frame = _expect(ws, lambda f: f["type"] == "event")

    assert frame["event"]["sessionId"] == S
    assert frame["event"]["payload"]["marker"] == "mine"


# ── robustness ────────────────────────────────────────────────────────────────


def test_a_disconnected_client_does_not_break_the_feed(ws_client: TestClient) -> None:
    """A closed tab must not take the live view down for everyone else in the
    booth — the hub drops dead sockets instead of raising."""
    with ws_client.websocket_connect(f"/v1/ws/{T}/{S}?token={ws_client.token}") as survivor:
        survivor.receive_json()

        with ws_client.websocket_connect(f"/v1/ws/{T}/{S}?token={ws_client.token}") as doomed:
            doomed.receive_json()
        # doomed is now closed

        ws_client.post("/events", json=make_event(payload={"marker": "after_close"}))
        frame = _expect(
            survivor,
            lambda f: f["type"] == "event"
            and f["event"]["payload"].get("marker") == "after_close",
        )

    assert frame["event"]["payload"]["marker"] == "after_close"
