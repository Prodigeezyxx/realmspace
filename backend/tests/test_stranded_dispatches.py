"""
The stranded `claimed` dispatch, and the human who can finally answer for it.

`claim_dispatch` refuses a row sitting at `claimed` — a process died between the
claim and the outbound call, so whether the message arrived is unknown, and it
"needs a human rather than a guess". Until now there was no way to ask one: the
row blocked its `(fired_event_id, action_type)` pair forever.

These tests are mostly about restraint. The easy version of this feature is a
retry button, and a retry button is the double-post that `rule_dispatch` exists
to prevent. So what is asserted here is as much what does *not* happen — no
re-send, no overwriting an outcome the dispatcher observed, no healthy in-flight
dispatch on the operator's screen — as what does.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.config import get_settings
from tests.conftest import TENANT

RULE_ID = "r_entry_crowd"


@pytest.fixture(autouse=True)
async def _a_rule_exists(db_session: AsyncSession):
    """The panel shows a rule's name, so there has to be one to show."""
    await repository.upsert_rule(
        db_session,
        tenant_id=TENANT,
        rule_id=RULE_ID,
        name="Entrance crowding → ping ops",
        trigger_type="spatial.dwell",
        trigger_zone_id="z_entry",
        condition={"type": "threshold", "count": 5, "windowSec": 300},
        action={"type": "slack", "channel": "#ops", "message": "5 at entrance"},
        enabled=True,
        cooldown_sec=60,
    )
    await db_session.commit()


async def claim(
    db_session: AsyncSession, *, fired_event_id: uuid.UUID | None = None
) -> int:
    """A dispatch in the state a live one is in: claimed, call in flight."""
    row = await repository.claim_dispatch(
        db_session,
        tenant_id=TENANT,
        fired_event_id=fired_event_id or uuid.uuid4(),
        rule_id=RULE_ID,
        action_type="slack",
    )
    await db_session.commit()
    assert row is not None
    return row.id


async def strand(db_session: AsyncSession, dispatch_id: int, *, age_seconds: float):
    """Age a claimed row past the cutoff.

    `created_at` is a server default, so there is no way to insert one already
    old — and faking the clock instead would test a monkeypatched `now()` rather
    than the query.
    """
    await db_session.execute(
        text(
            "UPDATE rule_dispatch SET created_at = now() - make_interval(secs => :age) "
            "WHERE id = :id"
        ),
        {"age": age_seconds, "id": dispatch_id},
    )
    await db_session.commit()


async def stranded_list(client: AsyncClient) -> list[dict]:
    response = await client.get("/v1/dispatches/stranded")
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_dispatch_still_in_flight_is_not_called_stranded(
    client: AsyncClient, db_session: AsyncSession
):
    """The one that would ruin the panel.

    Every dispatch is `claimed` for the whole of its outbound call — that is the
    idempotency mechanism, not a symptom. Listing them all would put each healthy
    Slack post on screen as a problem, and an operator who learns the panel is
    mostly false alarms stops reading it.
    """
    await claim(db_session)

    assert await stranded_list(client) == []


async def test_a_dispatch_whose_process_died_shows_up_with_its_rule(
    client: AsyncClient, db_session: AsyncSession
):
    dispatch_id = await claim(db_session)
    await strand(db_session, dispatch_id, age_seconds=300)

    rows = await stranded_list(client)

    assert len(rows) == 1
    assert rows[0]["id"] == dispatch_id
    assert rows[0]["actionType"] == "slack"
    # The name, not just the id: `r_entry_crowd` does not tell an operator which
    # message to go looking for in the channel.
    assert rows[0]["ruleName"] == "Entrance crowding → ping ops"
    assert rows[0]["strandedForSeconds"] >= 300


async def test_a_deleted_rule_leaves_the_dispatch_readable(
    client: AsyncClient, db_session: AsyncSession
):
    """A rule can be deleted while one of its dispatches is stranded, and the
    stranded row still has to be resolvable — otherwise deleting a rule creates
    permanent blocks nobody can clear."""
    dispatch_id = await claim(db_session)
    await strand(db_session, dispatch_id, age_seconds=300)
    await repository.delete_rule(db_session, tenant_id=TENANT, rule_id=RULE_ID)
    await db_session.commit()

    rows = await stranded_list(client)

    assert len(rows) == 1
    assert rows[0]["ruleId"] == RULE_ID
    assert rows[0]["ruleName"] is None


async def test_the_operator_saying_it_arrived_closes_it_for_good(
    client: AsyncClient, db_session: AsyncSession
):
    """`delivered` — and the claim stays taken, so a replay still cannot post."""
    fired = uuid.uuid4()
    dispatch_id = await claim(db_session, fired_event_id=fired)
    await strand(db_session, dispatch_id, age_seconds=300)

    response = await client.post(
        f"/v1/dispatches/{dispatch_id}/resolve",
        json={"verdict": "delivered", "note": "found it in #ops at 14:32"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "delivered"
    assert "will not post again" in response.json()["effect"]
    assert await stranded_list(client) == []

    # The property that matters: a replayed firing is still refused.
    retaken = await repository.claim_dispatch(
        db_session,
        tenant_id=TENANT,
        fired_event_id=fired,
        rule_id=RULE_ID,
        action_type="slack",
    )
    assert retaken is None


async def test_the_operator_saying_it_never_arrived_releases_it(
    client: AsyncClient, db_session: AsyncSession
):
    """`failed` — the block is gone, and `claim_dispatch` takes the row back.

    Note what is *not* asserted: nothing was sent. The dispatcher's cursor is
    long past this firing, so the verdict removes an obstacle rather than
    deciding to act again.
    """
    fired = uuid.uuid4()
    dispatch_id = await claim(db_session, fired_event_id=fired)
    await strand(db_session, dispatch_id, age_seconds=300)

    response = await client.post(
        f"/v1/dispatches/{dispatch_id}/resolve",
        json={"verdict": "failed", "note": "nothing in the channel"},
    )

    assert response.status_code == 200, response.text
    assert "Nothing has been re-sent" in response.json()["effect"]

    retaken = await repository.claim_dispatch(
        db_session,
        tenant_id=TENANT,
        fired_event_id=fired,
        rule_id=RULE_ID,
        action_type="slack",
    )
    assert retaken is not None
    assert retaken.attempts == 2


async def test_the_verdict_records_who_gave_it(
    client: AsyncClient, db_session: AsyncSession
):
    """A `delivered` the dispatcher wrote is a 200 from Slack; a `delivered` an
    operator wrote is a person saying they saw the message. `resolved_by` is what
    keeps those two apart afterwards."""
    fired = uuid.uuid4()
    dispatch_id = await claim(db_session, fired_event_id=fired)
    await strand(db_session, dispatch_id, age_seconds=300)

    await client.post(
        f"/v1/dispatches/{dispatch_id}/resolve",
        json={"verdict": "delivered", "note": "saw it"},
    )

    row = await repository.get_dispatch(
        db_session, tenant_id=TENANT, fired_event_id=fired, action_type="slack"
    )
    assert row.resolved_by == "u_test"
    assert row.resolved_at is not None
    assert "saw it" in row.detail


async def test_an_outcome_the_dispatcher_observed_is_not_up_for_a_vote(
    client: AsyncClient, db_session: AsyncSession
):
    """Refuse anything not at `claimed`.

    A `delivered` row was settled by the provider's own response, and a `failed`
    row is already released. Letting a recollection overwrite either would
    replace something observed with something remembered.
    """
    dispatch_id = await claim(db_session)
    await repository.complete_dispatch(
        db_session, dispatch_id=dispatch_id, status="delivered", detail="posted (200)"
    )
    await db_session.commit()

    response = await client.post(
        f"/v1/dispatches/{dispatch_id}/resolve", json={"verdict": "failed"}
    )

    assert response.status_code == 409
    assert "already 'delivered'" in response.json()["detail"]


async def test_the_cutoff_comes_from_settings(
    client: AsyncClient, db_session: AsyncSession
):
    """The staleness line is configuration, not a literal in the query — a
    deployment with a slower webhook target has to be able to move it."""
    dispatch_id = await claim(db_session)
    await strand(db_session, dispatch_id, age_seconds=90)

    settings = get_settings()
    original = settings.stranded_dispatch_after_seconds
    try:
        settings.stranded_dispatch_after_seconds = 120.0
        assert await stranded_list(client) == []
        settings.stranded_dispatch_after_seconds = 30.0
        assert len(await stranded_list(client)) == 1
    finally:
        settings.stranded_dispatch_after_seconds = original


async def test_resolving_something_that_is_not_there(client: AsyncClient):
    response = await client.post(
        "/v1/dispatches/999999/resolve", json={"verdict": "delivered"}
    )
    assert response.status_code == 404
