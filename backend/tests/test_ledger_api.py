"""
Recording an outcome, and reading the ledger out.

The builder's arithmetic is covered in `test_ledger.py`. What is only true here
is the plumbing an auditor actually touches: that a retried POST does not create
a second deal, that the model and window come from the activation's own
configuration rather than from a query parameter somebody could shop in, and that
the CSV is a complete flattening rather than a lossy one.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from neo4j import AsyncSession as GraphSession
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.auth.models import AuthUser
from app.auth.tokens import issue_token
from app.db import get_session
from app.graph import repository as graph_repo
from app.main import app
from app.schemas import EventIn

#: `t_test`, not the shared `t_floats`, because these tests write Session nodes.
#: `conftest.py`'s graph fixtures only wipe the `t_test*` tenants, so an
#: activation seeded anywhere else is left behind in the dev Neo4j instance —
#: the one thing that file is careful never to do.
TENANT = "t_test"
S = "s_ledger"
TOUCH = dt.datetime(2026, 8, 13, 10, 0, 0, tzinfo=dt.timezone.utc)
KEY = f"{TENANT}:sam@example.com"


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """An operator client for `t_test`. Operator, because recording an outcome
    changes the numerator of the ROI ratio a client is shown."""
    from tests.conftest import as_tenant

    await as_tenant(db_session, TENANT)
    db_session.add(
        AuthUser(
            user_id="u_test",
            email="ledger@floats.demo",
            display_name="Ledger",
            tenant_id=TENANT,
            role="admin",
        )
    )
    await db_session.commit()

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        yield db_session
        await db_session.commit()

    app.dependency_overrides[get_session] = override_get_session
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={
            "Authorization": (
                f"Bearer {issue_token(subject='u_test', tenant_id=TENANT, role='admin')}"
            )
        },
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


async def seed_session(graph_session: GraphSession, *, window_days: int = 90) -> None:
    await graph_repo.upsert_session(
        graph_session,
        tenant_id=TENANT,
        session_id=S,
        client="Acme",
        activation_cost=1000.0,
        attribution_model="influenced",
        attribution_window_days=window_days,
        revenue_influenced=99.0,
    )


async def seed_handoff(db_session: AsyncSession) -> None:
    await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(),
            tenant_id=TENANT,
            session_id=S,
            type="handoff.lead",
            payload={
                "schema": "realmspace.lead_handoff/v1",
                "stage": "final",
                "dedupe_key": KEY,
                "anon_id": "P-012",
                "emitted_at": TOUCH.isoformat(),
                "contact": {"id": "ct_1", "email": "sam@example.com", "name": "Sam"},
                "spatial_intent": {
                    "zones_visited": ["Entry", "Pod"],
                    "dwell_seconds_total": 160.0,
                    "lead_score": 74,
                    "lead_score_basis": "spatial/v1",
                },
                "consent": {
                    "tier": "T2",
                    "basis": "explicit_optin",
                    "copy_version": "consent-en-2026-08",
                    "captured_at": TOUCH.isoformat(),
                },
            },
            occurred_at=TOUCH,
        ),
    )
    await db_session.commit()


def outcome_body(**over) -> dict:
    body = {
        "outcomeId": "o_1",
        "sessionId": S,
        "dedupeKey": KEY,
        "stage": "won",
        "value": 5000.0,
        "currency": "USD",
        "closedAt": "2026-09-01T10:00:00+00:00",
    }
    body.update(over)
    return body


# ── recording ─────────────────────────────────────────────────────────────────


async def test_an_outcome_lands_on_the_log_with_who_recorded_it(
    client: AsyncClient, db_session: AsyncSession
):
    response = await client.post("/v1/outcomes", json=outcome_body())

    assert response.status_code == 201, response.text
    events = await repository.read_events(
        db_session, tenant_id=TENANT, session_id=S, type="outcome.recorded", limit=10
    )
    assert len(events) == 1
    # Who said this is part of what an auditor is reading the ledger to find out.
    assert events[0].payload["recorded_by"] == "u_test"
    assert events[0].payload["source"] == "operator"


async def test_a_retried_post_is_one_deal(
    client: AsyncClient, db_session: AsyncSession
):
    """The same argument as consent capture, with more at stake: a duplicated
    outcome doubles the numerator of the ROI ratio a client is shown."""
    first = await client.post("/v1/outcomes", json=outcome_body())
    second = await client.post("/v1/outcomes", json=outcome_body())

    assert second.json()["eventId"] == first.json()["eventId"]
    events = await repository.read_events(
        db_session, tenant_id=TENANT, session_id=S, type="outcome.recorded", limit=10
    )
    assert len(events) == 1


async def test_a_closed_deal_without_a_close_date_is_refused_with_the_reason(
    client: AsyncClient,
):
    response = await client.post(
        "/v1/outcomes", json=outcome_body(stage="won", closedAt=None)
    )

    assert response.status_code == 422
    assert "attribution window" in response.json()["detail"]


async def test_an_open_opportunity_needs_no_close_date(client: AsyncClient):
    """`open` is a real answer — most of a B2B pipeline at any moment."""
    response = await client.post(
        "/v1/outcomes", json=outcome_body(stage="open", closedAt=None, value=None)
    )

    assert response.status_code == 201


# ── reading ───────────────────────────────────────────────────────────────────


async def test_the_ledger_reconciles_a_touch_to_a_deal(
    client: AsyncClient, db_session: AsyncSession, graph_session: GraphSession
):
    await seed_session(graph_session)
    await seed_handoff(db_session)
    await client.post("/v1/outcomes", json=outcome_body())

    body = (await client.get(f"/v1/ledger/{S}")).json()

    assert len(body["rows"]) == 1
    assert body["rows"][0]["outcomes"][0]["in_window"] is True
    assert body["totals"]["revenue_influenced"] == 5000.0


async def test_the_window_comes_from_the_activation_not_the_caller(
    client: AsyncClient, db_session: AsyncSession, graph_session: GraphSession
):
    """roi-framework.md §5: the model and window are agreed before the doors
    open. A ledger whose window could be chosen at read time is one you can shop
    in for a better number."""
    await seed_session(graph_session, window_days=30)
    await seed_handoff(db_session)
    await client.post("/v1/outcomes", json=outcome_body())

    body = (await client.get(f"/v1/ledger/{S}?attributionWindowDays=90")).json()

    assert body["attribution_window_days"] == 30
    # ~19 days, so still inside 30 — the assertion is that the *stated* window is
    # the session's, not the query string's.
    assert body["rows"][0]["outcomes"][0]["in_window"] is True


async def test_the_operators_stated_revenue_travels_beside_the_measured_one(
    client: AsyncClient, db_session: AsyncSession, graph_session: GraphSession
):
    """Not replaced by it. Where a client's typed-in figure and the ledger
    disagree, the disagreement is information."""
    await seed_session(graph_session)
    await seed_handoff(db_session)
    await client.post("/v1/outcomes", json=outcome_body())

    body = (await client.get(f"/v1/ledger/{S}")).json()

    assert body["revenue_influenced_stated"] == 99.0
    assert body["totals"]["revenue_influenced"] == 5000.0


async def test_the_csv_carries_every_lead_including_those_without_a_deal(
    client: AsyncClient, db_session: AsyncSession, graph_session: GraphSession
):
    await seed_session(graph_session)
    await seed_handoff(db_session)
    await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(),
            tenant_id=TENANT,
            session_id=S,
            type="handoff.lead",
            payload={
                "stage": "final",
                "dedupe_key": f"{TENANT}:kim@example.com",
                "anon_id": "P-900",
                "emitted_at": TOUCH.isoformat(),
                "contact": {"id": "ct_2", "email": "kim@example.com", "name": "Kim"},
                "spatial_intent": {"zones_visited": ["Entry"]},
                "consent": {"tier": "T1"},
            },
            occurred_at=TOUCH,
        ),
    )
    await db_session.commit()
    await client.post("/v1/outcomes", json=outcome_body())

    response = await client.get(f"/v1/ledger/{S}?format=csv")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]

    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == 2
    by_email = {row["contact_email"]: row for row in rows}
    assert by_email["sam@example.com"]["outcome_value"] == "5000.0"
    # The lead still in its sales cycle is a row, not an omission — dropping it
    # would overstate the conversion rate of everything left.
    assert by_email["kim@example.com"]["outcome_id"] == ""
    assert by_email["sam@example.com"]["zones_visited"] == "Entry > Pod"


async def test_a_withdrawn_lead_is_redacted_in_the_export_too(
    client: AsyncClient, db_session: AsyncSession, graph_session: GraphSession
):
    await seed_session(graph_session)
    await seed_handoff(db_session)
    await client.post("/v1/outcomes", json=outcome_body())
    await client.post(
        "/v1/consent/withdraw",
        json={"sessionId": S, "anonId": "P-012", "reason": "visitor_request"},
    )

    rows = list(
        csv.DictReader(io.StringIO((await client.get(f"/v1/ledger/{S}?format=csv")).text))
    )

    assert len(rows) == 1
    assert rows[0]["contact_email"] == ""
    assert rows[0]["contact_name"] == ""
    assert rows[0]["withdrawn"] == "True"
    # The touch and its basis survive — that is what the row is for.
    assert rows[0]["consent_copy_version"] == "consent-en-2026-08"
    assert rows[0]["outcome_value"] == "5000.0"


async def test_an_activation_with_nothing_in_it_reads_as_empty_not_broken(
    client: AsyncClient, graph_session: GraphSession
):
    await seed_session(graph_session)

    body = (await client.get(f"/v1/ledger/{S}")).json()

    assert body["rows"] == []
    assert body["totals"]["leads"] == 0
    assert body["truncated"] is False


async def test_a_withdrawal_taken_at_another_activation_still_redacts_this_one(
    client: AsyncClient, db_session: AsyncSession, graph_session: GraphSession
):
    """A Contact id is stable across activations; a withdrawal is filed under one.

    So a visitor who attended two events and withdrew at the second has that
    withdrawal recorded against the second session alone. The first activation's
    ledger has to honour it anyway — reading withdrawals scoped to the session
    being reported would print the name of the one person who had explicitly
    asked that it not be.
    """
    await seed_session(graph_session)
    await seed_handoff(db_session)

    await client.post(
        "/v1/consent/withdraw",
        json={
            # Somewhere else entirely, months later.
            "sessionId": "s_a_later_activation",
            "contactId": "ct_1",
            "reason": "visitor_request",
        },
    )

    row = (await client.get(f"/v1/ledger/{S}")).json()["rows"][0]

    assert row["withdrawn"] is True
    assert row["contact_email"] is None
    assert row["contact_name"] is None
    # The touch and its basis survive, as they do for any withdrawal.
    assert row["consent_copy_version"] == "consent-en-2026-08"
