"""
What each pricing tier allows, and the refusal when a request would exceed it.

Roadmap Phase 6: "Billing hooks (Stripe) + plan metering/limits". This is the
limits half. `multi-tenant.md` §5 — "Multi-tenant makes them enforceable" — has
named four of them since Phase 1: cameras, retention window, # agents,
# integrations. None existed. A tenant on any tier could declare eight cameras,
arm forty rules, connect five CRMs and read a year of log, so the pricing sheet
was a document rather than a constraint.

The spend half was already built: `app/cost.py` meters `action_unit` on every
dispatched action and `llm_tokens` on a model-backed Ask, with derived event ids
so a replay cannot inflate a client's bill.

## Only what `gtm.md` writes down is enforced

Every number below is a phrase in `gtm.md`'s tier table, quoted in the comment
beside it. Where the table is silent the limit is `None`, and `None` means **not
enforced** — the same rule `roi-framework.md` imposes on revenue and P3's cost
tile imposed on spend: a default invented here would be a commercial term
invented on a client's behalf, and it would look exactly like one somebody had
agreed.

Two of those silences are surprising enough to be worth reading twice, and they
are the pricing sheet's gaps rather than this module's:

  * **Booth has no agent cap while Pavilion is capped at two.** "2 custom
    agents" appears only in the Pavilion row. The literal reading inverts the
    ladder. Filling it in is one line here and no migration.
  * **No tier states an integration count at all**, so that limit never fires.
    The key is carried anyway, because the check that reads it is where the
    number goes; a limit added later with no call site is a config key nothing
    reads.

## Two client-facing reads are deliberately *not* clamped

The retention window is applied to `GET /v1/events` and to the activation
listing. It is refused on the other two log readers, and the reason is the same
one in both:

  * **`GET /v1/handoffs`** is a delivery cursor, not a way to browse your data.
    Its contract is "a client that saves `nextSince` after a successful import
    cannot skip a lead", and a floor puts a silent gap in exactly that path for
    anyone who pauses an import past their window. Worse, it reads *every*
    withdrawal to redact withdrawn leads: clamping would hide an old withdrawal
    and un-redact a name somebody asked us to remove.
  * **`GET /v1/ledger/{session}`** is the audit artifact, built from the log
    precisely because "an auditor asks what was known and when". A ledger that
    quietly dropped the touches older than the window would report a different
    attributed revenue for the same activation depending on the day it was run,
    and it reads withdrawals tenant-wide for the same redaction.

Retention is a commercial term about reading your data back. Neither of those is
that, and in both the failure mode is a privacy regression rather than a
withheld feature.

## Ask queries are not counted, deliberately

"unlimited Ask the Room" is in the Pavilion row, which implies Booth is capped
and gives no number — so the limit is `None` like the others. It is also the one
limit whose *usage* cannot honestly be counted today: the only record of an Ask
is the `cost.metered` event, and `routers/ask.py` meters only when the provider
reported tokens. With open decision 2 still open the deterministic provider
reports none, so every Ask this system currently answers is invisible to the
meter. A counter that reads zero for real traffic is worse than no counter, so
`GET /v1/plan` reports this one as unknown and says why.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
from dataclasses import asdict, dataclass

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class PlanLimits:
    """One tier's ceilings. `None` is "unlimited, and not enforced".

    Frozen for the reason `Principal` is: nothing downstream should be able to
    raise its own ceiling.
    """

    #: Cameras a session may declare.
    max_cameras: int | None
    #: How far back a *client* may read their own log. Not how far back the
    #: system processes it — see `retention_floor`.
    retention_days: int | None
    #: Rule documents a tenant may hold. `gtm.md` calls them "custom agents".
    max_agents: int | None
    #: Rows in `tenant_integration`. No tier states one; see the docstring.
    max_integrations: int | None
    #: Ask questions per calendar month. No tier states one, and usage is not
    #: countable today; see the docstring.
    max_ask_queries: int | None


#: The four tiers in `gtm.md` § "Packaging & pricing", by their row.
PLANS: dict[str, PlanLimits] = {
    # "Single camera kit … 30-day data retention"
    "booth": PlanLimits(
        max_cameras=1,
        retention_days=30,
        max_agents=None,       # the row does not mention agents
        max_integrations=None,  # no row does
        max_ask_queries=None,   # no row gives a number
    ),
    # "Up to 4 cameras + sensor fusion … unlimited Ask the Room, 2 custom
    # agents … 90-day retention + raw export"
    "pavilion": PlanLimits(
        max_cameras=4,
        retention_days=90,
        max_agents=2,
        max_integrations=None,
        max_ask_queries=None,   # "unlimited" is stated for this tier
    ),
    # "Multi-venue, multi-city, cross-activation benchmarking, white-labeled
    # client portal, on-prem option" — no count of anything.
    "campaign": PlanLimits(None, None, None, None, None),
    # "Kit + dashboard, white-label, 80/20 split" — likewise.
    "partner": PlanLimits(None, None, None, None, None),
}

#: What a self-serve signup lands on. The entry tier, because handing out a
#: higher one for free is a commercial decision nobody made.
DEFAULT_PLAN = "booth"

#: What an organisation that existed before migration 0012 is on. Putting every
#: existing tenant on the entry tier would start refusing the four-camera
#: sessions and demo data this repo was built against — a regression dressed as
#: a feature. See the migration.
LEGACY_PLAN = "pavilion"

#: Human labels for a refusal that has to name a tier somebody could buy.
PLAN_LABELS = {
    "booth": "Booth",
    "pavilion": "Pavilion",
    "campaign": "Campaign",
    "partner": "Partner kit",
}

#: Cheapest first, so a refusal can name the next tier that would allow the
#: request rather than making the operator read the pricing page.
PLAN_ORDER = ("booth", "pavilion", "campaign", "partner")


class PlanLimitExceeded(HTTPException):
    """**402**, not 403.

    `principal.requires()` already owns 403 and means "your role may not do
    this", whose remedy is to ask an admin for a capability. This means "your
    plan does not include this", whose remedy is commercial. Telling a client on
    the wrong tier to go and ask for a role sends them somewhere that cannot
    help them.

    The detail names the tier, the limit, what they are using and the cheapest
    tier that would allow it — the same shape `requires()` uses when it names
    the missing capability rather than the roles that hold it.
    """

    def __init__(
        self,
        *,
        plan: str,
        limit_name: str,
        limit: int,
        requested: int,
        noun: str,
        noun_singular: str | None = None,
    ) -> None:
        upgrade = _cheapest_allowing(limit_name, requested, above=plan)
        remedy = (
            f" The {PLAN_LABELS[upgrade]} plan allows "
            f"{_describe(getattr(PLANS[upgrade], limit_name))}."
            if upgrade
            else " No higher tier states a larger number; talk to us."
        )
        counted = (noun_singular or noun) if limit == 1 else noun
        super().__init__(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                f"the {PLAN_LABELS.get(plan, plan)} plan allows {limit} {counted}"
                f"; this would make {requested}.{remedy}"
            ),
        )


def _describe(value: int | None) -> str:
    return "any number" if value is None else str(value)


def _cheapest_allowing(limit_name: str, requested: int, *, above: str) -> str | None:
    """The cheapest tier **above `above`** whose `limit_name` admits `requested`.

    Only upwards, and that restriction is load-bearing rather than tidy. Two
    tiers state no agent count, so a search over the whole table answers
    Pavilion's two-agent refusal with "the Booth plan allows any number" — which
    is true, is the pricing sheet's own gap, and would be read as advice to
    downgrade. A refusal may suggest an upgrade or say there is none; it must not
    suggest paying less to get more.
    """
    for name in PLAN_ORDER[PLAN_ORDER.index(above) + 1 :]:
        ceiling = getattr(PLANS[name], limit_name)
        if ceiling is None or ceiling >= requested:
            return name
    return None


def limits_for(plan: str) -> PlanLimits:
    """The ceilings for a tier.

    An unknown string raises rather than resolving to something permissive. The
    failure of a limits system should be a refusal, not an exemption — and
    migration 0012's CHECK means the only way to reach this is a code change
    that added a tier here and not there.
    """
    try:
        return PLANS[plan]
    except KeyError:
        raise ValueError(
            f"unknown plan {plan!r}; known plans are {', '.join(PLAN_ORDER)}"
        ) from None


async def plan_for(session: AsyncSession, tenant_id: str) -> str:
    """Which tier this organisation is on.

    A tenant with **no registry row** predates migration 0011 and resolves to
    `LEGACY_PLAN`, which is what 0012's backfill gives every row that did exist.
    The two have to agree or the same organisation would be on two different
    tiers depending on whether anyone had written its name down.

    This is not a hole a new organisation can climb through: `POST
    /v1/auth/signup` has written a `tenant` row since 0011, and `app.auth.seed`
    writes one too.
    """
    from app.auth.models import Tenant  # local: app.auth imports app.db, not us

    row = (
        await session.execute(select(Tenant.plan).where(Tenant.tenant_id == tenant_id))
    ).scalar_one_or_none()
    return row or LEGACY_PLAN


def enforce(
    *,
    plan: str,
    limit_name: str,
    requested: int,
    noun: str,
    noun_singular: str | None = None,
) -> None:
    """Refuse if `requested` would exceed this tier's `limit_name`.

    Call it immediately before the write, so a refusal happens before anything
    lands. `requested` is the count *after* the write would succeed, not the
    count before it — every call site computes the post-state, because "you have
    4" and "this would make 5" are different sentences and only the second is
    the one being refused.
    """
    ceiling = getattr(limits_for(plan), limit_name)
    if ceiling is not None and requested > ceiling:
        raise PlanLimitExceeded(
            plan=plan,
            limit_name=limit_name,
            limit=ceiling,
            requested=requested,
            noun=noun,
            noun_singular=noun_singular,
        )


def retention_floor(plan: str, *, now: dt.datetime | None = None) -> dt.datetime | None:
    """The oldest `occurred_at` a client on this tier may read back, or None.

    **Measured on `occurred_at`, not `received_at`.** Every other event-time
    decision here — rule cooldowns, insight windows, drift windows — is on
    `occurred_at`, and a batch buffered through an outage and replayed a week
    later should not earn a week of extra retention for having been late.

    Applied by routers only. `repository.read_events` is what all the consumers
    poll with, and clamping it would make the tracker skip events and a replay
    build a different graph. Retention is what a *client* may read back, not
    what the system may process — and no log row is deleted by any of this; see
    the roadmap bullet on why a purge is its own piece of work.
    """
    days = limits_for(plan).retention_days
    if days is None:
        return None
    return (now or dt.datetime.now(dt.timezone.utc)) - dt.timedelta(days=days)


# ── the CLI ───────────────────────────────────────────────────────────────────
#
# Changing a plan is deliberately **not** an endpoint. A self-serve upgrade with
# no payment path is a free upgrade; the thing that should own this is the
# Stripe webhook that does not exist yet. Until it does, moving a tenant between
# tiers is an operator action on the box, in the shape of `app.auth.seed`.


async def _set_plan(tenant_id: str, plan: str) -> None:
    from app.auth.models import Tenant
    from app.db import SessionLocal

    limits_for(plan)  # refuse a typo here rather than at the CHECK

    async with SessionLocal() as session:
        row = await session.get(Tenant, tenant_id)
        if row is None:
            raise SystemExit(
                f"no organisation {tenant_id!r} in the registry. "
                "Sign it up, or seed it with `python -m app.auth.seed`."
            )
        was = row.plan
        row.plan = plan
        await session.commit()
        print(f"{tenant_id}: {was} → {plan}")
        for field, value in asdict(limits_for(plan)).items():
            print(f"  {field:<18} {_describe(value)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    sub = parser.add_subparsers(dest="command", required=True)
    setter = sub.add_parser("set", help="move an organisation to a tier")
    setter.add_argument("tenant_id")
    setter.add_argument("plan", choices=PLAN_ORDER)
    args = parser.parse_args()
    if args.command == "set":
        asyncio.run(_set_plan(args.tenant_id, args.plan))


if __name__ == "__main__":
    main()
