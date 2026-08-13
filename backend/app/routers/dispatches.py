"""
The other human-in-the-loop queue: dispatches nobody can say the outcome of.

`/v1/dead-letters` covers failures — an action that raised, three times, with a
traceback. This covers the case that is not a failure and not a success:
`repository.claim_dispatch` writes a row before the outbound call goes out, and
if the process dies between those two moments the row is left reading `claimed`.
Whether Slack got the message is then genuinely unknown.

The dispatcher is right to refuse it. Retrying risks the double-post the whole
`rule_dispatch` table exists to prevent, and rewriting it to `failed` on restart
would be a guess dressed as a fact. What was missing is the other half of that
reasoning: `claim_dispatch` says the row "needs a human rather than a guess", and
`get_dispatch` said it was "for `/ops`" — but no route read it, so the human was
never asked. A crash mid-call therefore blocked its `(fired_event_id,
action_type)` pair permanently. It was the one state in the Phase 3 chain with
no exit.

## The two verdicts, and what they do not do

An operator answers one question: *did the message arrive?* They answer it by
looking at the channel, the inbox, or the screen — not from anything this system
can see, which is why it is a person being asked.

- **it arrived** → `delivered`. Closed, and `claim_dispatch` still refuses it, so
  a replay of that firing cannot post a second time.
- **it did not** → `failed`. Released, because `claim_dispatch` takes a `failed`
  row back — nothing was delivered, so there is nothing to duplicate.

Neither verdict re-sends anything. The dispatcher's cursor is long past that
firing, so a redelivery needs the firing replayed, which is a cursor rewind and
deliberately an operator action rather than a button — the same reason
`DispatchConsumer.retryable` is False and the tracker's dead letters decline
their retry. This endpoint records what is true; it does not decide to act.

## Tenant isolation

Nothing here filters by tenant, and that is deliberate in the same way
`dead_letters.py` says: `rule_dispatch` is under forced row-level security and
`get_principal` scopes the transaction, so another tenant's row is invisible to
every query in this file.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.auth.principal import Principal, require_operator, require_reader
from app.config import get_settings
from app.db import get_session
from app.schemas import _to_camel

router = APIRouter(prefix="/v1/dispatches", tags=["dispatches"])


class StrandedDispatchOut(BaseModel):
    """A dispatch whose outcome nobody knows, with what the operator needs to
    go and find out."""

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    id: int
    rule_id: str
    #: The rule's name at the time of reading, or None if it has since been
    #: deleted. Shown because `r_entry_crowd` does not tell an operator which
    #: message to go looking for, and "Entrance crowding → ping ops" does.
    rule_name: str | None
    action_type: str
    attempts: int
    created_at: dt.datetime
    #: How long it has been stuck. Computed here rather than in the browser so
    #: the panel and the API agree about the clock.
    stranded_for_seconds: float


class VerdictIn(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    #: `delivered` closes it; `failed` releases it. See the module docstring —
    #: neither re-sends.
    verdict: Literal["delivered", "failed"]
    #: How the operator knows. Optional, and worth asking for: "found it in
    #: #ops at 14:32" is the difference between an audit trail and a shrug.
    note: str | None = Field(default=None, max_length=1000)


class VerdictOut(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    id: int
    status: str
    resolved_by: str
    #: Says in words what the verdict did to the row, because "failed" on its own
    #: reads like the system failed rather than like a human reporting that a
    #: message never arrived.
    effect: str


DELIVERED_EFFECT = (
    "Closed. A replay of this firing will not post again — the claim stays taken."
)
FAILED_EFFECT = (
    "Released. Nothing has been re-sent: the dispatcher is long past this firing, "
    "so a redelivery needs the firing replayed from a rewound cursor."
)


@router.get(
    "/stranded",
    response_model=list[StrandedDispatchOut],
    summary="Dispatches stuck at `claimed`, whose outcome is unknown",
)
async def list_stranded(
    older_than_seconds: float | None = Query(
        None,
        description=(
            "Override the staleness cutoff. Defaults to "
            "`settings.stranded_dispatch_after_seconds`; lower it only to "
            "diagnose, since every in-flight dispatch is `claimed` by design."
        ),
    ),
    principal: Principal = Depends(require_reader),
    session: AsyncSession = Depends(get_session),
) -> list[StrandedDispatchOut]:
    settings = get_settings()
    cutoff = (
        settings.stranded_dispatch_after_seconds
        if older_than_seconds is None
        else older_than_seconds
    )
    rows = await repository.list_stranded_dispatches(
        session, older_than_seconds=cutoff
    )

    now = dt.datetime.now(dt.timezone.utc)
    out: list[StrandedDispatchOut] = []
    for row in rows:
        rule = await repository.get_rule(
            session, tenant_id=principal.tenant_id, rule_id=row.rule_id
        )
        out.append(
            StrandedDispatchOut(
                id=row.id,
                rule_id=row.rule_id,
                rule_name=rule.name if rule else None,
                action_type=row.action_type,
                attempts=row.attempts,
                created_at=row.created_at,
                stranded_for_seconds=(now - row.created_at).total_seconds(),
            )
        )
    return out


@router.post(
    "/{dispatch_id}/resolve",
    response_model=VerdictOut,
    summary="Record whether the message actually arrived",
)
async def resolve(
    dispatch_id: int,
    body: VerdictIn,
    principal: Principal = Depends(require_operator),
    session: AsyncSession = Depends(get_session),
) -> VerdictOut:
    """Operator-only, because this decides whether a replay may post again.

    Refuses any row that is not at `claimed`. A `delivered` row is already
    settled by evidence better than a recollection, and a `failed` row is already
    released — overwriting either would replace something the system observed
    with something a person remembered.
    """
    row = await repository.get_dispatch_by_id(session, dispatch_id=dispatch_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    if row.status != "claimed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"dispatch {dispatch_id} is already {row.status!r}, which the "
                "dispatcher recorded from the call itself. There is nothing for a "
                "human to decide."
            ),
        )

    note = body.note or "no note given"
    await repository.resolve_dispatch(
        session,
        dispatch_id=dispatch_id,
        status=body.verdict,
        resolved_by=principal.subject,
        detail=f"resolved by {principal.subject}: {note}"[:2000],
    )

    return VerdictOut(
        id=dispatch_id,
        status=body.verdict,
        resolved_by=principal.subject,
        effect=DELIVERED_EFFECT if body.verdict == "delivered" else FAILED_EFFECT,
    )
