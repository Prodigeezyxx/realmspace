"""
The four roles, asserted against the real endpoints.

`multi-tenant.md` §3 has specified Owner/Admin, Operator, Analyst/Marketer and
Viewer/Client since Phase 1, and `auth/models.py` has listed all four in
`USER_ROLES` — but only `admin` and `operator` were ever checked. `analyst` and
`viewer` were both simply "not admin, not operator", and therefore identical: an
analyst could not do the things the table grants them, and a client-facing viewer
could reach everything an analyst could. A role that changes no behaviour is a
label.

## Why this asserts against endpoints rather than against the map

A test that reads `CAPABILITIES` and checks it says what it says proves nothing —
it is the map agreeing with itself. What matters is whether the router a caller
actually hits refuses them, so every row below is a real request with a real
signed token.

## The two deliberate denials

`multi-tenant.md` §3 is read as a **deny-list**: a permission matrix where
omission grants nothing is the only kind that means anything. Two consequences
are surprising enough to be worth pinning, so that changing either has to be a
decision rather than a side effect:

- an **operator cannot query Ask** — §3 puts it with Analyst, though the person
  running the room is arguably who most wants to ask it a question;
- a **viewer cannot read leads** — they are the sponsor or brand stakeholder,
  reading the report about an activation rather than the list of people who came.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import USER_ROLES, AuthUser
from app.auth.principal import CAPABILITIES
from app.auth.tokens import issue_token
from app.db import get_session
from app.main import app

TENANT = "t_floats"

#: One request per capability, chosen to be the cheapest endpoint that is gated
#: by it. A 403 is the assertion; any other status means the gate let them
#: through, whatever happened afterwards.
PROBES: dict[str, tuple[str, str]] = {
    "read": ("GET", "/v1/insights?sessionId=s_probe"),
    # Reading the queue is `read` — watching the floor includes seeing what
    # is stuck. Acting on it is `run_activation`.
    "run_activation": ("POST", "/v1/dead-letters/1/retry"),
    "author_rules": ("DELETE", "/v1/rules/r_probe"),
    "ask": ("POST", "/v1/ask"),
    "leads": ("GET", "/v1/handoffs"),
    "manage_org": ("GET", "/v1/integrations"),
}


async def client_for(
    db_session: AsyncSession, *, role: str
) -> AsyncClient:
    user_id = f"u_rbac_{role}"
    db_session.add(
        AuthUser(
            user_id=user_id,
            email=f"{user_id}@floats.demo",
            display_name=user_id,
            tenant_id=TENANT,
            role=role,
        )
    )
    await db_session.commit()

    async def override() -> AsyncIterator[AsyncSession]:
        yield db_session
        await db_session.commit()

    app.dependency_overrides[get_session] = override
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={
            "Authorization": (
                f"Bearer {issue_token(subject=user_id, tenant_id=TENANT, role=role)}"
            )
        },
    )


async def probe(client: AsyncClient, capability: str) -> int:
    method, path = PROBES[capability]
    body = {"question": "how many visitors?", "sessionId": "s_probe"}
    response = await client.request(
        method, path, json=body if method == "POST" else None
    )
    return response.status_code


@pytest.mark.parametrize("role", USER_ROLES)
@pytest.mark.parametrize("capability", sorted(PROBES))
async def test_the_matrix_is_what_the_doc_says(
    role: str, capability: str, db_session: AsyncSession
) -> None:
    """Every role against every capability, through the endpoints themselves."""
    granted = capability in CAPABILITIES[role]
    client = await client_for(db_session, role=role)
    async with client:
        status = await probe(client, capability)

    if granted:
        assert status != 403, f"{role} should hold {capability}"
    else:
        assert status == 403, f"{role} should not hold {capability}"


async def test_an_operator_cannot_query_ask(db_session: AsyncSession) -> None:
    """The most surprising line in the matrix, pinned on purpose.

    §3 puts "query Ask" with Analyst and gives Operator "run activations, see
    live dashboard". Read as a deny-list, that denies the person running the room
    the one tool for asking it a question. Recorded in the doc as a decision; if
    it is ever reversed, this test is where it has to be reversed.
    """
    client = await client_for(db_session, role="operator")
    async with client:
        response = await client.post(
            "/v1/ask", json={"question": "how many visitors?", "sessionId": "s"}
        )
    assert response.status_code == 403
    assert "'ask'" in response.json()["detail"]
    # The refusal names the capability, not the roles that hold it: an operator
    # told "this needs ask" can be granted it; one told "you are not an analyst"
    # learns only that they are not somebody else.
    assert "analyst" not in response.json()["detail"]


async def test_a_viewer_cannot_read_the_leads(db_session: AsyncSession) -> None:
    """A Viewer is "the sponsor/brand stakeholder" — the client reading a report
    about their activation, not their sales team reading the attendee list."""
    client = await client_for(db_session, role="viewer")
    async with client:
        assert (await client.get("/v1/handoffs")).status_code == 403
        assert (await client.get("/v1/followups")).status_code == 403
        # But the report itself, and the room's own numbers, are theirs.
        assert (await client.get("/v1/insights")).status_code == 200


async def test_an_admin_is_a_superset(db_session: AsyncSession) -> None:
    """A decision, not an oversight.

    §3 does not list "run activations" against Admin, and a literal reading would
    lock an owner out of their own floor on the morning of an event. An admin can
    already grant themselves any role, so denying them would be theatre with a
    cost.
    """
    client = await client_for(db_session, role="admin")
    async with client:
        for capability in PROBES:
            assert await probe(client, capability) != 403, capability


async def test_a_device_key_holds_nothing(
    device_key: str, db_session: AsyncSession
) -> None:
    """Unchanged, and worth re-asserting here: an API key exists to append
    events. A key taped inside a booth kit is the credential most likely to walk
    out of a venue, so it holds no capability at all — not even `read`."""

    async def override() -> AsyncIterator[AsyncSession]:
        yield db_session
        await db_session.commit()

    app.dependency_overrides[get_session] = override
    client = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        # A device key travels in its own header, not as a bearer token —
        # `auth/principal.py` keeps the two credential kinds on separate
        # doors so neither can be mistaken for the other.
        headers={"X-API-Key": device_key},
    )
    async with client:
        for capability in PROBES:
            assert await probe(client, capability) == 403, capability


async def test_a_role_nobody_defined_holds_nothing(db_session: AsyncSession) -> None:
    """Fail closed on a typo.

    `auth_user.role` is free text, and the old gate admitted any human role by
    name — which is how three test suites ran for weeks against a role called
    "reader" that was never in `USER_ROLES`. An unrecognised role now grants
    nothing rather than everything a reader had.
    """
    client = await client_for(db_session, role="reader")
    async with client:
        for capability in PROBES:
            assert await probe(client, capability) == 403, capability
