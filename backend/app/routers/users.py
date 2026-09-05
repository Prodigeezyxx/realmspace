"""
The people in an organisation — who is in it, adding one, removing one.

`multi-tenant.md` §3 puts users with Admin ("manage org, billing, integrations,
**users**"), so the whole router is behind `require_admin`.

## Why this exists beyond the obvious

Two things were waiting on it.

**Signup only ever creates a one-person organisation.** `POST /v1/auth/signup`
deliberately refuses to join an existing tenant — an address that matched a
domain and was quietly filed into somebody's organisation is exactly the failure
`/v1/auth/resolve` refuses to commit — so the *only* way an organisation ever
gets a second person is an admin who is already in it saying so. Without this
endpoint, §4's onboarding stops at one seat.

**The report's "Share with client" button had nothing to call.** §3 defines
Viewer as "read-only dashboard + report — the sponsor or brand stakeholder", so
sharing a report with a client *is* granting them a viewer seat. It is the same
call as invite, with the role fixed.

## It grants a seat; it does not send an email

There is no email provider in this repo — the same absence Phase 5's SDR sits
behind, where the decision recorded was that it "drafts and does not send".
Adding one here would need a suppression list, a bounce story and an audit of
who pressed the button, and half of that behind a button labelled "Invite" would
be worse than none.

So the response says what actually happened: this address may now sign in. Every
surface that calls this has to say the same, because a UI claiming an invitation
was emailed is the lying button one layer down — and the operator finds out when
the client says they never got anything.

## The two refusals worth having

**An address that belongs to another organisation is refused, not moved.**
`auth_user.email` is globally unique and one user has exactly one tenant
(`auth/models.py`), so "adding" such an address would silently remove that person
from their own organisation — with their sessions, leads and integrations left
behind in it. That is data loss dressed as an invitation, and it would be
performed by an admin who typed a plausible address.

**The last admin cannot be removed or demoted.** An organisation with no admin
can never invite anybody, connect an integration, or erase a contact under GDPR
Article 17 — it is bricked, permanently, by one successful-looking request.
There is no recovery path short of somebody with `psql`.
"""

from __future__ import annotations

import datetime as dt
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import USER_ROLES, AuthUser
from app.auth.principal import Principal, require_admin
from app.db import get_session

router = APIRouter(prefix="/v1/users", tags=["users"])


class UserOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    userId: str
    email: str
    displayName: str | None
    role: str
    createdAt: dt.datetime


class InviteIn(BaseModel):
    email: EmailStr
    #: Validated against `USER_ROLES` rather than typed as a Literal so the
    #: refusal can name the four and so a role added to that tuple is offerable
    #: here without a second edit.
    role: str = Field(default="viewer")
    displayName: str | None = Field(default=None, max_length=120)


class InviteOut(UserOut):
    """What was granted, and — pointedly — what was not.

    `emailSent` is always `false` and is in the payload anyway, so a UI cannot
    render "invitation sent" by assuming. The day an email provider exists this
    becomes true in one place instead of every surface being corrected.
    """

    emailSent: bool = False
    detail: str


@router.get("", response_model=list[UserOut], summary="Everyone in this organisation")
async def list_users(
    principal: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[UserOut]:
    """The caller's own organisation, always. The tenant comes from the verified
    credential and there is no parameter to disagree with it."""
    rows = (
        await session.execute(
            select(AuthUser)
            .where(AuthUser.tenant_id == principal.tenant_id)
            .order_by(AuthUser.created_at.asc())
        )
    ).scalars().all()

    return [
        UserOut(
            userId=u.user_id,
            email=u.email,
            displayName=u.display_name,
            role=u.role,
            createdAt=u.created_at,
        )
        for u in rows
    ]


@router.post(
    "",
    response_model=InviteOut,
    status_code=status.HTTP_201_CREATED,
    summary="Give an address a seat in this organisation",
)
async def invite(
    body: InviteIn,
    principal: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> InviteOut:
    """Create the `auth_user` row that `POST /v1/auth/token` looks for.

    That is the whole mechanism: a Firebase identity is only half a login here,
    because the address still has to map to a tenant and a role. This writes the
    other half, and until it exists the person gets `401 unknown user` no matter
    how correctly they sign in with Google.
    """
    if body.role not in USER_ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"unknown role {body.role!r}; multi-tenant.md §3 defines "
                f"{', '.join(USER_ROLES)}"
            ),
        )

    email = body.email.lower()
    existing = (
        await session.execute(select(AuthUser).where(AuthUser.email == email))
    ).scalar_one_or_none()

    if existing is not None:
        if existing.tenant_id == principal.tenant_id:
            # Already here. Idempotent rather than a 409, because the realistic
            # cause is an admin who is not sure whether the first attempt landed
            # — there being no email to check — and re-inviting should not be
            # punished. It does **not** silently change their role: that is a
            # different intent and it would let a mistyped invite quietly demote
            # a colleague.
            return InviteOut(
                userId=existing.user_id,
                email=existing.email,
                displayName=existing.display_name,
                role=existing.role,
                createdAt=existing.created_at,
                detail=(
                    f"{existing.email} is already in this organisation as "
                    f"{existing.role}. Nothing changed."
                ),
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "that address already belongs to a different organisation. "
                "Moving it here would take them out of theirs, along with "
                "everything they can see in it — so it has to be their admin "
                "who removes them first."
            ),
        )

    user = AuthUser(
        user_id=f"u_{secrets.token_hex(6)}",
        email=email,
        display_name=body.displayName,
        tenant_id=principal.tenant_id,
        role=body.role,
    )
    session.add(user)
    await session.flush()

    return InviteOut(
        userId=user.user_id,
        email=user.email,
        displayName=user.display_name,
        role=user.role,
        createdAt=user.created_at,
        detail=(
            f"{user.email} can now sign in and will see this organisation as "
            f"{user.role}. No email was sent — tell them yourself."
        ),
    )


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove somebody's seat",
)
async def remove(
    user_id: str,
    principal: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete the `auth_user` row, which is what makes the address a stranger again.

    Nothing they produced is touched. Sessions, events, leads and outcomes belong
    to the organisation, not to the person who happened to be signed in — and a
    removal that deleted an activation's data would be a way to destroy a
    client's report by tidying up a leaver.
    """
    user = (
        await session.execute(
            select(AuthUser).where(
                AuthUser.user_id == user_id,
                AuthUser.tenant_id == principal.tenant_id,
            )
        )
    ).scalar_one_or_none()

    if user is None:
        # Scoped to the caller's tenant above, so this is also the answer for
        # somebody else's user — a 404 rather than a 403, because confirming
        # that a user id exists elsewhere is a fact this caller has no business
        # learning.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no user {user_id!r} in this organisation",
        )

    if user.role == "admin":
        admins = (
            await session.execute(
                select(func.count())
                .select_from(AuthUser)
                .where(
                    AuthUser.tenant_id == principal.tenant_id,
                    AuthUser.role == "admin",
                )
            )
        ).scalar_one()
        if admins <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "that is the only admin. An organisation without one cannot "
                    "invite anybody, connect an integration, or honour an "
                    "erasure request — promote somebody else first."
                ),
            )

    await session.delete(user)
