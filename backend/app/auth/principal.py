"""
Who is calling, and what they are allowed to touch.

This module exists to make one sentence true: **`tenant_id` is derived from a
verified credential, never supplied by the caller.**

Before this, `GET /events?tenant_id=…` would hand you any tenant's log for the
asking. The fix is not to check the parameter — it is to delete it, so there is
nothing left to check. Every handler now reads `principal.tenant_id`.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import tokens
from app.auth.models import DEVICE_ROLE, ApiKey, AuthUser
from app.db import get_session


@dataclass(frozen=True)
class Principal:
    """A verified caller. Frozen because nothing downstream should be able to
    edit the tenant it was told to work in."""

    kind: Literal["user", "device"]
    subject: str      # user_id, or key_id
    tenant_id: str    # verified — the whole point
    role: str

    @property
    def may_write(self) -> bool:
        return True  # devices exist to write; every human role may too

    @property
    def may_read(self) -> bool:
        """Devices are write-only.

        A camera has no business reading back the event log, and a key taped
        inside a booth kit is the credential most likely to walk out of a venue.
        Least privilege costs nothing here.
        """
        return self.kind == "user"


UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="missing or invalid credentials",
    # Tells a browser client which scheme to retry with; also what the spec says
    # a 401 must carry.
    headers={"WWW-Authenticate": "Bearer"},
)


async def _principal_from_api_key(session: AsyncSession, raw_key: str) -> Principal:
    row = (
        await session.execute(
            select(ApiKey).where(ApiKey.key_hash == tokens.hash_api_key(raw_key))
        )
    ).scalar_one_or_none()

    # Look up by hash, then compare in constant time. The lookup alone would be
    # enough here, but comparing this way keeps the property true if the storage
    # ever changes to something not uniquely indexed.
    if row is None or not tokens.api_keys_match(
        tokens.hash_api_key(raw_key), row.key_hash
    ):
        raise UNAUTHENTICATED
    if row.revoked_at is not None:
        raise UNAUTHENTICATED

    return Principal(
        kind="device", subject=row.key_id, tenant_id=row.tenant_id, role=DEVICE_ROLE
    )


async def _principal_from_token(session: AsyncSession, raw_token: str) -> Principal:
    try:
        claims = tokens.verify_token(raw_token)
    except tokens.AuthError:
        raise UNAUTHENTICATED from None

    # The tenant and role come from the signed claims, not from a fresh lookup:
    # a token is a statement this server already made. The user row is checked
    # only to confirm the account still exists, so deleting a user takes effect
    # before their token expires.
    user = (
        await session.execute(
            select(AuthUser).where(AuthUser.user_id == claims.get("sub", ""))
        )
    ).scalar_one_or_none()
    if user is None:
        raise UNAUTHENTICATED

    return Principal(
        kind="user",
        subject=user.user_id,
        tenant_id=claims["tenant_id"],
        role=claims.get("role", user.role),
    )


async def get_principal(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> Principal:
    """The dependency every protected endpoint takes.

    Accepts either scheme. A device presenting a key and a human presenting a
    bearer token both end up as a Principal with a verified tenant, so handlers
    never need to care which arrived.
    """
    if x_api_key:
        return await _principal_from_api_key(session, x_api_key)

    if authorization:
        scheme, _, credential = authorization.partition(" ")
        if scheme.lower() == "bearer" and credential:
            return await _principal_from_token(session, credential)

    raise UNAUTHENTICATED


async def require_reader(
    principal: Principal = Depends(get_principal),
) -> Principal:
    """Read endpoints. Rejects device keys."""
    if not principal.may_read:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="device credentials are write-only",
        )
    return principal


async def principal_for_socket(
    session: AsyncSession, token: str | None, path_tenant_id: str
) -> Principal:
    """Same checks, for a WebSocket.

    A browser cannot set headers on a WebSocket handshake, so the token arrives
    in the query string. That is the standard workaround and it has a real cost:
    query strings are logged by proxies and servers in a way headers are not, so
    a token in a URL is a token in somebody's log. Mitigated by the short TTL
    (`jwt_ttl_seconds`, one hour by default) rather than pretended away.

    The tenant in the path is kept for contract parity with the other track, but
    it is checked against the token rather than trusted — otherwise the socket
    would be exactly the hole the HTTP endpoints just closed.
    """
    if not token:
        raise UNAUTHENTICATED
    principal = await _principal_from_token(session, token)
    if principal.tenant_id != path_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="tenant mismatch"
        )
    return principal


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)
