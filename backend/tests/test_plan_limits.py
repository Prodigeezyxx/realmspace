"""
Plan limits — the enforceable half of `roadmap.md` Phase 6's billing bullet.

`multi-tenant.md` §5 has named four of these since Phase 1 and none existed, so
the `gtm.md` tier table was a document rather than a constraint. `app/plans.py`
is the one place that says what a tier allows; these are the refusals.

Three of the assertions here are about a bug rather than a feature, and they are
the ones worth keeping:

  * **replace at the cap succeeds.** `PUT` is create-or-replace, so a count
    taken on every write refuses an operator editing their *own* second rule for
    being the third. That is the one a real operator hits first.
  * **the retention floor does not reach the consumers.** It is applied by
    routers; clamping `repository.read_events` would make the tracker skip
    events and a replay build a different graph.
  * **a tenant with no registry row resolves to the legacy tier**, matching
    migration 0012's backfill. If the two disagreed, the same organisation would
    be on two plans depending on whether anybody had written its name down.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
from httpx import ASGITransport, AsyncClient
from neo4j import AsyncSession as GraphSession
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import plans, repository
from app.auth.models import AuthUser, Tenant
from app.auth.tokens import issue_token
from app.db import get_session
from app.main import app
from app.schemas import EventIn
from tests.conftest import as_tenant

T = "t_test"
S = "s_plan"


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
async def _scope_to_test_tenant(db_session: AsyncSession) -> AsyncIterator[None]:
    await as_tenant(db_session, T)
    yield


async def _register(db_session: AsyncSession, plan: str) -> None:
    """Put `T` in the registry on a tier.

    Without a row `plan_for` answers `LEGACY_PLAN`, which is the pre-0011 case
    and has its own test below. Everything else here is about a tenant whose
    plan somebody actually chose.
    """
    db_session.add(Tenant(tenant_id=T, name="Test Org", created_by=None, plan=plan))
    await db_session.commit()


def _client(db_session: AsyncSession, token: str) -> AsyncClient:
    async def override_get_session() -> AsyncIterator[AsyncSession]:
        yield db_session
        await db_session.commit()

    app.dependency_overrides[get_session] = override_get_session
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


async def _as(db_session: AsyncSession, role: str) -> AsyncClient:
    user_id = f"u_{role}"
    db_session.add(
        AuthUser(
            user_id=user_id,
            email=f"{user_id}@floats.demo",
            display_name=role,
            tenant_id=T,
            role=role,
        )
    )
    await db_session.commit()
    return _client(db_session, issue_token(subject=user_id, tenant_id=T, role=role))


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def rule_body(rule_id: str) -> dict:
    return {
        "ruleId": rule_id,
        "name": rule_id,
        "triggerType": "spatial.dwell",
        "condition": {"type": "threshold", "count": 5, "windowSec": 30},
        "action": {"type": "slack", "channel": "#floor", "message": "five at the arch"},
        "enabled": True,
    }


# ── the table itself ──────────────────────────────────────────────────────────


def test_every_number_comes_from_the_pricing_sheet() -> None:
    """`gtm.md`'s tier table, and nothing invented beside it.

    The two surprising entries are asserted rather than left to be discovered:
    Booth has no agent cap while Pavilion is capped at two, and no tier states
    an integration count at all. Both are silences in the pricing sheet. This
    test fails the day somebody fills one in, which is when the decision should
    be visible rather than a diff nobody reviewed.
    """
    assert plans.PLANS["booth"].max_cameras == 1
    assert plans.PLANS["booth"].retention_days == 30
    assert plans.PLANS["pavilion"].max_cameras == 4
    assert plans.PLANS["pavilion"].retention_days == 90
    assert plans.PLANS["pavilion"].max_agents == 2

    assert plans.PLANS["booth"].max_agents is None, "gtm.md states no Booth agent count"
    assert all(
        plans.PLANS[name].max_integrations is None for name in plans.PLAN_ORDER
    ), "no tier states an integration count"

    # Campaign and Partner state no count of anything.
    assert plans.PLANS["campaign"] == plans.PlanLimits(None, None, None, None, None)


def test_an_unknown_plan_is_a_refusal_not_an_exemption() -> None:
    """The failure of a limits system must not be unlimited access."""
    with pytest.raises(ValueError, match="unknown plan"):
        plans.limits_for("enterprise")


def test_a_refusal_names_the_tier_that_would_allow_it() -> None:
    with pytest.raises(plans.PlanLimitExceeded) as caught:
        plans.enforce(
            plan="booth",
            limit_name="max_cameras",
            requested=3,
            noun="cameras",
            noun_singular="camera",
        )
    assert caught.value.status_code == 402
    assert "Booth" in caught.value.detail
    assert "Pavilion" in caught.value.detail
    # "allows 1 cameras" on the entry tier's most common refusal.
    assert "1 camera;" in caught.value.detail


def test_a_refusal_never_suggests_paying_less() -> None:
    """Two tiers state no agent count, so a search over the whole table answers
    Pavilion's two-agent refusal with "the Booth plan allows any number" — the
    pricing sheet's own gap, read as advice to downgrade."""
    with pytest.raises(plans.PlanLimitExceeded) as caught:
        plans.enforce(
            plan="pavilion", limit_name="max_agents", requested=3, noun="agents"
        )
    assert "Booth" not in caught.value.detail
    assert "Campaign" in caught.value.detail


def test_no_ceiling_refuses_nothing() -> None:
    plans.enforce(plan="campaign", limit_name="max_cameras", requested=99, noun="cameras")


# ── cameras ───────────────────────────────────────────────────────────────────


async def test_booth_is_one_camera_and_pavilion_is_four(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    await _register(db_session, "booth")
    operator = await _as(db_session, "operator")

    at_the_cap = await operator.post(
        "/v1/sessions", json={"sessionId": S, "cameraCount": 1}
    )
    assert at_the_cap.status_code == 200, at_the_cap.text

    over = await operator.post("/v1/sessions", json={"sessionId": S, "cameraCount": 2})
    assert over.status_code == 402, over.text
    assert "Pavilion" in over.json()["detail"]

    await db_session.execute(
        text("UPDATE tenant SET plan = 'pavilion' WHERE tenant_id = :t"), {"t": T}
    )
    await db_session.commit()

    now_allowed = await operator.post(
        "/v1/sessions", json={"sessionId": S, "cameraCount": 4}
    )
    assert now_allowed.status_code == 200, now_allowed.text


async def test_the_camera_list_counts_too(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """Either field alone can carry a booth past its tier.

    A save that declares two `cameras` and leaves `cameraCount` unset is
    ordinary — the wizard writes the list — and counting only the integer would
    let it through.
    """
    await _register(db_session, "booth")
    operator = await _as(db_session, "operator")

    res = await operator.post(
        "/v1/sessions",
        json={
            "sessionId": S,
            "cameras": [{"id": "cam-1", "label": "Front"}, {"id": "cam-2", "label": "Side"}],
        },
    )
    assert res.status_code == 402, res.text


async def test_a_refused_save_changes_nothing(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """The check runs before the first write.

    `upsert_session` rewrites the measurement parameters the report divides by,
    so a save refused halfway would leave an activation scored against numbers
    nobody agreed to.
    """
    await _register(db_session, "pavilion")
    operator = await _as(db_session, "operator")

    await operator.post(
        "/v1/sessions", json={"sessionId": S, "cameraCount": 2, "activationCost": 1000.0}
    )

    refused = await operator.post(
        "/v1/sessions", json={"sessionId": S, "cameraCount": 9, "activationCost": 5000.0}
    )
    assert refused.status_code == 402

    after = await operator.get(f"/v1/sessions/{S}")
    assert after.json()["activationCost"] == 1000.0


# ── agents ────────────────────────────────────────────────────────────────────


async def test_pavilion_allows_two_agents(db_session: AsyncSession) -> None:
    await _register(db_session, "pavilion")
    analyst = await _as(db_session, "analyst")

    for rule_id in ("r_one", "r_two"):
        res = await analyst.put(f"/v1/rules/{rule_id}", json=rule_body(rule_id))
        assert res.status_code == 200, res.text

    third = await analyst.put("/v1/rules/r_three", json=rule_body("r_three"))
    assert third.status_code == 402, third.text
    assert "agents" in third.json()["detail"]


async def test_editing_an_existing_rule_at_the_cap_is_not_a_third(
    db_session: AsyncSession,
) -> None:
    """The bug a real operator hits first.

    A count taken on every `PUT` refuses somebody editing their own second rule
    for being the third.
    """
    await _register(db_session, "pavilion")
    analyst = await _as(db_session, "analyst")

    for rule_id in ("r_one", "r_two"):
        await analyst.put(f"/v1/rules/{rule_id}", json=rule_body(rule_id))

    edited = rule_body("r_two") | {"name": "renamed"}
    res = await analyst.put("/v1/rules/r_two", json=edited)
    assert res.status_code == 200, res.text
    assert res.json()["name"] == "renamed"


async def test_a_disabled_rule_still_counts(db_session: AsyncSession) -> None:
    """It is a rule the operator can arm without asking anybody."""
    await _register(db_session, "pavilion")
    analyst = await _as(db_session, "analyst")

    await analyst.put("/v1/rules/r_one", json=rule_body("r_one") | {"enabled": False})
    await analyst.put("/v1/rules/r_two", json=rule_body("r_two") | {"enabled": False})

    third = await analyst.put("/v1/rules/r_three", json=rule_body("r_three"))
    assert third.status_code == 402


async def test_booth_has_no_agent_cap(db_session: AsyncSession) -> None:
    """The pricing sheet's silence, enforced as written rather than guessed at.

    This is the inverted ladder `test_every_number_comes_from_the_pricing_sheet`
    records. It is here so the behaviour is visible, not because it is right.
    """
    await _register(db_session, "booth")
    analyst = await _as(db_session, "analyst")

    for rule_id in ("r_one", "r_two", "r_three", "r_four"):
        res = await analyst.put(f"/v1/rules/{rule_id}", json=rule_body(rule_id))
        assert res.status_code == 200, res.text


# ── retention ─────────────────────────────────────────────────────────────────


def _uuid(seed: str) -> str:
    """A stable uuid per name, so an assertion can name the event it expects."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"plan-limits/{seed}"))


async def _seed(db_session: AsyncSession, *, event_id: str, days_ago: int) -> None:
    await repository.append_event(
        db_session,
        EventIn(
            event_id=_uuid(event_id),
            tenant_id=T,
            session_id=S,
            type="spatial.dwell",
            payload={"anonId": "P-001", "zoneId": "z1", "durationSeconds": 12.0},
            occurred_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago),
        ),
    )
    await db_session.commit()


async def test_the_log_read_is_clamped_and_says_so(db_session: AsyncSession) -> None:
    await _register(db_session, "booth")
    await _seed(db_session, event_id="e_recent", days_ago=2)
    await _seed(db_session, event_id="e_old", days_ago=45)

    reader = await _as(db_session, "viewer")
    res = await reader.get("/events")
    assert res.status_code == 200, res.text

    ids = [row["eventId"] for row in res.json()]
    assert ids == [_uuid("e_recent")]
    # An empty or short page must be distinguishable from a quiet activation.
    assert "X-Retention-Floor" in res.headers


async def test_a_longer_plan_returns_the_same_event(db_session: AsyncSession) -> None:
    await _register(db_session, "pavilion")
    await _seed(db_session, event_id="e_recent", days_ago=2)
    await _seed(db_session, event_id="e_old", days_ago=45)

    reader = await _as(db_session, "viewer")
    ids = [row["eventId"] for row in (await reader.get("/events")).json()]
    assert ids == [_uuid("e_recent"), _uuid("e_old")]


async def test_the_floor_never_reaches_a_consumer(db_session: AsyncSession) -> None:
    """Retention is what a client may read back, not what the system processes.

    `repository.read_events` is the shape all sixteen consumers poll with. If
    the floor were applied there the tracker would skip an event during a replay
    of old history and the graph would come out different — which is the failure
    this separation exists to prevent, and it would be silent.
    """
    await _register(db_session, "booth")
    await _seed(db_session, event_id="e_old", days_ago=45)

    rows = await repository.read_events(db_session, tenant_id=T)
    assert [str(r.event_id) for r in rows] == [_uuid("e_old")]


async def test_an_activation_outside_the_window_is_not_listed(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """Otherwise the benchmark is handed an activation that scores as nothing,
    which is indistinguishable from one that genuinely measured nothing."""
    from app.graph import repository as graph_repo

    await _register(db_session, "booth")
    now = dt.datetime.now(dt.timezone.utc)
    for session_id, days_ago in (("s_recent", 3), ("s_old", 60)):
        await graph_repo.upsert_session(
            graph_session,
            tenant_id=T,
            session_id=session_id,
            venue="Test Hall",
            started_at=(now - dt.timedelta(days=days_ago)).isoformat(),
        )
    await graph_repo.upsert_session(
        graph_session, tenant_id=T, session_id="s_undated", venue="Test Hall"
    )

    reader = await _as(db_session, "viewer")
    listed = [row["sessionId"] for row in (await reader.get("/v1/sessions")).json()]

    assert "s_recent" in listed
    assert "s_old" not in listed
    # A configured activation nobody has run holds no measurement to retain.
    assert "s_undated" in listed


# ── the registry fallback ─────────────────────────────────────────────────────


async def test_a_tenant_with_no_registry_row_is_on_the_legacy_plan(
    db_session: AsyncSession,
) -> None:
    """Migration 0011 added the registry; everything older has no row.

    0012 backfills every row that *did* exist to `pavilion`, so the fallback has
    to agree — or the same organisation would be on two tiers depending on
    whether anybody had written its name down.
    """
    assert await plans.plan_for(db_session, "t_never_registered") == plans.LEGACY_PLAN
    assert plans.LEGACY_PLAN == "pavilion"


async def test_signup_lands_on_the_entry_tier(db_session: AsyncSession) -> None:
    assert plans.DEFAULT_PLAN == "booth"


# ── the plan endpoint ─────────────────────────────────────────────────────────


async def test_the_plan_endpoint_shows_where_a_tenant_stands(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """A 402 must not be the first time an operator learns a limit exists."""
    await _register(db_session, "pavilion")

    # Two clients, because `multi-tenant.md` §3 splits the two jobs: an operator
    # runs activations and an analyst builds agents. Both hold `read`, so either
    # can see the plan.
    analyst = await _as(db_session, "analyst")
    operator = await _as(db_session, "operator")

    armed = await analyst.put("/v1/rules/r_one", json=rule_body("r_one"))
    assert armed.status_code == 200, armed.text
    configured = await operator.post(
        "/v1/sessions", json={"sessionId": S, "cameraCount": 3}
    )
    assert configured.status_code == 200, configured.text

    res = await analyst.get("/v1/plan")
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["plan"] == "pavilion"
    assert body["retentionDays"] == 90
    assert body["cameras"] == {"limit": 4, "used": 3, "counted": True, "note": None}
    assert body["agents"]["limit"] == 2
    assert body["agents"]["used"] == 1


async def test_ask_usage_is_reported_unknown_rather_than_zero(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """With no AI provider the deterministic answers leave no meter to count.

    Reporting 0 would look like a client who had never asked anything.
    """
    await _register(db_session, "booth")
    analyst = await _as(db_session, "analyst")

    asks = (await analyst.get("/v1/plan")).json()["asks"]
    assert asks["used"] is None
    assert asks["counted"] is False
    assert "provider" in asks["note"]


async def test_the_tier_sheet_is_readable_by_a_viewer(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """A client on Booth can see what Pavilion buys. It is the public pricing
    sheet in machine form, and the 402 already names the next tier up."""
    await _register(db_session, "booth")
    viewer = await _as(db_session, "viewer")

    tiers = (await viewer.get("/v1/plan/tiers")).json()
    assert set(tiers) == set(plans.PLAN_ORDER)
    assert tiers["pavilion"]["maxCameras"] == 4
