"""
Where somebody asks to be erased, not merely to be forgotten going forward.

`consent-and-identity.md` §5 lists it beside withdrawal and means something
stronger: *"Right to erasure (GDPR Art. 17 / CCPA) → tenant-scoped erasure job
that removes a `Contact` and all PII edges, keeping only anonymised aggregates."*

## It appends two events, and the first one is the ordinary withdrawal

A withdrawal already drops the link, redacts the graph Contact and retracts the
record from every CRM that received it. An erasure needs all of that plus the
log, so it asks for the existing path by name — `consent.withdrawn` with
`reason: "erasure_request"`, which `routers/consent.py` has accepted since Phase
4 opened and which derives its own event id from the reason, so an erasure after
a change of mind is a second fact rather than a duplicate of the first.

Then `erasure.requested`, which `consumers/erasure.py` acts on once the
retraction has actually landed.

## Admin, not operator

`multi-tenant.md` §RBAC puts the irreversible things with Admin, and this is the
most irreversible act in the system: it rewrites the append-only log. An operator
running an activation captures and withdraws consent all day; erasing a person
from every store is a different authority.

## It records the request and does nothing else

Same shape as `routers/consent.py` and for the same reason: an erasure request
that depended on the graph or a CRM being reachable would be a request that can
fail at the moment it matters most. Recording it is unconditional; carrying it
out is a consumer's job, with the bus's retries and dead-lettering behind it —
and if it cannot be carried out, it parks on `/ops` where a human finds out,
rather than returning a 500 to somebody exercising a legal right.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.auth.principal import Principal, require_admin
from app.consumers.ids import derive_event_id
from app.db import get_session
from app.schemas import EventIn, EventOut, _to_camel

router = APIRouter(prefix="/v1/erasure", tags=["consent"])

WITHDRAWN = "consent.withdrawn"
REQUESTED = "erasure.requested"


class ErasureIn(BaseModel):
    """Who is to be erased, and who asked.

    The three identifiers are optional and at least one is required, the same
    rule `routers/consent.py` states about a withdrawal and for the same reason:
    the request has to work from whichever handle the person exercising the right
    actually has.
    """

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    session_id: str = Field(min_length=1)
    contact_id: str | None = None
    consent_id: str | None = None
    anon_id: str | None = None

    #: Who received the request and is accountable for it. Required, unlike on a
    #: withdrawal: an erasure is answered to a regulator, and "the system did it"
    #: is not an answer. It names a person, not a data subject, so it survives
    #: the erasure it authorises.
    requested_by: str = Field(min_length=1, max_length=128)
    #: Free text — a ticket reference, a case number, the wording of the request.
    #: Never the subject's own details; the consumer's receipt explains why.
    note: str | None = Field(default=None, max_length=1000)
    requested_at: dt.datetime | None = None


@router.post(
    "",
    response_model=EventOut,
    status_code=status.HTTP_201_CREATED,
    summary="Erase a person from every store (GDPR Art. 17)",
)
async def request_erasure(
    body: ErasureIn,
    principal: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> EventOut:
    """Append the withdrawal and the erasure request. The consumers do the rest.

    Returns the `erasure.requested` event, because that is the one whose progress
    a caller follows — the withdrawal beside it is the first step of carrying it
    out, not a separate thing that was asked for.
    """
    subject = body.contact_id or body.consent_id or body.anon_id
    if subject is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "an erasure must name at least one of contactId, consentId or "
                "anonId — whichever the person exercising the right can be "
                "identified by"
            ),
        )

    requested_at = body.requested_at or dt.datetime.now(dt.timezone.utc)

    await repository.append_event(
        session,
        EventIn(
            # The same id `POST /v1/consent/withdraw` would derive for this
            # subject and reason, deliberately: an erasure that follows an
            # ordinary withdrawal must not make the re-anonymiser redo work it
            # has already done, and the log deduping it is how that is arranged.
            event_id=derive_event_id(
                "consent_withdrawn", principal.tenant_id, subject, "erasure_request"
            ),
            tenant_id=principal.tenant_id,
            session_id=body.session_id,
            type=WITHDRAWN,
            payload={
                "consent_id": body.consent_id,
                "contact_id": body.contact_id,
                "anon_id": body.anon_id,
                "reason": "erasure_request",
                "withdrawn_at": requested_at.isoformat(),
            },
            occurred_at=requested_at,
        ),
    )

    row, _ = await repository.append_event(
        session,
        EventIn(
            # Derived from the subject alone: asking twice is one right exercised
            # twice, and a timestamped id would put a second request on the log
            # for the consumer to redo against a person already erased.
            event_id=derive_event_id("erasure", principal.tenant_id, subject),
            tenant_id=principal.tenant_id,
            session_id=body.session_id,
            type=REQUESTED,
            payload={
                "consent_id": body.consent_id,
                "contact_id": body.contact_id,
                "anon_id": body.anon_id,
                "requested_by": body.requested_by,
                "note": body.note,
                "requested_at": requested_at.isoformat(),
            },
            occurred_at=requested_at,
        ),
    )
    return EventOut.model_validate(row)
