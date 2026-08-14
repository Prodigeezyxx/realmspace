"""
What a lead turned into.

The other end of Phase 4's acceptance — *"the attribution ledger reconciles
booth-touch → outcome"* — and until now the repo had no outcome at all.
`data-model.md`'s node list stopped at `Frame`, there was no `outcome.`
namespace, and every attribution claim in the docs rested on a thing nothing
defined.

## Why an endpoint rather than only a CRM adapter

The obvious source of outcomes is the CRM, and that is where they will come from
once the adapters land. But the adapters are blocked on a per-tenant credential
store, and a ledger that cannot be filled in until then is a ledger nobody can
check the arithmetic of.

So outcomes are recorded here too, by an operator, with `source: "operator"` on
every row that arrives this way. That is not a placeholder: a client whose CRM we
do not integrate with still has a finance team who can tell us what closed, and
`roi-framework.md` §3's whole posture is that we report what we were told and
name where it came from.

## These are the client's figures, and they are labelled as such

Same treatment as `revenue_influenced` and `qualified_leads` on the session
config: a number a human typed is not a measurement, and every surface that
displays one says so. What realmspace measured is the booth touch; what the
client asserts is the deal.

## Idempotency

`outcome_id` is supplied by the caller, for the reason `event-bus-spec.md` §2
gives about `event_id` and `routers/consent.py` repeats about `consentId`: a POST
that times out gets resent, and the second attempt must be the same outcome
rather than a second deal appearing in a client's ROI ratio.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.auth.principal import Principal, require_operator
from app.consumers.ids import derive_event_id
from app.db import get_session
from app.schemas import EventIn, EventOut, _to_camel

router = APIRouter(prefix="/v1/outcomes", tags=["outcomes"])

RECORDED = "outcome.recorded"


class OutcomeIn(BaseModel):
    """What an operator (or, later, a CRM adapter) records against a lead."""

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    #: The caller's idempotency key — see the module docstring.
    outcome_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1)

    #: The join to the booth touch. The same key every adapter upserts on, which
    #: is why `handoff.lead` carries it: an outcome names the lead it came from
    #: in the vocabulary the destination already speaks, rather than in an
    #: internal id the CRM never saw.
    dedupe_key: str = Field(min_length=1, max_length=512)

    #: `open` is a real answer and not a missing one. A pipeline that has not
    #: closed yet is most of a B2B funnel at any moment, and forcing it to
    #: won/lost would make the ledger claim resolutions that have not happened.
    stage: Literal["won", "lost", "open"]

    #: Absent rather than zero when unknown. Same rule as everywhere else here:
    #: "we were not told" and "it was worth nothing" are different statements.
    value: float | None = Field(default=None, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)

    #: When it closed. Required for `won`/`lost`, because the attribution window
    #: is measured against it and an undated close cannot be judged in or out.
    closed_at: dt.datetime | None = None

    source: Literal["operator", "crm"] = "operator"
    #: The deal's id in whatever system it really lives in, so a disputed row can
    #: be traced out of here and into the client's own records.
    external_ref: str | None = Field(default=None, max_length=256)


@router.post(
    "",
    response_model=EventOut,
    status_code=status.HTTP_201_CREATED,
    summary="Record what a lead turned into",
)
async def record(
    body: OutcomeIn,
    principal: Principal = Depends(require_operator),
    session: AsyncSession = Depends(get_session),
) -> EventOut:
    """Append `outcome.recorded`. The graph write is a consumer's job.

    `require_operator` rather than `get_principal`, unlike consent capture: a
    kiosk needs to record a yes and runs on a device credential, but nothing on
    the floor has any business declaring that a deal closed. This changes the
    numerator of the ROI ratio a client is shown.
    """
    closed_at = body.closed_at
    if body.stage in ("won", "lost") and closed_at is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"a {body.stage!r} outcome needs closedAt — the attribution "
                "window is measured against it, and an undated close cannot be "
                "judged inside or outside one"
            ),
        )

    occurred_at = closed_at or dt.datetime.now(dt.timezone.utc)
    row, _ = await repository.append_event(
        session,
        EventIn(
            event_id=derive_event_id("outcome", principal.tenant_id, body.outcome_id),
            tenant_id=principal.tenant_id,
            session_id=body.session_id,
            type=RECORDED,
            payload={
                "outcome_id": body.outcome_id,
                "dedupe_key": body.dedupe_key,
                "stage": body.stage,
                "value": body.value,
                "currency": body.currency,
                "closed_at": closed_at.isoformat() if closed_at else None,
                "source": body.source,
                "external_ref": body.external_ref,
                # Recorded on the row rather than inferred later: who said this
                # is part of what an auditor is reading the ledger to find out.
                "recorded_by": principal.subject,
            },
            occurred_at=occurred_at,
        ),
    )
    return EventOut.model_validate(row)
