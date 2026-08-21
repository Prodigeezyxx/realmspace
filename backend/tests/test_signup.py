"""
Signup, and the three things it refuses to do.

`multi-tenant.md` §4 lists six onboarding steps. Steps 2–6 all existed — the
wizard, prefabs, the zone editor, integrations, consent config, run — while step
1, "Sign up → create Organization", was unbuilt. A user existed only if somebody
had run `python -m app.auth.seed` on the server, and the dashboard meanwhile
shipped a "Create your account" tab that made a Firebase identity and then hit
`401 unknown user` forever.

The load-bearing test is `test_signup_goes_through_the_same_gate_as_sign_in`.
This is an **unauthenticated** endpoint that hands out organisations, so the one
thing it must never grow is its own opinion about who somebody is. It calls the
same `_verified_email` as `POST /token`, which is why the 503 below is inherited
rather than reimplemented.

The other two refusals both protect somebody who is *already* a customer: an
address that signs up twice must not be moved out of its own organisation, and
nobody may sign up into an organisation that already exists.

Reuses the Firebase harness from `test_firebase_auth.py` — its own RSA keypair
standing in for Google's signing key, and every check after that the real one.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import AuthUser, Tenant
from app.auth.tokens import verify_token
from app.config import get_settings
from app.db import get_session
from app.main import app
from tests.test_firebase_auth import (  # noqa: F401 — fixtures used by name
    EMAIL,
    firebase_configured,
    keypair,
    sign,
    signing_key,
)


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def override() -> AsyncIterator[AsyncSession]:
        yield db_session
        await db_session.commit()

    app.dependency_overrides[get_session] = override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


async def signup(client: AsyncClient, keypair, *, email=EMAIL, org="Northwind"):
    return await client.post(
        "/v1/auth/signup",
        json={"idToken": sign(keypair, email=email), "orgName": org},
    )


# ── the dead end, closed ──────────────────────────────────────────────────────


async def test_a_verified_stranger_can_now_create_an_organisation(
    client: AsyncClient, keypair, signing_key, firebase_configured
) -> None:
    """The 401 `test_a_verified_stranger_gets_401_not_404` asserts is still
    correct for `/token`, and now has somewhere to go instead of nowhere."""
    before = await client.post("/v1/auth/token", json={"idToken": sign(keypair)})
    assert before.status_code == 401

    created = await signup(client, keypair)
    assert created.status_code == 201, created.text

    after = await client.post("/v1/auth/token", json={"idToken": sign(keypair)})
    assert after.status_code == 200
    assert after.json()["tenantId"] == created.json()["tenantId"]


async def test_the_founder_is_an_admin(
    client: AsyncClient, keypair, signing_key, firebase_configured
) -> None:
    """Not configurable, and the reason is structural rather than generous.

    Every other role is unable to invite the second person — `multi-tenant.md`
    §3 puts users with Admin — so an organisation founded by an operator could
    never be joined by anybody, ever.
    """
    body = (await signup(client, keypair)).json()
    assert body["role"] == "admin"

    claims = verify_token(body["accessToken"])
    assert claims["role"] == "admin"
    assert claims["tenant_id"] == body["tenantId"]


async def test_it_returns_a_token_so_signup_is_one_round_trip(
    client: AsyncClient, keypair, signing_key, firebase_configured
) -> None:
    """Making the caller sign in again is a second chance for the same request
    to fail, on a path where the account now exists and they have been told
    nothing."""
    body = (await signup(client, keypair)).json()
    assert body["accessToken"]
    assert body["expiresIn"] == get_settings().jwt_ttl_seconds


async def test_the_organisation_is_in_the_registry_with_its_real_name(
    client: AsyncClient, keypair, signing_key, firebase_configured, db_session
) -> None:
    """Migration 0011's whole reason: a tenant used to be whatever string an
    `auth_user` row happened to carry, so nothing could show an operator what
    their organisation is called."""
    body = (await signup(client, keypair, org="Northwind Traders")).json()

    tenant = await db_session.get(Tenant, body["tenantId"])
    assert tenant is not None
    assert tenant.name == "Northwind Traders"
    assert tenant.created_by is not None


async def test_the_tenant_id_is_readable_and_still_unique(
    client: AsyncClient, keypair, signing_key, firebase_configured
) -> None:
    """`t_northwind_ab12cd`, not a UUID.

    These ids show up in psql, in /ops, in the WebSocket path and in every
    support conversation. The random tail is not decoration: two organisations
    can legitimately share a name, and the alternative is telling a real
    customer their company name is taken — by somebody else.
    """
    first = (await signup(client, keypair, org="Northwind")).json()["tenantId"]
    second = (
        await signup(client, keypair, email="other@example.com", org="Northwind")
    ).json()["tenantId"]

    assert first.startswith("t_northwind_")
    assert second.startswith("t_northwind_")
    assert first != second


async def test_a_name_with_no_ascii_still_gets_an_organisation(
    client: AsyncClient, keypair, signing_key, firebase_configured
) -> None:
    """An organisation is not obliged to be nameable in ASCII to have an account."""
    body = (await signup(client, keypair, org="株式会社テスト")).json()
    assert body["tenantId"].startswith("t_")
    assert body["orgName"] == "株式会社テスト"


# ── the three refusals ────────────────────────────────────────────────────────


async def test_signup_goes_through_the_same_gate_as_sign_in(
    client: AsyncClient, monkeypatch
) -> None:
    """**The one that matters.** An unauthenticated endpoint that hands out
    organisations must not have its own idea of who somebody is.

    Asserted from this caller rather than trusted from `/token`'s tests: the
    failure being guarded against is somebody adding a shortcut here later, and
    that would leave `/token`'s tests green.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "firebase_project_id", None)
    monkeypatch.setattr(settings, "env", "production")

    response = await client.post(
        "/v1/auth/signup", json={"email": EMAIL, "orgName": "Northwind"}
    )
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]


async def test_an_unverified_email_cannot_found_an_organisation(
    client: AsyncClient, keypair, signing_key, firebase_configured
) -> None:
    """Firebase issues a working token before an address is proven.

    Accepting one here is worse than accepting it at sign-in: it does not just
    get you into somebody's organisation, it lets you found one in their name.
    """
    response = await client.post(
        "/v1/auth/signup",
        json={
            "idToken": sign(keypair, email_verified=False),
            "orgName": "Northwind",
        },
    )
    assert response.status_code == 403


async def test_signing_up_twice_does_not_move_you_out_of_your_own_org(
    client: AsyncClient, keypair, signing_key, firebase_configured, db_session
) -> None:
    """`auth_user.email` is globally unique and a user has one tenant.

    Written as an upsert, a second signup would not create a second account — it
    would reassign the existing one, taking that person out of the organisation
    they built, with its sessions, leads and integrations left behind.
    """
    first = (await signup(client, keypair, org="Northwind")).json()

    again = await signup(client, keypair, org="Somewhere Else")
    assert again.status_code == 409
    assert "already belongs" in again.json()["detail"]

    user = (
        await db_session.execute(select(AuthUser).where(AuthUser.email == EMAIL))
    ).scalar_one()
    assert user.tenant_id == first["tenantId"]


async def test_an_existing_member_cannot_sign_up_a_second_organisation(
    client: AsyncClient, keypair, signing_key, firebase_configured, db_session
) -> None:
    """Someone invited as a viewer is already somebody's user.

    Signup must not be the back door out of an organisation that a `DELETE
    /v1/users/{id}` would otherwise be the only way through — nor a way to hold
    two, which the one-tenant-per-user model cannot express.
    """
    db_session.add(
        AuthUser(
            user_id="u_invited",
            email=EMAIL,
            display_name=None,
            tenant_id="t_someone_else",
            role="viewer",
        )
    )
    await db_session.commit()

    response = await signup(client, keypair)
    assert response.status_code == 409

    user = (
        await db_session.execute(select(AuthUser).where(AuthUser.email == EMAIL))
    ).scalar_one()
    assert user.tenant_id == "t_someone_else"
    assert user.role == "viewer"


async def test_an_org_name_is_required(
    client: AsyncClient, keypair, signing_key, firebase_configured
) -> None:
    """An organisation with no name shows up as a blank on every screen that
    names one, and there is no later prompt that would fill it in."""
    response = await client.post(
        "/v1/auth/signup", json={"idToken": sign(keypair), "orgName": "  "}
    )
    assert response.status_code == 422
