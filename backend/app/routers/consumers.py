"""
Make a consumer catch up now, instead of waiting for its next poll.

Ported from the `postgres-track`, which has had `POST /v1/consumers/graph_writer/drain`
since early on. Two things earn it a place here:

- **Operations.** Somebody who has just fixed the cause of a parked event wants
  the pipeline to move immediately, not on the next tick. That is the natural
  companion to the review queue at `/ops`.
- **Testing the recovery story.** "Chaos test recovers cleanly" is much easier
  to assert against when a test can say *drain now* rather than sleeping past a
  poll interval and hoping.

Two differences from the other track's version, both deliberate.

**It drains the running instance, not a fresh one.** The tracker's output depends
on state accumulated from the stream — where each person is, how close they came
to each zone. Building a new instance to drain with would advance the same cursor
while emitting whatever an empty state implies, which is a subtler version of the
bug the HITL retry rules exist to avoid. If consumers are not running in this
process, this says so rather than substituting one.

**It is authenticated and tenant-scoped.** Theirs takes `tenantId` as a query
parameter; here the tenant comes from the credential, like everywhere else, so
there is no parameter to point at somebody else's backlog.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict

from app.auth.principal import Principal, require_operator
from app.consumers import run as consumer_run
from app.consumers.run import CONSUMER_CLASSES
from app.schemas import _to_camel

router = APIRouter(prefix="/v1/consumers", tags=["consumers"])


class DrainResult(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    consumer: str
    tenant_id: str
    #: Events read in this pass — including ones skipped as not this consumer's
    #: business, and ones parked, because both advance the cursor.
    processed: int


@router.post(
    "/{name}/drain",
    response_model=DrainResult,
    summary="Process this consumer's backlog for your tenant, now",
)
async def drain(
    name: str,
    principal: Principal = Depends(require_operator),
) -> DrainResult:
    """Operator-only: this runs handlers with real side effects.

    Safe to call while the background loop is also polling. Handlers are
    idempotent and `advance_cursor` only ever moves forward (it takes the
    GREATEST of the two), so the worst case is that one batch is processed twice
    — which is the same guarantee a crash mid-batch already relies on.

    Returns the count for one pass, not a promise that the backlog is empty: a
    pass is bounded by `consumer_batch_size`, so a long backlog wants more than
    one call. Saying "processed 200" and letting the caller decide is more honest
    than looping here until the number reaches zero, which is how a request ends
    up open for minutes.
    """
    if not any(cls.name == name for cls in CONSUMER_CLASSES):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no consumer named {name!r}",
        )

    consumer = consumer_run.running(name)
    if consumer is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"{name!r} is not running in this process, so there is nothing "
                "to drain. Draining a freshly built instance would advance the "
                "real cursor while emitting what an empty state implies."
            ),
        )

    processed = await consumer.process_batch(principal.tenant_id)
    return DrainResult(
        consumer=name, tenant_id=principal.tenant_id, processed=processed
    )
