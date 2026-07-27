"""realmspace edge API — FastAPI entrypoint."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app import __version__, bus, db, graph_writer
from app.config import get_settings
from app.hub import hub
from app.models import (
    AuthResolveResponse,
    EventBatch,
    GraphSnapshot,
    HealthResponse,
    RealmEvent,
    RealmEventInput,
)

_loop: asyncio.AbstractEventLoop | None = None


def _on_bus_event(event: RealmEvent) -> None:
    """Sync bus subscriber → graph writer + schedule WS broadcast."""
    graph_writer.on_bus_event(event)
    if _loop and _loop.is_running():
        asyncio.run_coroutine_threadsafe(hub.broadcast(event), _loop)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _loop
    _loop = asyncio.get_running_loop()
    db.init_db()
    bus.subscribe(_on_bus_event)
    # Catch up any backlog for the default tenant
    settings = get_settings()
    graph_writer.process_pending(settings.default_tenant_id)
    yield


app = FastAPI(
    title="realmspace edge API",
    version=__version__,
    description="Append-only event bus + relational graph projection for the edge kit.",
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__, database=db.backend_name())


@app.post("/v1/events", response_model=RealmEvent)
def post_event(body: RealmEventInput) -> RealmEvent:
    try:
        return bus.append(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/v1/events/batch", response_model=list[RealmEvent])
def post_events(body: EventBatch) -> list[RealmEvent]:
    try:
        return bus.append_many(body.events)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/v1/events", response_model=list[RealmEvent])
def get_events(
    tenant_id: str = Query(..., alias="tenantId"),
    session_id: str = Query(..., alias="sessionId"),
    after_seq: int = Query(0, alias="afterSeq"),
    limit: int = Query(500, ge=1, le=5000),
    type: list[str] | None = Query(None),
) -> list[RealmEvent]:
    return bus.read(
        tenant_id,
        session_id,
        after_seq=after_seq,
        limit=limit,
        types=type,
    )


@app.get("/v1/graph/{tenant_id}/{session_id}", response_model=GraphSnapshot)
def get_graph(tenant_id: str, session_id: str) -> GraphSnapshot:
    graph_writer.process_pending(tenant_id)
    return graph_writer.snapshot(tenant_id, session_id)


@app.post("/v1/consumers/graph_writer/drain")
def drain_graph_writer(tenant_id: str = Query(..., alias="tenantId")) -> dict[str, Any]:
    n = graph_writer.process_pending(tenant_id)
    return {"processed": n, "consumer": "graph_writer", "tenantId": tenant_id}


@app.get("/v1/auth/resolve", response_model=AuthResolveResponse)
def auth_resolve(email: str = Query(...)) -> AuthResolveResponse:
    """RBAC skeleton: resolve email → org → role (local seed users)."""
    row = db.fetchone("SELECT * FROM auth_users WHERE email = ?", (email.lower(),))
    if not row:
        # Unknown users land as viewer on default tenant for local demos
        return AuthResolveResponse(
            userId="u_anonymous",
            email=email.lower(),
            displayName=None,
            orgId=get_settings().default_tenant_id,
            role="viewer",
        )
    role = row["role"]
    if role not in ("admin", "operator", "analyst", "viewer"):
        role = "viewer"
    return AuthResolveResponse(
        userId=row["user_id"],
        email=row["email"],
        displayName=row["display_name"],
        orgId=row["org_id"],
        role=role,  # type: ignore[arg-type]
    )


@app.websocket("/v1/ws/{tenant_id}/{session_id}")
async def ws_session(websocket: WebSocket, tenant_id: str, session_id: str) -> None:
    await hub.connect(tenant_id, session_id, websocket)
    # Catch-up: send recent events so the client can hydrate
    recent = bus.read(tenant_id, session_id, after_seq=0, limit=200)
    await websocket.send_json(
        {
            "type": "hello",
            "tenantId": tenant_id,
            "sessionId": session_id,
            "replay": [e.model_dump() for e in recent],
        }
    )
    try:
        while True:
            # Keepalive / client pings; we ignore payload content for now
            await websocket.receive_text()
    except WebSocketDisconnect:
        await hub.disconnect(tenant_id, session_id, websocket)
