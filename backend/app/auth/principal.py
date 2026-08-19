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
from app.db import get_session, scope_to_tenant


#: What a caller may do, as capabilities rather than as a ladder of roles.
#:
#: `multi-tenant.md` §3 is a **matrix**, not a hierarchy: an Operator "runs
#: activations" and does not build agents; an Analyst "builds agents, queries
#: Ask, owns follow-up sequences" and does not run activations. A ladder cannot
#: express that, which is exactly why two of the four roles had collapsed into
#: one — `analyst` and `viewer` were both simply "not admin, not operator", and
#: therefore identical.
#:
#: Named for what they let somebody do, so the map below can be read against the
#: doc's own table without translation.
READ = "read"                      # dashboards, reports, events, insights
RUN_ACTIVATION = "run_activation"  # session config, consent capture, outcomes, /ops
AUTHOR_RULES = "author_rules"      # writing a rule document
ASK = "ask"                        # /v1/ask
LEADS = "leads"                    # /v1/handoffs, /v1/followups — contact PII
MANAGE_ORG = "manage_org"          # integrations, erasure

CAPABILITIES: dict[str, frozenset[str]] = {
    # "manage org, billing, integrations, users" — and everything else.
    #
    # Admin as a strict superset is a decision rather than an oversight. §3 does
    # not list "run activations" against Admin, and a literal reading would lock
    # an owner out of their own floor on the morning of an event. That is a
    # support ticket, not a security property: an admin can already grant
    # themselves any role, so denying them here would be theatre with a cost.
    "admin": frozenset(
        {READ, RUN_ACTIVATION, AUTHOR_RULES, ASK, LEADS, MANAGE_ORG}
    ),
    # "run activations, see live dashboard, receive staff prompts".
    #
    # Deliberately **without** ASK and LEADS. §3 puts "query Ask" and "own
    # follow-up sequences" with Analyst, and this map reads the table as a
    # deny-list: a permission matrix where omission grants nothing is the only
    # kind that means anything. It is the surprising one — the person running the
    # room is arguably who most wants to ask it a question — and it is recorded
    # in `multi-tenant.md` §3 as a decision so a future change has to be one too.
    "operator": frozenset({READ, RUN_ACTIVATION}),
    # "build agents, query Ask, own follow-up sequences, ROI reports".
    "analyst": frozenset({READ, AUTHOR_RULES, ASK, LEADS}),
    # "read-only dashboard + report" — the sponsor or brand stakeholder.
    "viewer": frozenset({READ}),
}


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

    def can(self, capability: str) -> bool:
        """May this caller do `capability`?

        Devices never can. An API key exists to append events — that is what a
        camera is for — and `may_write` is the only permission it has. Reading
        the log back, configuring a session or seeing a lead are all things a
        key taped inside a booth kit has no business doing.
        """
        if self.kind != "user":
            return False
        return capability in CAPABILITIES.get(self.role, frozenset())

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
    principal: Principal | None = None

    if x_api_key:
        principal = await _principal_from_api_key(session, x_api_key)
    elif authorization:
        scheme, _, credential = authorization.partition(" ")
        if scheme.lower() == "bearer" and credential:
            principal = await _principal_from_token(session, credential)

    if principal is None:
        raise UNAUTHENTICATED

    # Tell the database which tenant this request may touch. Everything after
    # this point is constrained by the row-level security policies from
    # migration 0003 — including any query someone adds later and forgets to
    # scope, which is the entire reason for doing it here rather than trusting
    # each call site.
    #
    # It happens after authentication because the tenant comes *from* the
    # verified credential. The auth tables themselves are deliberately not
    # under RLS, or this lookup could not have happened at all.
    await scope_to_tenant(session, principal.tenant_id)
    return principal


def requires(capability: str):
    """A dependency that admits only callers holding `capability`.

    One function behind every gate in the system, so "who may do this" is a line
    in `CAPABILITIES` rather than a condition repeated in each router with its
    own idea of which roles count. The three named dependencies below are thin
    wrappers over it, kept because every call site already reads well.

    The refusal names the capability rather than the roles that hold it. An
    operator told "this needs `ask`" can be granted it; one told "you are not an
    analyst" learns only that they are not somebody else.
    """

    async def dependency(
        principal: Principal = Depends(get_principal),
    ) -> Principal:
        if principal.kind != "user":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="device credentials are write-only",
            )
        if not principal.can(capability):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"this needs the {capability!r} capability, which the "
                    f"{principal.role!r} role does not have "
                    "(multi-tenant.md §3)"
                ),
            )
        return principal

    return dependency


#: `read` — dashboards, reports, events, insights. Every human role holds it.
require_reader = requires(READ)

#: `ask` — §3 puts "query Ask" with Analyst. See CAPABILITIES on why an
#: operator does not hold it.
require_ask = requires(ASK)

#: `leads` — anything returning contact PII: the handoff pull API and the
#: follow-up drafts. §3 gives "own follow-up sequences" to Analyst, and a
#: Viewer is the client's own stakeholder rather than their sales team.
require_leads = requires(LEADS)

#: `author_rules` — writing a rule document. Reading them is `read`: seeing what
#: is armed is part of watching the floor.
require_rule_author = requires(AUTHOR_RULES)


#: `run_activation` — session config, consent capture, outcomes, and the ops
#: screens.
#:
#: Narrower than `may_write` on purpose. Appending an event is something every
#: credential does by design — that is what a camera is for. Redrawing zones or
#: setting the activation cost is not: those are the numbers the ROI report
#: divides by (roi-framework.md §5), so the set of things that can change them
#: should be as small as the job allows.
#:
#: A device key cannot reach here at all, which matters because that is the
#: credential most likely to walk out of a venue.
require_operator = requires(RUN_ACTIVATION)


#: `manage_org` — endpoints that hand us a credential to somebody else's system,
#: or erase a person from every store.
#:
#: The line is where multi-tenant.md §3 draws it: an Operator "runs
#: activations", an Admin "manages org, billing, **integrations**, users".
#:
#: The distinction is worth its own capability rather than reusing the operator
#: one. An operator arming a rule decides what this booth does in this room, and
#: the blast radius is a Slack message or a screen. Storing a CRM token decides
#: what realmspace writes into the client's system of record, on a credential
#: that outlives the activation and that nobody on the floor should be able to
#: replace.
require_admin = requires(MANAGE_ORG)


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
