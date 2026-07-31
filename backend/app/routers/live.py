"""
The live WebSocket — `event_log` out to the browser.

Roadmap Phase 1: "WebSocket: perception → bus → dashboard /live (replace mock
feed)". Week 1 task 1.8, whose stated outcome is "events in browser <500ms".

## Contract

Path and message envelope deliberately match the `postgres-track`'s
`/v1/ws/{tenant_id}/{session_id}`, so POD 3 writes one integration for task 1.13
rather than one per backend. The bake-off should be decided by the graph store,
not by whose URL the dashboard happened to be wired to.

    → {"type":"hello","tenantId":…,"sessionId":…,"sinceSeq":N,"replay":[event,…]}
    → {"type":"event","event":{…}}      for each event thereafter

Events are the canonical `RealmEvent` shape from
`dashboard/src/lib/contracts/events.ts` — camelCase, ms-epoch timestamps.

## One deliberate difference: `since_seq` is a real cursor

The other track replays from 0 on every connect, capped at 200. That means a
client that reconnects re-receives everything it already had, and on a busy
session the oldest events fall off the cap and are simply lost.

Here the client passes the highest `seq` it has already seen and gets only what
came after. Conference wifi drops constantly — this is the same cursor
discipline the consumers use (`event-bus-spec.md` §2), carried to the last hop,
so a reconnect has no gap and no duplicates.

## Not authenticated yet

The tenant comes from the URL, so anyone who can reach this process can watch
any tenant's live feed by guessing an id. Same as the other track today. This is
Week 1 tasks 1.6/1.7; when they land, `tenant_id` comes from the verified token
and the path parameter is checked against it — a dependency change here, not a
reshape of the contract.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app import db
from app.hub import hub
from app.schemas import to_wire

log = logging.getLogger(__name__)

router = APIRouter(tags=["live"])

#: How many events a reconnecting client may catch up on in the hello frame.
#: Bounded so one very stale client cannot pull the whole log into memory; a
#: client further behind than this should page through GET /events instead.
MAX_REPLAY = 500


@router.websocket("/v1/ws/{tenant_id}/{session_id}")
async def live_socket(
    websocket: WebSocket,
    tenant_id: str,
    session_id: str,
    since_seq: int = Query(
        0, ge=0, alias="since_seq", description="highest seq the client already has"
    ),
) -> None:
    await websocket.accept()

    # Catch up BEFORE joining the room. Joining first would let a broadcast
    # interleave with the replay and arrive out of order — or twice, if it is
    # also in the replay window.
    session: AsyncSession
    async with db.SessionLocal() as session:
        missed = await repository.read_events(
            session,
            tenant_id=tenant_id,
            since_seq=since_seq,
            limit=MAX_REPLAY,
            session_id=session_id,
        )

    await websocket.send_json(
        {
            "type": "hello",
            "tenantId": tenant_id,
            "sessionId": session_id,
            "sinceSeq": since_seq,
            "replay": [to_wire(e) for e in missed],
        }
    )

    await hub.join(tenant_id, session_id, websocket)
    log.info("socket joined %s/%s (replayed %s)", tenant_id, session_id, len(missed))

    try:
        while True:
            # We do not interpret anything the client sends. Reading is how a
            # disconnect is detected — without it the socket would sit open and
            # the room would leak.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 — never let one socket kill the endpoint
        log.exception("socket error on %s/%s", tenant_id, session_id)
    finally:
        await hub.leave(tenant_id, session_id, websocket)
        log.info("socket left %s/%s", tenant_id, session_id)
