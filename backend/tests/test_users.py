"""
Who is in an organisation, and the two ways of getting that wrong.

Signup deliberately only ever creates a one-person organisation — it refuses to
join an existing tenant, because an address quietly filed into somebody's org by
a matching domain is exactly what `/v1/auth/resolve` refuses to do. So this
endpoint is the *only* way an organisation ever gets a second person, and
`multi-tenant.md` §4's onboarding stops at one seat without it.

Two tests here guard against changes that would look like improvements.

`test_inviting_someone_elses_member_is_refused_not_moved` — `auth_user.email` is
globally unique and a user has exactly one tenant, so "adding" an address that
already belongs somewhere would silently *remove* that person from their own
organisation. Data loss dressed as an invitation, performed by an admin who
typed a plausible address.

`test_the_last_admin_cannot_be_removed` — an organisation with no admin can never
invite anybody, connect an integration, or honour a GDPR Article 17 erasure. It
is bricked permanently by one successful-looking request, with no recovery short
of psql.

The third thing asserted here is a negative: the response says **no email was
sent**, in the payload and not only in prose, because there is no email provider
in this repo and a UI that renders "invitation sent" is the lying button one
layer down.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import AuthUser
from app.auth.tokens import issue_token
from app.db import get_session
from app.main import app
from tests.conftest import as_tenant

T = "t_test"
OTHER = "t_test_other"


@pytest.fixture(autouse=True)
async def _scope_to_test_tenant(db_session: AsyncSession) -> AsyncIterator[None]:
    await as_tenant(db_session, T)
    yield


def _client(db_session: AsyncSession, token: str) -> AsyncClient:
    async def override() -> AsyncIterator[AsyncSession]:
        yield db_session
        await db_session.commit()

    app.dependency_overrides[get_session] = override
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


async def _user(
    db_session: AsyncSession, user_id: str, role: str, tenant: str = T
) -> str:
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
async def admin(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    token = await _user(db_session, "u_admin", "admin")
    async with _client(db_session, token) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def operator(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    token = await _user(db_session, "u_op", "operator")
    async with _client(db_session, token) as ac:
        yield ac
    app.dependency_overrides.clear()


# ── the seat ──────────────────────────────────────────────────────────────────


async def test_an_invited_address_can_sign_in_where_it_could_not_before(
    admin: AsyncClient, db_session: AsyncSession
) -> None:
    """The whole mechanism: a Firebase identity is only half a login here.

    The address still has to map to a tenant and a role, and until this row
    exists `POST /v1/auth/token` answers `401 unknown user` no matter how
    correctly somebody signs in with Google.
    """
    response = await admin.post(
        "/v1/users", json={"email": "client@brand.example", "role": "viewer"}
    )
    assert response.status_code == 201, response.text

    row = (
        await db_session.execute(
            select(AuthUser).where(AuthUser.email == "client@brand.example")
        )
    ).scalar_one()
    assert row.tenant_id == T
    assert row.role == "viewer"


async def test_it_says_plainly_that_no_email_was_sent(admin: AsyncClient) -> None:
    """There is no email provider — the absence Phase 5's SDR sits behind, where
    the decision was that it "drafts and does not send".

    `emailSent` is a field rather than only prose so a UI cannot render
    "invitation sent" by assuming, and so the day a provider exists this becomes
    true in one place instead of every surface being corrected.
    """
    body = (
        await admin.post("/v1/users", json={"email": "client@brand.example"})
    ).json()
    assert body["emailSent"] is False
    assert "No email was sent" in body["detail"]


async def test_the_default_role_is_viewer(admin: AsyncClient) -> None:
    """The report's "Share with client" is the commonest caller, and §3 defines
    Viewer as "the sponsor or brand stakeholder". Defaulting to anything more is
    a permission granted by omission."""
    body = (await admin.post("/v1/users", json={"email": "c@brand.example"})).json()
    assert body["role"] == "viewer"


async def test_the_team_list_is_this_organisation_only(
    admin: AsyncClient, db_session: AsyncSession
) -> None:
    await _user(db_session, "u_outsider", "admin", tenant=OTHER)
    await as_tenant(db_session, T)

    emails = {u["email"] for u in (await admin.get("/v1/users")).json()}
    assert "u_admin@floats.demo" in emails
    assert "u_outsider@floats.demo" not in emails


# ── the two refusals ──────────────────────────────────────────────────────────


async def test_inviting_someone_elses_member_is_refused_not_moved(
    admin: AsyncClient, db_session: AsyncSession
) -> None:
    """**The one that would look like a feature.**

    An upsert here does not add a person to two organisations — the model has
    one tenant per user — it takes them out of theirs, leaving their sessions,
    leads and integrations behind in an org they can no longer reach.
    """
    await _user(db_session, "u_theirs", "operator", tenant=OTHER)
    await as_tenant(db_session, T)

    response = await admin.post("/v1/users", json={"email": "u_theirs@floats.demo"})
    assert response.status_code == 409
    assert "different organisation" in response.json()["detail"]

    row = (
        await db_session.execute(
            select(AuthUser).where(AuthUser.email == "u_theirs@floats.demo")
        )
    ).scalar_one()
    assert row.tenant_id == OTHER
    assert row.role == "operator"


async def test_the_last_admin_cannot_be_removed(admin: AsyncClient) -> None:
    """An org with no admin cannot invite, integrate, or honour an erasure.

    Bricked permanently by one request that returns 204.
    """
    response = await admin.delete("/v1/users/u_admin")
    assert response.status_code == 409
    assert "only admin" in response.json()["detail"]


async def test_an_admin_can_be_removed_once_there_is_another(
    admin: AsyncClient, db_session: AsyncSession
) -> None:
    """The guard is about the last one, not about admins."""
    await admin.post("/v1/users", json={"email": "second@floats.demo", "role": "admin"})
    assert (await admin.delete("/v1/users/u_admin")).status_code == 204


async def test_re_inviting_an_existing_member_changes_nothing(
    admin: AsyncClient,
) -> None:
    """Idempotent rather than a 409: with no email to check, the realistic cause
    is an admin unsure whether the first attempt landed.

    It does **not** update the role — that is a different intent, and letting it
    would mean a mistyped invite quietly demoting a colleague.
    """
    await admin.post("/v1/users", json={"email": "c@brand.example", "role": "analyst"})
    again = await admin.post(
        "/v1/users", json={"email": "c@brand.example", "role": "viewer"}
    )

    assert again.status_code == 201
    assert again.json()["role"] == "analyst"
    assert "Nothing changed" in again.json()["detail"]


async def test_an_unknown_role_is_refused_with_the_four(admin: AsyncClient) -> None:
    response = await admin.post(
        "/v1/users", json={"email": "c@brand.example", "role": "owner"}
    )
    assert response.status_code == 422
    assert "admin" in response.json()["detail"]


async def test_a_device_role_cannot_be_given_to_a_person(admin: AsyncClient) -> None:
    """`producer` is deliberately not in `USER_ROLES` — `auth/models.py` says it
    "must never be assignable to a person by a typo"."""
    response = await admin.post(
        "/v1/users", json={"email": "c@brand.example", "role": "producer"}
    )
    assert response.status_code == 422


# ── who may do this at all ────────────────────────────────────────────────────


async def test_an_operator_cannot_manage_users(operator: AsyncClient) -> None:
    """§3 puts users with Admin, beside integrations, and for the same reason:
    an operator decides what this booth does in this room, an admin decides who
    can see the room at all."""
    assert (await operator.get("/v1/users")).status_code == 403
    assert (
        await operator.post("/v1/users", json={"email": "c@brand.example"})
    ).status_code == 403


async def test_removing_someone_elses_user_is_a_404(
    admin: AsyncClient, db_session: AsyncSession
) -> None:
    """404 rather than 403: confirming that a user id exists elsewhere is a fact
    this caller has no business learning."""
    await _user(db_session, "u_theirs", "viewer", tenant=OTHER)
    await as_tenant(db_session, T)

    assert (await admin.delete("/v1/users/u_theirs")).status_code == 404

    still = (
        await db_session.execute(select(AuthUser).where(AuthUser.user_id == "u_theirs"))
    ).scalar_one()
    assert still.tenant_id == OTHER
