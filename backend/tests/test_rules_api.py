"""
`/v1/rules` — authoring a rule document.

Two things are being checked, and the second matters more than the first. One:
the CRUD works. Two: the validator is *asymmetric* in the way ADR-002 asks for —
`triggerType` open, `condition.type` and `action.type` closed — because getting
that backwards is invisible until either a new producer lands (and rules cannot
name it) or a rule is saved naming an action nothing can carry out.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import AuthUser
from app.auth.tokens import issue_token

TENANT = "t_floats"  # the conftest client's tenant


async def become(
    client: AsyncClient, db_session: AsyncSession, *, tenant_id: str, role: str
) -> None:
    """Re-authenticate the client as somebody else.

    A token alone is not enough: `get_principal` looks the subject up, so a
    signed token for a user who does not exist is a 401 rather than the 403 or
    empty list a test about *authorisation* is trying to observe.
    """
    user_id = f"u_{role}_{tenant_id}"
    db_session.add(
        AuthUser(
            user_id=user_id,
            email=f"{user_id}@floats.demo",
            display_name=role,
            tenant_id=tenant_id,
            role=role,
        )
    )
    await db_session.commit()
    token = issue_token(subject=user_id, tenant_id=tenant_id, role=role)
    client.headers["Authorization"] = f"Bearer {token}"

RULE = {
    "ruleId": "r_entry_crowd",
    "name": "Entrance crowding → ping ops",
    "triggerType": "spatial.dwell",
    "triggerZoneId": "z_entry",
    "condition": {
        "type": "threshold",
        "count": 5,
        "windowSec": 30,
        "zoneId": "z_entry",
        "minDwellSec": 30,
    },
    "action": {"type": "slack", "channel": "#ops", "message": "5 at entrance"},
    "enabled": True,
    "cooldownSec": 60,
}


async def test_a_rule_round_trips(client: AsyncClient):
    """ADR-002's own example, saved and read back unchanged. The document an
    operator confirmed is the document that is stored."""
    put = await client.put("/v1/rules/r_entry_crowd", json=RULE)
    assert put.status_code == 200

    got = await client.get("/v1/rules/r_entry_crowd")
    assert got.status_code == 200
    body = got.json()
    for key, value in RULE.items():
        if isinstance(value, dict):
            # Every field the operator set, spelled the way they set it. A
            # response carries the *unset* optionals as explicit nulls too —
            # `payloadEquals` here — which is what the response model does for
            # every optional field and not something this rule chose. What is
            # stored is `exclude_none`, so the row itself carries only what was
            # written.
            assert {k: body[key][k] for k in value} == value
        else:
            assert body[key] == value
    assert body["condition"]["payloadEquals"] is None
    assert body["tenantId"] == TENANT


async def test_a_payload_filter_round_trips(client: AsyncClient):
    """`payloadEquals` is what lets a rule tell the two ends of one event type
    apart — a zone filling from the same zone clearing. It is stored in the
    document's own camelCase, which is the spelling
    `consumers/rules._matches_scope` reads back."""
    rule = {
        **RULE,
        "ruleId": "r_entry_full",
        "triggerType": "spatial.occupancy",
        "condition": {
            "type": "any",
            "zoneId": "z_entry",
            "payloadEquals": {"status": "over"},
        },
    }
    put = await client.put("/v1/rules/r_entry_full", json=rule)
    assert put.status_code == 200

    got = await client.get("/v1/rules/r_entry_full")
    assert got.json()["condition"]["payloadEquals"] == {"status": "over"}


async def test_a_payload_filter_is_refused_on_a_silence(client: AsyncClient):
    """A `none` condition is judged by looking back for the last event of a
    type, not by scoping the events in a window (`consumers/rules._none`), so
    there is no payload to match against. Refused rather than accepted and
    ignored — a filter that silently does nothing is how a rule ends up armed,
    correct-looking and matching everything."""
    resp = await client.put(
        "/v1/rules/r_bad",
        json={
            **RULE,
            "ruleId": "r_bad",
            "condition": {
                "type": "none",
                "windowSec": 300,
                "payloadEquals": {"status": "over"},
            },
        },
    )
    assert resp.status_code == 422


async def test_a_second_put_replaces_rather_than_duplicates(client: AsyncClient):
    """The composer sends a whole document when an operator edits a rule, not a
    patch — a half-edited rule is exactly the state that makes "why did this
    fire?" unanswerable from the stored document."""
    await client.put("/v1/rules/r_entry_crowd", json=RULE)
    await client.put(
        "/v1/rules/r_entry_crowd", json={**RULE, "name": "Renamed", "enabled": False}
    )

    listed = (await client.get("/v1/rules")).json()
    assert len(listed) == 1
    assert listed[0]["name"] == "Renamed"
    assert listed[0]["enabled"] is False


async def test_a_mismatched_rule_id_is_refused(client: AsyncClient):
    """Silently preferring the path would mean a confused composer overwrites the
    wrong rule and finds out when it fires."""
    resp = await client.put("/v1/rules/r_other", json=RULE)
    assert resp.status_code == 400
    assert "r_entry_crowd" in resp.text


async def test_delete_removes_it(client: AsyncClient):
    await client.put("/v1/rules/r_entry_crowd", json=RULE)
    assert (await client.delete("/v1/rules/r_entry_crowd")).status_code == 204
    assert (await client.get("/v1/rules/r_entry_crowd")).status_code == 404
    assert (await client.delete("/v1/rules/r_entry_crowd")).status_code == 404


# ── validation ────────────────────────────────────────────────────────────────


async def test_a_trigger_type_no_producer_writes_yet_is_accepted(client: AsyncClient):
    """The asymmetry, first half. `intent.scored` has no producer on any branch —
    it was pre-registered in the taxonomy so that P4 does not pay a schema
    migration to ship one. A rule spec with a closed trigger enum would reinstate
    exactly that tax, which is why ADR-002 makes this a namespace check."""
    resp = await client.put(
        "/v1/rules/r_intent",
        json={**RULE, "ruleId": "r_intent", "triggerType": "intent.scored"},
    )
    assert resp.status_code == 200


async def test_an_unknown_trigger_namespace_is_refused(client: AsyncClient):
    """What the prefix check does buy. It does not catch a typo in the suffix —
    `spatial.dwel` would pass — and `schemas.EVENT_NAMESPACES` is candid about
    that gap."""
    resp = await client.put(
        "/v1/rules/r_bad",
        json={**RULE, "ruleId": "r_bad", "triggerType": "spatal.dwell"},
    )
    assert resp.status_code == 422


async def test_an_action_nothing_dispatches_is_refused(client: AsyncClient):
    """The asymmetry, second half. Unlike the event taxonomy, actions are not
    additive by design: a rule naming an action no dispatcher implements looks
    armed on the composer screen and does nothing in the room. A 422 at the
    moment of saving is the only point where that is cheap."""
    resp = await client.put(
        "/v1/rules/r_bad",
        json={**RULE, "ruleId": "r_bad", "action": {"type": "carrier_pigeon"}},
    )
    assert resp.status_code == 422


async def test_an_unknown_condition_type_is_refused(client: AsyncClient):
    resp = await client.put(
        "/v1/rules/r_bad",
        json={**RULE, "ruleId": "r_bad", "condition": {"type": "vibes"}},
    )
    assert resp.status_code == 422


async def test_a_threshold_needs_a_window(client: AsyncClient):
    """A window of zero seconds counts nothing, and a rule that can never fire is
    worse than one that is refused."""
    resp = await client.put(
        "/v1/rules/r_bad",
        json={
            **RULE,
            "ruleId": "r_bad",
            "condition": {"type": "threshold", "count": 5, "windowSec": 0},
        },
    )
    assert resp.status_code == 422


# ── tenancy ───────────────────────────────────────────────────────────────────


async def test_the_body_cannot_choose_a_tenant(client: AsyncClient):
    """`tenantId` is not on `RuleIn` at all, so a body carrying one is ignored
    rather than honoured. The tenant comes from the verified credential, as it
    does everywhere else in this API."""
    await client.put(
        "/v1/rules/r_entry_crowd", json={**RULE, "tenantId": "t_somebody_else"}
    )

    body = (await client.get("/v1/rules/r_entry_crowd")).json()
    assert body["tenantId"] == TENANT


async def test_another_tenant_cannot_see_the_rule(
    client: AsyncClient, db_session: AsyncSession
):
    """Row-level security, not a WHERE clause in the router — `rules` is under
    FORCE ROW LEVEL SECURITY (migration 0004), so the database refuses even if a
    future handler forgets to scope."""
    await client.put("/v1/rules/r_entry_crowd", json=RULE)

    await become(client, db_session, tenant_id="t_test_other", role="admin")

    assert (await client.get("/v1/rules")).json() == []
    assert (await client.get("/v1/rules/r_entry_crowd")).status_code == 404


async def test_a_viewer_cannot_arm_a_rule(client: AsyncClient, db_session: AsyncSession):
    """Saving a rule arms an action that will post to Slack or change a screen in
    the room, with nobody in the loop afterwards. That is an operator's decision,
    not a reader's."""
    await become(client, db_session, tenant_id=TENANT, role="viewer")

    assert (await client.put("/v1/rules/r_entry_crowd", json=RULE)).status_code == 403
    # Reading is still fine.
    assert (await client.get("/v1/rules")).status_code == 200
