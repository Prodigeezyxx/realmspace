"""
Authentication and tenant isolation, as executable claims.

The first block is Week 1 task 1.6's stated outcome — **"cross-tenant read
fails"** — written down as assertions. Before this, `GET /events?tenant_id=…`
would hand any caller any tenant's log, and the socket would stream any
activation to anyone who guessed the id.

Note what test_cannot_read_another_tenant does *not* do: it does not pass a
tenant and check for a 403. There is no parameter to pass. The isolation comes
from the parameter not existing, which is a stronger guarantee than a check
someone has to remember to write.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import tokens
from app.auth.models import ApiKey, AuthUser
from app.db import get_session
from app.main import app
from tests.conftest import TENANT

OTHER = "t_other"
BASE = dt.datetime(2026, 7, 31, 12, 0, 0, tzinfo=dt.timezone.utc)


def make_event(**overrides) -> dict:
    event = {
        "event_id": str(uuid.uuid4()),
        "tenant_id": TENANT,
        "session_id": "s_auth",
        "type": "perception.detection",
        "payload": {"person_id": "P-001", "bbox": [1, 1, 2, 2],
                    "frame_width": 10, "frame_height": 10},
        "occurred_at": BASE.isoformat(),
    }
    event.update(overrides)
    return event


def client_with(db_session: AsyncSession, **headers: str) -> AsyncClient:
    """A client carrying exactly the headers given — including none."""

    async def override_get_session():
        yield db_session
        await db_session.commit()

    app.dependency_overrides[get_session] = override_get_session
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=headers
    )


# ── the outcome: cross-tenant read fails ──────────────────────────────────────


async def test_no_credential_is_rejected(db_session: AsyncSession) -> None:
    """The finding this task exists to close: an open API on a venue LAN."""
    async with client_with(db_session) as anon:
        assert (await anon.get("/events")).status_code == 401
        assert (await anon.post("/events", json=make_event())).status_code == 401
    app.dependency_overrides.clear()


async def test_cannot_read_another_tenant(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """A caller sees their own tenant and only their own.

    The foreign event is written directly, because the API now refuses to write
    it — if this used the API the setup would fail silently and the assertion
    would pass while proving nothing.
    """
    from app import repository
    from app.schemas import EventIn
    from tests.conftest import as_tenant

    await client.post("/events", json=make_event())

    # Planting the foreign row now requires acting as that tenant — row-level
    # security rejects the write otherwise. That refusal is itself part of the
    # guarantee, so the test declares the tenant and switches back.
    await as_tenant(db_session, OTHER)
    await repository.append_event(
        db_session,
        EventIn(**{**make_event(), "tenant_id": OTHER, "event_id": uuid.uuid4()}),
    )
    await db_session.commit()
    await as_tenant(db_session, TENANT)

    rows = (await client.get("/events")).json()
    assert len(rows) == 1
    assert rows[0]["tenantId"] == TENANT

    # and there is no parameter that changes that — an ignored query string is
    # the point, not an oversight
    rows = (await client.get("/events", params={"tenant_id": OTHER})).json()
    assert all(r["tenantId"] == TENANT for r in rows)


async def test_cannot_write_for_another_tenant(client: AsyncClient) -> None:
    """403 rather than silently re-homing the event to the credential's tenant.

    A producer configured for the wrong tenant should find out on its first
    request, not have its events quietly filed elsewhere and discovered missing
    in a report weeks later.
    """
    r = await client.post("/events", json=make_event(tenant_id=OTHER))
    assert r.status_code == 403
    assert OTHER in r.text  # says which tenant it objected to


# ── credentials ───────────────────────────────────────────────────────────────


async def test_device_key_can_write_but_not_read(
    db_session: AsyncSession, device_key: str
) -> None:
    """Least privilege. A camera has no reason to read the log back, and a key
    taped inside a booth kit is the credential most likely to leave a venue."""
    async with client_with(db_session, **{"X-API-Key": device_key}) as device:
        assert (await device.post("/events", json=make_event())).status_code == 201
        assert (await device.get("/events")).status_code == 403
    app.dependency_overrides.clear()


async def test_tampered_token_is_rejected(
    db_session: AsyncSession, user_token: str
) -> None:
    """Alter the payload and the signature stops matching.

    The payload segment, not the last character of the signature. Base64url
    encodes 6 bits per character, so the final character of a segment carries
    "don't care" bits — several different characters decode to identical bytes,
    and flipping it is a no-op a good fraction of the time. This test was flaky
    for exactly that reason before it was pinned to a mutation that always
    changes the signed content.
    """
    header, payload, signature = user_token.split(".")
    tampered_payload = ("A" if payload[0] != "A" else "B") + payload[1:]
    bad = f"{header}.{tampered_payload}.{signature}"

    async with client_with(db_session, Authorization=f"Bearer {bad}") as c:
        assert (await c.get("/events")).status_code == 401
    app.dependency_overrides.clear()


async def test_token_signed_with_another_secret_is_rejected(
    db_session: AsyncSession, dev_user: AuthUser
) -> None:
    """The one that matters most: knowing the payload is not enough.

    An attacker who knows a user id and tenant can build a perfectly-shaped
    token. Without the signing secret it is worthless.
    """
    import jwt

    forged = jwt.encode(
        {
            "sub": dev_user.user_id,
            "tenant_id": OTHER,
            "role": "admin",
            "kind": "user",
            "exp": dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1),
        },
        "not-the-real-secret",
        algorithm="HS256",
    )
    async with client_with(db_session, Authorization=f"Bearer {forged}") as c:
        assert (await c.get("/events")).status_code == 401
    app.dependency_overrides.clear()


async def test_expired_token_is_rejected(
    db_session: AsyncSession, dev_user: AuthUser, monkeypatch
) -> None:
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "jwt_ttl_seconds", -1)  # already expired
    stale = tokens.issue_token(
        subject=dev_user.user_id, tenant_id=TENANT, role="admin"
    )
    async with client_with(db_session, Authorization=f"Bearer {stale}") as c:
        assert (await c.get("/events")).status_code == 401
    app.dependency_overrides.clear()


async def test_revoked_key_is_rejected(
    db_session: AsyncSession, device_key: str
) -> None:
    from sqlalchemy import select

    row = (
        await db_session.execute(
            select(ApiKey).where(ApiKey.key_hash == tokens.hash_api_key(device_key))
        )
    ).scalar_one()
    row.revoked_at = dt.datetime.now(dt.timezone.utc)
    await db_session.commit()

    async with client_with(db_session, **{"X-API-Key": device_key}) as device:
        assert (await device.post("/events", json=make_event())).status_code == 401
    app.dependency_overrides.clear()


async def test_deleted_user_cannot_use_an_unexpired_token(
    db_session: AsyncSession, dev_user: AuthUser, user_token: str
) -> None:
    """Removing someone takes effect immediately, not whenever their token runs
    out. That is why the user row is checked even though the claims are signed."""
    await db_session.delete(dev_user)
    await db_session.commit()

    async with client_with(db_session, Authorization=f"Bearer {user_token}") as c:
        assert (await c.get("/events")).status_code == 401
    app.dependency_overrides.clear()


# ── endpoints ─────────────────────────────────────────────────────────────────


async def test_health_needs_no_credentials(db_session: AsyncSession) -> None:
    """Liveness probes and the operator's first "is it up?" must not need a
    token — an auth failure would be indistinguishable from a dead process."""
    async with client_with(db_session) as anon:
        assert (await anon.get("/health")).status_code == 200
    app.dependency_overrides.clear()


async def test_token_endpoint_issues_a_working_credential(
    db_session: AsyncSession, dev_user: AuthUser
) -> None:
    async with client_with(db_session) as anon:
        r = await anon.post("/v1/auth/token", json={"email": dev_user.email})
        assert r.status_code == 200
        body = r.json()
        assert body["tenantId"] == TENANT and body["tokenType"] == "Bearer"

    async with client_with(
        db_session, Authorization=f"Bearer {body['accessToken']}"
    ) as c:
        assert (await c.get("/events")).status_code == 200
    app.dependency_overrides.clear()


async def test_unknown_email_gets_401_not_404_from_token(
    db_session: AsyncSession,
) -> None:
    """404 would confirm which addresses have accounts — a free user-enumeration
    oracle on an unauthenticated endpoint."""
    async with client_with(db_session) as anon:
        r = await anon.post("/v1/auth/token", json={"email": "nobody@example.com"})
        assert r.status_code == 401
    app.dependency_overrides.clear()


async def test_resolve_matches_the_other_tracks_shape(
    db_session: AsyncSession, dev_user: AuthUser
) -> None:
    """Contract parity so POD 3 writes one integration, not one per backend."""
    async with client_with(db_session) as anon:
        body = (
            await anon.get("/v1/auth/resolve", params={"email": dev_user.email})
        ).json()
    assert set(body) == {"userId", "email", "displayName", "orgId", "role"}
    assert body["orgId"] == TENANT
    app.dependency_overrides.clear()


@pytest.mark.parametrize("role", ["admin", "operator", "analyst", "viewer"])
async def test_every_documented_role_can_authenticate(
    db_session: AsyncSession, role: str
) -> None:
    """multi-tenant.md §3 lists four roles. All four must work — per-role
    permissions beyond read/write are a later item, but a role the system
    cannot even authenticate is a broken doc."""
    user = AuthUser(
        user_id=f"u_{role}", email=f"{role}@floats.demo", display_name=role,
        tenant_id=TENANT, role=role,
    )
    db_session.add(user)
    await db_session.commit()

    token = tokens.issue_token(subject=user.user_id, tenant_id=TENANT, role=role)
    async with client_with(db_session, Authorization=f"Bearer {token}") as c:
        assert (await c.get("/events")).status_code == 200
    app.dependency_overrides.clear()


async def test_health_is_503_when_degraded(db_session: AsyncSession, monkeypatch) -> None:
    """A degraded backend must fail the status code, not just say so in the body.

    Docker's HEALTHCHECK and compose's `depends_on: service_healthy` decide
    purely on the status code. This returned 200 while reporting
    `"status": "degraded"`, so a container with a dead store or a crashed
    consumer advertised itself as healthy — hiding exactly the silent failure
    this endpoint exists to surface.
    """
    from app import main

    class DeadTask:
        def get_name(self) -> str:
            return "consumer:tracker"

        def done(self) -> bool:
            return True

    monkeypatch.setattr(main, "_consumer_tasks", [DeadTask()])

    async with client_with(db_session) as anon:
        r = await anon.get("/health")

    assert r.status_code == 503, "a degraded backend reported itself healthy"
    assert r.json()["status"] == "degraded"
    assert r.json()["consumers"]["tracker"] == "stopped"
    app.dependency_overrides.clear()
