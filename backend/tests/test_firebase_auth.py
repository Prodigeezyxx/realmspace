"""
The door that used to be open.

`POST /v1/auth/token` took an email address and issued that user's token. Its own
docstring was candid about it — *"anyone who knows an address can get that user's
token"* — and it was the only real authentication hole in the system: every other
endpoint verified our own signed token correctly.

The forgeries below are the point of this file. Each one is a way somebody could
have got a token they were not entitled to, and each is refused with a reason a
person could act on rather than a flat "auth failed".

Firebase's signing key is Google's; these tests generate their own RSA keypair
and stand in for it, which is the only seam that needs faking. Everything else —
the audience check, the issuer check, the algorithm list, the expiry — runs
exactly as it does in production.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import firebase
from app.auth.models import AuthUser
from app.config import get_settings
from app.db import get_session
from app.main import app

PROJECT = "realmspace-test"
EMAIL = "jules@example.com"
TENANT = "t_floats"


@pytest.fixture(scope="module")
def keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


@pytest.fixture
def signing_key(keypair, monkeypatch):
    """Stand in for Google's published key.

    The only thing faked. `PyJWKClient` fetches Google's JWKS by kid; here it
    hands back the test key, and every check after that is the real one.
    """
    _, public = keypair

    class _Key:
        key = public

    class _Client:
        def get_signing_key_from_jwt(self, token):  # noqa: ANN001
            return _Key()

    monkeypatch.setattr(firebase, "_client", lambda: _Client())


def sign(keypair, **over) -> str:
    private, _ = keypair
    now = dt.datetime.now(dt.timezone.utc)
    claims = {
        "iss": f"https://securetoken.google.com/{PROJECT}",
        "aud": PROJECT,
        "sub": "firebase-uid-1",
        "iat": int(now.timestamp()),
        "exp": int((now + dt.timedelta(hours=1)).timestamp()),
        "email": EMAIL,
        "email_verified": True,
    }
    claims.update(over)
    algorithm = over.pop("_alg", "RS256")
    return jwt.encode(claims, private, algorithm=algorithm)


@pytest.fixture
def firebase_configured(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "firebase_project_id", PROJECT)
    return settings


@pytest.fixture
async def known_user(db_session: AsyncSession) -> None:
    db_session.add(
        AuthUser(
            user_id="u_jules",
            email=EMAIL,
            display_name="Jules",
            tenant_id=TENANT,
            role="analyst",
        )
    )
    await db_session.commit()


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


# ── the happy path, so the refusals below mean something ─────────────────────


async def test_a_verified_sign_in_gets_a_token(
    client: AsyncClient, keypair, signing_key, firebase_configured, known_user
) -> None:
    response = await client.post("/v1/auth/token", json={"idToken": sign(keypair)})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tenantId"] == TENANT
    # The role travels back so the browser can stop offering screens the token
    # will refuse. The backend remains the only gate.
    assert body["role"] == "analyst"


# ── the forgeries ────────────────────────────────────────────────────────────


async def test_a_token_signed_with_another_key_is_refused(
    client: AsyncClient, signing_key, firebase_configured, known_user
) -> None:
    """The base case: anybody can mint a JWT. Only Google can sign this one."""
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode(
        {
            "iss": f"https://securetoken.google.com/{PROJECT}",
            "aud": PROJECT,
            "sub": "whoever",
            "iat": 1,
            "exp": 9_999_999_999,
            "email": EMAIL,
            "email_verified": True,
        },
        attacker,
        algorithm="RS256",
    )

    response = await client.post("/v1/auth/token", json={"idToken": forged})
    assert response.status_code == 401


async def test_an_unsigned_token_is_refused(
    client: AsyncClient, signing_key, firebase_configured, known_user
) -> None:
    """`alg: none` — the oldest JWT forgery there is.

    The defence is the hard-coded single-element algorithm list in
    `auth/firebase.py`, and this is the test that says so.
    """
    forged = jwt.encode(
        {
            "iss": f"https://securetoken.google.com/{PROJECT}",
            "aud": PROJECT,
            "sub": "whoever",
            "iat": 1,
            "exp": 9_999_999_999,
            "email": EMAIL,
            "email_verified": True,
        },
        key="",
        algorithm="none",
    )

    response = await client.post("/v1/auth/token", json={"idToken": forged})
    assert response.status_code == 401


async def test_a_token_for_another_firebase_project_is_refused(
    client: AsyncClient, keypair, signing_key, firebase_configured, known_user
) -> None:
    """Firebase is a shared service. Google signs everybody's tokens, so
    "signed by Google" only means "signed for us" because of this check —
    otherwise anyone with their own Firebase project could mint our tokens.

    Only the audience is wrong here. A real cross-project token would fail the
    issuer check too, and PyJWT reaches that one first — so testing both at once
    would prove the issuer check twice and the audience check never.
    """
    response = await client.post(
        "/v1/auth/token",
        json={"idToken": sign(keypair, aud="somebody-elses-project")},
    )
    assert response.status_code == 401
    assert "different Firebase project" in response.json()["detail"]


async def test_a_token_google_signed_for_something_else_is_refused(
    client: AsyncClient, keypair, signing_key, firebase_configured, known_user
) -> None:
    """Google signs more than Firebase ID tokens. The issuer check is what rules
    the rest of them out."""
    response = await client.post(
        "/v1/auth/token",
        json={"idToken": sign(keypair, iss="https://accounts.google.com")},
    )
    assert response.status_code == 401
    assert "not issued by Firebase" in response.json()["detail"]


async def test_an_expired_sign_in_is_refused_with_something_to_do(
    client: AsyncClient, keypair, signing_key, firebase_configured, known_user
) -> None:
    past = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)
    response = await client.post(
        "/v1/auth/token",
        json={"idToken": sign(keypair, exp=int(past.timestamp()))},
    )
    assert response.status_code == 401
    assert "sign in again" in response.json()["detail"]


async def test_an_unverified_email_gets_nothing(
    client: AsyncClient, keypair, signing_key, firebase_configured, known_user
) -> None:
    """Firebase issues a working token for an email/password account before the
    address is proven. `auth_user` maps an address to a tenant and a role, so
    accepting one means signing up as somebody else's address and receiving
    their organisation — a weaker version of the hole being closed.
    """
    response = await client.post(
        "/v1/auth/token",
        json={"idToken": sign(keypair, email_verified=False)},
    )
    assert response.status_code == 403
    assert "not been confirmed" in response.json()["detail"]


async def test_a_sign_in_with_no_email_is_refused(
    client: AsyncClient, keypair, signing_key, firebase_configured, known_user
) -> None:
    """Anonymous and phone-number sign-ins are real Firebase identities and
    cannot be resolved to an organisation here."""
    response = await client.post(
        "/v1/auth/token", json={"idToken": sign(keypair, email=None)}
    )
    assert response.status_code == 401
    assert "no email address" in response.json()["detail"]


async def test_a_verified_stranger_gets_401_not_404(
    client: AsyncClient, keypair, signing_key, firebase_configured
) -> None:
    """A real Google account with no `auth_user` row. 404 would confirm which
    addresses have accounts — a free user-enumeration oracle."""
    response = await client.post(
        "/v1/auth/token",
        json={"idToken": sign(keypair, email="nobody@example.com")},
    )
    assert response.status_code == 401
    assert "unknown user" in response.json()["detail"]


# ── the trusted-email path is closed where Firebase is configured ────────────


async def test_an_email_body_is_refused_once_firebase_is_configured(
    client: AsyncClient, signing_key, firebase_configured, known_user
) -> None:
    """The old hole, tried against a deployment that has closed it."""
    response = await client.post("/v1/auth/token", json={"email": EMAIL})
    assert response.status_code == 422
    assert "idToken" in response.json()["detail"]


async def test_an_unconfigured_deployment_refuses_outside_local(
    client: AsyncClient, monkeypatch, known_user
) -> None:
    """Fails closed at the door.

    A deployment that forgets `firebase_project_id` would otherwise still be
    running the trusted-email path, and nobody outside would find out.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "firebase_project_id", None)
    monkeypatch.setattr(settings, "env", "production")

    response = await client.post("/v1/auth/token", json={"email": EMAIL})
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]


async def test_local_development_still_works(
    client: AsyncClient, monkeypatch, known_user
) -> None:
    """The one environment where refusing would mean nobody can run the stack."""
    settings = get_settings()
    monkeypatch.setattr(settings, "firebase_project_id", None)
    monkeypatch.setattr(settings, "env", "local")

    response = await client.post("/v1/auth/token", json={"email": EMAIL})
    assert response.status_code == 200
    assert response.json()["tenantId"] == TENANT


def test_the_local_path_says_what_it_is(monkeypatch, caplog) -> None:
    """It warns on every issue, so a developer reading the log can tell that
    this is not authentication.

    Called directly rather than through the ASGI client: the warning is a
    property of the decision function, and testing it there is both narrower and
    not at the mercy of how logs propagate through the app under test.
    """
    import logging

    from app.routers.auth import TokenRequest, _verified_email

    settings = get_settings()
    monkeypatch.setattr(settings, "firebase_project_id", None)
    monkeypatch.setattr(settings, "env", "local")

    with caplog.at_level(logging.WARNING):
        email = _verified_email(TokenRequest(email=EMAIL), settings)

    assert email == EMAIL
    assert "anyone who knows an address" in caplog.text


def test_the_unconfigured_refusal_is_logged_as_an_error(monkeypatch, caplog) -> None:
    """A deployment that forgot the project id should be findable from its own
    logs, not only from a 503 somebody happened to notice."""
    import logging

    from fastapi import HTTPException

    from app.routers.auth import TokenRequest, _verified_email

    settings = get_settings()
    monkeypatch.setattr(settings, "firebase_project_id", None)
    monkeypatch.setattr(settings, "env", "production")

    with caplog.at_level(logging.ERROR):
        with pytest.raises(HTTPException) as raised:
            _verified_email(TokenRequest(email=EMAIL), settings)

    assert raised.value.status_code == 503
    assert "Authentication is not configured" in caplog.text


async def test_an_unreachable_key_service_refuses_and_says_nothing_useful(
    client: AsyncClient, keypair, monkeypatch, firebase_configured, known_user, caplog
) -> None:
    """Found by running it: on a host with no CA bundle the JWKS fetch fails, and
    the raw `urlopen` error — URL, TLS diagnostics and all — was going back to
    the caller.

    Two properties here. It **refuses**: a verifier that trusted a token because
    it could not check it would be worse than none. And it refuses without
    describing our infrastructure to whoever is asking, while still putting the
    real reason in the log where an operator will look.
    """
    import logging

    class _Broken:
        def get_signing_key_from_jwt(self, token):  # noqa: ANN001
            raise OSError("SSL: CERTIFICATE_VERIFY_FAILED for https://internal.example")

    monkeypatch.setattr(firebase, "_client", lambda: _Broken())

    with caplog.at_level(logging.WARNING):
        response = await client.post("/v1/auth/token", json={"idToken": sign(keypair)})

    assert response.status_code == 401
    detail = response.json()["detail"]
    assert "try again in a moment" in detail
    assert "CERTIFICATE" not in detail
    assert "internal.example" not in detail
    # The operator still gets the real reason.
    assert "CERTIFICATE_VERIFY_FAILED" in caplog.text
