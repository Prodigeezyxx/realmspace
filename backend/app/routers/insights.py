"""
The insights, and the events each one rests on.

`floats-agent`'s spec, adopted verbatim: *"shown on `/live`; click-through opens
the underlying events"*, with the acceptance clause *"insights on `/live` trace
to source events"*. This serves both halves — the insights, and the events a
reader follows a citation to.

## Why the citations are resolved here rather than in the browser

The refs are seqs on the durable log, and the browser would otherwise need a
second call per insight, with its own idea of which events count and its own way
of failing when one is missing. One call returns the insight and, on request, the
events it cites, so what an operator clicks through to is the thing the backend
computed the number from.

## Anonymous, and stays that way

An insight carries no PII by construction (`app/llm/digest.py` reads only
`spatial.*` and `surface.interaction`), so the cited events are anonymous too.
`require_reader` is right for the same reason the ROI tiles are: this is the
room, not a person.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.auth.principal import Principal, require_reader
from app.db import get_session

router = APIRouter(prefix="/v1/insights", tags=["insights"])

GENERATED = "insight.generated"


@router.get("", summary="What the room has been told about itself")
async def list_insights(
    session_id: str | None = Query(default=None, alias="sessionId"),
    since: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
    principal: Principal = Depends(require_reader),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rows = await repository.read_events(
        session,
        tenant_id=principal.tenant_id,
        since_seq=since,
        limit=limit,
        session_id=session_id,
        type=GENERATED,
    )
    return {
        "insights": [
            {
                "seq": row.seq,
                "sessionId": row.session_id,
                "occurredAt": row.occurred_at.isoformat(),
                **row.payload,
            }
            for row in rows
        ],
        "count": len(rows),
        "nextSince": rows[-1].seq if rows else since,
        "more": len(rows) == limit,
    }


@router.get(
    "/{seq}/sources",
    summary="The events an insight was computed from",
)
async def get_sources(
    seq: int,
    principal: Principal = Depends(require_reader),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Resolve one insight's citations against the log.

    A ref that does not resolve is reported as `missing` rather than dropped. An
    insight quietly showing four of its five sources would be a weaker claim
    presented as the original one, and the log is append-only — so a missing ref
    means something is wrong with the insight, not with the log, and the operator
    should be able to see that.
    """
    insight = await repository.get_event_by_seq(
        session, tenant_id=principal.tenant_id, seq=seq
    )
    if insight is None or insight.type != GENERATED:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no insight at seq {seq} for this tenant",
        )

    sources = []
    for ref in insight.payload.get("refs") or []:
        row = await repository.get_event_by_seq(
            session, tenant_id=principal.tenant_id, seq=ref.get("seq", -1)
        )
        if row is None:
            sources.append({"seq": ref.get("seq"), "missing": True})
            continue
        sources.append(
            {
                "seq": row.seq,
                "eventId": str(row.event_id),
                "type": row.type,
                "occurredAt": row.occurred_at.isoformat(),
                "payload": row.payload,
                "missing": False,
            }
        )

    return {
        "seq": insight.seq,
        "text": insight.payload.get("text"),
        "window": insight.payload.get("window"),
        "sources": sources,
        "count": len(sources),
    }
