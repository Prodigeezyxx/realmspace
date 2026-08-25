"""
Getting a credential.

Three endpoints:

  POST /v1/auth/signup    a verified identity with no account → a new organisation
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

## …and now they have somewhere to go *(2026-08-21)*

That 401 was a dead end. `multi-tenant.md` §4 lists six onboarding steps and
steps 2–6 all exist — the wizard, prefabs, zone editor, integrations, consent
config, run — while step 1, "Sign up → create Organization", was unbuilt. A user
existed only if somebody had run `python -m app.auth.seed` on the server. The
dashboard meanwhile shipped a "Create your account" tab that made a Firebase
identity and then hit that 401 forever.

`POST /v1/auth/signup` closes it, and goes through **the same `_verified_email`**
this file already calls "the whole of the authentication decision, in one place".
That is the point rather than an economy: an endpoint that hands out
organisations must not have its own opinion about who somebody is. It inherits
the table above unchanged, including the 503.

Three things it will not do:

- **It never joins an existing organisation.** No email-domain matching. `resolve`
  below already argues this: "Defaulting an unknown user into a real tenant is how
  someone ends up quietly holding a role nobody granted them." Joining is what
  `POST /v1/users` is for, and it takes an admin who already belongs.
- **It refuses an address that already has an account**, rather than upserting.
  `auth_user.email` is globally unique, so a careless upsert would not create a
  second account — it would **move that person out of their own organisation**,
  taking their sessions, leads and integrations with them.
- **It does not choose the role.** Whoever signs up is `admin`, because they are
  alone in a new organisation and every other role is unable to invite the second
  person (`multi-tenant.md` §3 puts users with Admin). An operator signing
  themselves up would create an org nobody could ever join.
"""

from __future__ import annotations

import logging
import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import plans
from app.auth import firebase, tokens
from app.auth.models import AuthUser, Tenant
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


class SignupRequest(TokenRequest):
    """The same identity `TokenRequest` carries, plus what to call the org.

    Subclassed rather than repeated so the two endpoints cannot drift about what
    proves an identity — `_verified_email` takes either.
    """

    #: What the organisation is called. Not the tenant id: an operator types
    #: "Floats" and should never have to think about `t_floats`.
    org_name: str = Field(alias="orgName", min_length=1, max_length=120)

    @field_validator("org_name")
    @classmethod
    def name_is_not_only_whitespace(cls, value: str) -> str:
        """Strip first, then require something left.

        `min_length` runs against the raw string, so `"  "` passes it and then
        strips to nothing — creating an organisation whose name is blank on
        every screen that shows one, with no later prompt that would fill it in.
        Stripping here also means the id derived from it is derived from the
        same text the operator will see.
        """
        stripped = value.strip()
        if not stripped:
            raise ValueError("an organisation needs a name")
        return stripped


class SignupResponse(TokenResponse):
    """A token, so signup and sign-in are one round trip rather than two.

    Making the caller sign in again immediately after signing up would be a
    second chance for the same request to fail, on a path where the account now
    exists and the user has been told nothing.
    """

    orgName: str


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


#: Tenant ids look like `t_floats` — the convention every seeded id and every
#: doc example already uses, so a generated one is not visibly second-class.
_SLUG = re.compile(r"[^a-z0-9]+")


def _tenant_id_from(org_name: str) -> str:
    """A readable id for an organisation, with a random tail.

    Readable because these ids appear in `psql`, in `/ops`, in the WebSocket
    path and in every support conversation, and `t_northwind` is worth a great
    deal more than a UUID at three in the morning.

    The random tail is not decoration. Two organisations can legitimately be
    called the same thing, and the alternative to a suffix is either refusing
    the second one — telling a real customer that their company name is taken,
    which it is not, it is somebody else's — or a counter, which leaks how many
    tenants exist to anyone who signs up twice.

    Falls back to a bare random id when the name has no usable characters at
    all, which a name in a non-Latin script would. An organisation is not
    obliged to be nameable in ASCII to have an account here.
    """
    slug = _SLUG.sub("_", org_name.strip().lower()).strip("_")[:32]
    tail = secrets.token_hex(3)
    return f"t_{slug}_{tail}" if slug else f"t_{tail}"


@router.post(
    "/signup",
    response_model=SignupResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an organisation for a verified identity that has no account",
)
async def signup(
    body: SignupRequest, session: AsyncSession = Depends(get_session)
) -> SignupResponse:
    """`multi-tenant.md` §4 step 1, which was the only one of the six unbuilt.

    Unauthenticated by design — the caller has no account yet, which is the
    whole point — but **not unverified**: `_verified_email` runs first and is the
    same gate `POST /token` passes through, including the 503 on a deployment
    that has not configured Firebase. An organisation handed out on an
    unverified email would be somebody else's organisation.

    The user and the tenant are written in one transaction. Half of this
    succeeding leaves either an organisation nobody can enter or a user pointing
    at an organisation that does not exist, and the second is the worse one: they
    would authenticate fine and see an empty, uncreatable world.
    """
    from app.config import get_settings

    settings = get_settings()
    email = _verified_email(body, settings)

    existing = await _lookup(session, email)
    if existing is not None:
        # 409 rather than the 401 `/token` gives an unknown address, and the
        # enumeration argument does not apply in reverse: a caller who reaches
        # here has already proven this address is theirs, so telling them it has
        # an account tells them nothing they could not learn by signing in.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "that address already belongs to an organisation — sign in "
                "instead. To join a different one, ask an admin there to invite "
                "you."
            ),
        )

    tenant_id = _tenant_id_from(body.org_name)
    user_id = f"u_{secrets.token_hex(6)}"

    session.add(
        Tenant(
            tenant_id=tenant_id,
            name=body.org_name,
            created_by=user_id,
            # The entry tier, stated rather than left to the column default:
            # what a self-serve signup gets is a commercial decision and should
            # be readable here, not only in a migration. `app/plans.py`.
            plan=plans.DEFAULT_PLAN,
        )
    )
    session.add(
        AuthUser(
            user_id=user_id,
            email=email.lower(),
            display_name=None,
            tenant_id=tenant_id,
            # Admin, and not configurable. See the module docstring: every other
            # role is unable to invite the second person, so an organisation
            # founded by an operator could never be joined.
            role="admin",
        )
    )
    await session.flush()

    log.info("organisation %s created by %s", tenant_id, user_id)

    return SignupResponse(
        accessToken=tokens.issue_token(
            subject=user_id, tenant_id=tenant_id, role="admin"
        ),
        expiresIn=settings.jwt_ttl_seconds,
        tenantId=tenant_id,
        role="admin",
        orgName=body.org_name,
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
