"""
Which tier this organisation is on, what it allows, and what they are using.

`multi-tenant.md` §5's plan limits are refusals scattered across four write
paths. Without this endpoint a **402** is the first time an operator learns a
limit exists — on the morning of an activation, halfway through the wizard,
which is where this product has already decided a lying button is worse than no
button.

## Read-only, and no upgrade control

Changing a plan is not an endpoint. A self-serve upgrade with no payment path is
a free upgrade; the thing that should own it is the Stripe webhook that does not
exist yet (`roadmap.md` Phase 6 → Billing, still 🔲). Until it does, the move is
`python -m app.plans set <tenant> <plan>` on the box. An "Upgrade" button here
would be the report's dead "Export PDF" again: a primary call to action wired to
nothing, discovered in front of the client.

## Usage is computed live, from the sources the checks read

There is no counters table. `GET /v1/sessions` already argues this for the
scorecard — a second place that counts a tenant's rules is a second answer to
"how many agents do I have", and the one nobody looks at is the one that goes
wrong.

`asks` is the exception and it is reported as **unknown**, not zero. The only
record of an Ask is the `cost.metered` event `routers/ask.py` writes, and it
writes one only when the provider reported tokens; with open decision 2 open the
deterministic provider reports none, so every Ask this system currently answers
is invisible to the meter. A counter that reads zero for real traffic would be
worse than saying so.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app import plans, repository
from app.auth.principal import Principal, require_reader
from app.db import get_session
from app.graph import repository as graph_repo
from app.graph.driver import get_graph_session
from app.schemas import _to_camel

router = APIRouter(prefix="/v1/plan", tags=["plan"])


class LimitOut(BaseModel):
    """One limit: the ceiling, what is against it, and whether it is enforced.

    `limit: null` is "unlimited, and not enforced" — which for most of these is
    because `gtm.md`'s tier table states no number, not because the tier is
    generous. `stated` carries that distinction so a UI can say "not part of
    this plan's terms" instead of the confident "unlimited" the value alone
    would suggest.
    """

    limit: int | None = None
    used: int | None = None
    #: False when nothing counts this. See the module docstring on `asks`.
    counted: bool = True
    note: str | None = None

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)


class PlanOut(BaseModel):
    plan: str
    label: str
    #: Days of their own log a client may read back. Null is unlimited.
    retention_days: int | None = None
    cameras: LimitOut
    agents: LimitOut
    integrations: LimitOut
    asks: LimitOut

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)


@router.get("", response_model=PlanOut, summary="This organisation's plan and usage")
async def get_plan(
    principal: Principal = Depends(require_reader),
    session: AsyncSession = Depends(get_session),
    graph=Depends(get_graph_session),
) -> PlanOut:
    plan = await plans.plan_for(session, principal.tenant_id)
    limits = plans.limits_for(plan)

    rules = await repository.list_rules(session, tenant_id=principal.tenant_id)
    integrations = await repository.list_integrations(
        session, tenant_id=principal.tenant_id
    )

    # Per activation, not per organisation — see the query.
    cameras_used = await graph_repo.max_cameras_for_tenant(
        graph, tenant_id=principal.tenant_id
    )

    return PlanOut(
        plan=plan,
        label=plans.PLAN_LABELS.get(plan, plan),
        retention_days=limits.retention_days,
        cameras=LimitOut(limit=limits.max_cameras, used=cameras_used),
        agents=LimitOut(limit=limits.max_agents, used=len(rules)),
        integrations=LimitOut(limit=limits.max_integrations, used=len(integrations)),
        asks=LimitOut(
            limit=limits.max_ask_queries,
            used=None,
            counted=False,
            note=(
                "Only a model-backed Ask is metered, and no AI provider is "
                "configured (roadmap.md open decision 2), so questions answered "
                "from the query catalogue leave no record to count."
            ),
        ),
    )


#: Belt and braces for the reader who came looking for the numbers themselves.
@router.get("/tiers", summary="What every tier allows")
async def get_tiers(
    principal: Principal = Depends(require_reader),
) -> dict[str, dict]:
    """Every tier's ceilings, so a client on Booth can see what Pavilion buys.

    Not gated on admin: it is the public pricing sheet in machine form, and a
    viewer who can read a 402 naming the next tier up can already see this.
    """
    return {
        name: {
            "label": plans.PLAN_LABELS[name],
            # camelCase like every other response this API returns. The
            # dataclass fields are snake_case and a raw `asdict` would make this
            # the one endpoint a browser client has to special-case.
            **{_to_camel(k): v for k, v in asdict(plans.PLANS[name]).items()},
        }
        for name in plans.PLAN_ORDER
    }
