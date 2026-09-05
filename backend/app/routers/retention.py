"""
Asking for a retention purge, and seeing what one would do first.

## Admin, like erasure

`multi-tenant.md` §RBAC puts the irreversible things with Admin, and this is the
second most irreversible act in the system after an Article 17 erasure — it
empties a client's own record of their activations. An operator runs activations;
deciding that a window has expired is a different authority.

## It records the request and does nothing else

The same shape `routers/erasure.py` and `routers/consent.py` use, for the same
reason: a request that depended on the graph being reachable would fail at the
moment it matters. `consumers/retention.py` does the work, with the bus's retries
and dead-lettering behind it — and if it refuses, it parks on `/ops` with the
reason, where somebody finds out that a retention window is not being enforced.

## The dry run is not a courtesy

`GET /v1/retention/preview` answers "what would go" without writing anything.
The first thing any sane person wants from a delete is to see it first, and the
counts also make the three refusals legible before they are hit rather than as a
parked event afterwards.
"""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import plans, repository, retention
from app.auth.principal import Principal, require_admin
from app.db import get_session
from app.models import EventLog
from app.schemas import EventIn, _to_camel

router = APIRouter(prefix="/v1/retention", tags=["retention"])


class PurgePreview(BaseModel):
    """What a purge would do, and what would stop it."""

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    plan: str
    #: None when the tier states no retention window — in which case nothing is
    #: purged, because `app/plans.py` enforces only what the pricing sheet says.
    purge_through: dt.datetime | None
    events_to_purge: int
    highest_seq: int
    sessions_entirely_past: int
    #: Empty when it would run. Each entry is the sentence the consumer would
    #: raise, so a refusal is legible here rather than as a parked event later.
    blocked_by: list[str]
    already_purged_before_seq: int


class PurgeRequested(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    event_id: uuid.UUID
    detail: str


async def _preview(
    session: AsyncSession, *, tenant_id: str, consumer_names: list[str]
) -> PurgePreview:
    plan = await plans.plan_for(session, tenant_id)
    floor = plans.retention_floor(plan)
    watermark = await repository.purge_watermark(session, tenant_id=tenant_id)

    if floor is None:
        return PurgePreview(
            plan=plan, purge_through=None, events_to_purge=0, highest_seq=0,
            sessions_entirely_past=0, blocked_by=[],
            already_purged_before_seq=watermark,
        )

    through_seq = await retention.highest_purgeable_seq(
        session, tenant_id=tenant_id, before=floor
    )

    count = 0
    sessions_past = 0
    if through_seq:
        conditions = [
            EventLog.tenant_id == tenant_id,
            EventLog.occurred_at < floor,
            EventLog.purged_at.is_(None),
        ]
        for prefix in repository.PURGE_EXEMPT_PREFIXES:
            conditions.append(~EventLog.type.like(f"{prefix}%"))
        count = (
            await session.execute(
                select(func.count()).select_from(EventLog).where(*conditions)
            )
        ).scalar_one()
        sessions_past = len(
            (
                await session.execute(
                    select(EventLog.session_id)
                    .where(EventLog.tenant_id == tenant_id)
                    .group_by(EventLog.session_id)
                    .having(func.max(EventLog.occurred_at) < floor)
                )
            ).scalars().all()
        )

    blocked: list[str] = []
    if through_seq:
        for check in (
            lambda: retention.refuse_if_consumers_are_behind(
                session, tenant_id=tenant_id, through_seq=through_seq,
                consumers=consumer_names,
            ),
            lambda: retention.refuse_if_an_erasure_is_in_flight(
                session, tenant_id=tenant_id
            ),
            lambda: retention.refuse_if_a_dead_letter_points_into_the_window(
                session, tenant_id=tenant_id, through_seq=through_seq
            ),
        ):
            try:
                await check()
            except retention.NotYet as exc:
                # Every reason, not the first. A preview that stopped at one
                # would send somebody round the loop three times.
                blocked.append(str(exc))

    return PurgePreview(
        plan=plan,
        purge_through=floor,
        events_to_purge=count,
        highest_seq=through_seq,
        sessions_entirely_past=sessions_past,
        blocked_by=blocked,
        already_purged_before_seq=watermark,
    )


def _consumer_names() -> list[str]:
    from app.consumers.run import CONSUMER_CLASSES

    return [c.name for c in CONSUMER_CLASSES if c.name != "retention"]


@router.get("/preview", response_model=PurgePreview, summary="What a purge would do")
async def preview_purge(
    principal: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> PurgePreview:
    """Writes nothing. See the module docstring on why this is not a courtesy."""
    return await _preview(
        session, tenant_id=principal.tenant_id, consumer_names=_consumer_names()
    )


@router.post(
    "/purge",
    response_model=PurgeRequested,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enforce this tenant's retention window",
)
async def request_purge(
    principal: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> PurgeRequested:
    """202, not 200: the work happens in `consumers/retention.py`.

    **No scheduler**, matching the refusal recorded for the CSV export — a job
    runner quietly deleting a client's data on Tuesdays is worse than an operator
    action with a receipt and a name on it.

    The window itself is not a parameter. It comes from the tenant's plan, so an
    admin cannot purge further back than they bought by passing a number, and the
    audit answer to "why was this deleted" is always the same one.
    """
    event = EventIn(
        event_id=uuid.uuid4(),
        tenant_id=principal.tenant_id,
        # Tenant-wide rather than per activation: the window is a property of
        # the plan. A sentinel rather than a real session, as the erasure
        # request uses.
        session_id="__tenant__",
        type=retention.PURGE_REQUESTED,
        payload={"requested_by": principal.subject},
        occurred_at=dt.datetime.now(dt.timezone.utc),
    )
    row, _created = await repository.append_event(session, event)
    await session.commit()
    return PurgeRequested(
        event_id=row.event_id,
        detail=(
            "Recorded. The purge runs on the bus and refuses if any consumer is "
            "behind, an erasure is in flight, or a dead letter points into the "
            "window — check /ops if nothing happens."
        ),
    )
