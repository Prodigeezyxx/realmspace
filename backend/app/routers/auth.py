"""
Getting a credential.

Two endpoints:

  POST /v1/auth/token     email → signed JWT carrying tenant + role
  GET  /v1/auth/resolve   email → who they are (the other track's shape)

`resolve` exists purely for contract parity — the `postgres-track` has it and
POD 3 should not need a different integration per backend. It is a *lookup*: it
tells you what role an email has, and grants nothing. `token` is the one that
issues something you can act with, and it is what this track adds.

## Email is a placeholder for a real login, and says so

`POST /v1/auth/token` currently takes an email and trusts it, which is not
authentication — anyone who knows an address can get that user's token. It is
deliberately the same trust model the other track's `resolve` already has, so
neither is more exposed than the other, and it is scoped to local development.

The seam for fixing it is here and nowhere else: the exchange point stays, and
the thing exchanged becomes a verified Firebase ID token (the dashboard already
signs users in — `dashboard/src/lib/firebase/auth-actions.ts`). Every other
endpoint verifies our own signed token and does not change. Tracked as the
follow-up in `backend/README.md`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import tokens
from app.auth.models import AuthUser
from app.db import get_session

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class TokenRequest(BaseModel):
    email: EmailStr


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


@router.post("/token", response_model=TokenResponse)
async def issue(
    body: TokenRequest, session: AsyncSession = Depends(get_session)
) -> TokenResponse:
    from app.config import get_settings

    user = await _lookup(session, body.email)
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
