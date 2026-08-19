"""
Getting a credential.

Two endpoints:

  POST /v1/auth/token     email → signed JWT carrying tenant + role
  GET  /v1/auth/resolve   email → who they are (the other track's shape)

`resolve` exists purely for contract parity — the `postgres-track` has it and
POD 3 should not need a different integration per backend. It is a *lookup*: it
tells you what role an email has, and grants nothing. `token` is the one that
issues something you can act with, and it is what this track adds.

## What is exchanged is now a verified identity *(closed 2026-08-18)*

This endpoint used to take an email and trust it, which is not authentication —
anyone who knew an address could get that user's token. The fix is the one this
file predicted: **the exchange point stays, and the thing exchanged becomes a
verified Firebase ID token.** The dashboard already signs users in
(`dashboard/src/lib/firebase/auth-actions.ts`); that identity was simply never
carried to the backend. Every other endpoint verifies our own signed token and
did not change — `app/auth/tokens.py` is untouched, because what it signs was
never the problem.

## An unconfigured deployment refuses rather than degrades

The same rule `app/secrets.py` follows about its encryption key:

| `firebase_project_id` | `env` | Result |
|---|---|---|
| set | any | only a verified `idToken` is accepted |
| unset | `local` | `email` accepted, loudly, for development |
| unset | anything else | **503** — nothing is issued |

The last row is the one that matters. A deployment that forgets the project id
fails closed at the door, rather than silently shipping the hole this change
exists to close. The local exception is scoped to the one environment where the
alternative is nobody being able to run the stack.

## An unverified email gets nothing

Firebase issues a working ID token for an email/password account before the
address has been proven. `auth_user` maps an address to a tenant and a role, so
accepting `email_verified: false` would leave a weaker version of the same hole:
sign up as somebody else's address, receive their organisation. Refused with a
sentence a UI can show.

## Unknown users get 401, not 404

Unchanged, and worth restating beside the rest: a 404 would confirm which
addresses have accounts, which is a free user-enumeration oracle on an
unauthenticated endpoint.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import firebase, tokens
from app.auth.models import AuthUser
from app.db import get_session

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class TokenRequest(BaseModel):
    """A Firebase ID token, or — only in local development — an email.

    Both optional in the type and exactly one required in practice, because
    which one is required depends on the deployment rather than on the caller.
    `issue` says which, with the reason.
    """

    id_token: str | None = Field(default=None, alias="idToken")
    #: Local development only. Ignored, and refused, wherever Firebase is
    #: configured — otherwise the trusted-email path would still be reachable on
    #: a deployment that had closed it.
    email: EmailStr | None = None


class TokenResponse(BaseModel):
    accessToken: str
    tokenType: str = "Bearer"
    expiresIn: int
    tenantId: str
    role: str


class ResolveResponse(BaseModel):
    """Field names match the other track's AuthResolveResponse exactly."""

    userId: str
    email: str
    displayName: str | None
    orgId: str
    role: str


async def _lookup(session: AsyncSession, email: str) -> AuthUser | None:
    return (
        await session.execute(select(AuthUser).where(AuthUser.email == email.lower()))
    ).scalar_one_or_none()


def _verified_email(body: TokenRequest, settings) -> str:
    """Whose address this is, proven — or a refusal saying why not.

    The whole of the authentication decision, in one place, so no caller can
    reach the issuing code below without passing through it.
    """
    if settings.firebase_project_id:
        if not body.id_token:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    "this deployment verifies Firebase sign-ins — send the "
                    "user's ID token as `idToken`"
                ),
            )
        try:
            identity = firebase.verify_id_token(
                body.id_token, project_id=settings.firebase_project_id
            )
        except firebase.FirebaseError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
            ) from exc

        if not identity.email_verified:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "that email address has not been confirmed yet — check your "
                    "inbox for the verification link, then sign in again"
                ),
            )
        return identity.email

    if settings.env != "local":
        # Fails closed, and loudly. A deployment that forgot the project id
        # would otherwise be running the hole this endpoint exists to have
        # closed, and nobody would find out from the outside.
        log.error(
            "refusing to issue a token: firebase_project_id is unset and env is "
            "%r. Authentication is not configured.",
            settings.env,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="authentication is not configured on this deployment",
        )

    if not body.email:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "no Firebase project is configured, so this local deployment "
                "expects `email`"
            ),
        )
    log.warning(
        "issuing a token for %s on an unverified email — local development only, "
        "and anyone who knows an address can do this",
        body.email,
    )
    return str(body.email)


@router.post("/token", response_model=TokenResponse)
async def issue(
    body: TokenRequest, session: AsyncSession = Depends(get_session)
) -> TokenResponse:
    from app.config import get_settings

    settings = get_settings()
    email = _verified_email(body, settings)

    user = await _lookup(session, email)
    if user is None:
        # 401 rather than 404. A 404 would confirm which addresses have accounts,
        # which is a free user-enumeration oracle on an unauthenticated endpoint.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="unknown user"
        )

    return TokenResponse(
        accessToken=tokens.issue_token(
            subject=user.user_id, tenant_id=user.tenant_id, role=user.role
        ),
        expiresIn=get_settings().jwt_ttl_seconds,
        tenantId=user.tenant_id,
        role=user.role,
    )


@router.get("/resolve", response_model=ResolveResponse)
async def resolve(
    email: str = Query(...), session: AsyncSession = Depends(get_session)
) -> ResolveResponse:
    """Resolve email → org → role. Grants nothing; `POST /token` does that.

    Unlike the other track's version this does **not** invent a viewer on the
    default tenant for an unknown address. Defaulting an unknown user into a
    real tenant is how someone ends up quietly holding a role nobody granted
    them; an unknown email is a 404 here.
    """
    user = await _lookup(session, email)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown user")

    return ResolveResponse(
        userId=user.user_id,
        email=user.email,
        displayName=user.display_name,
        orgId=user.tenant_id,
        role=user.role,
    )
