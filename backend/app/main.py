"""
realmspace backend — the durable event bus (docs/event-bus-spec.md) and the
spatial graph (docs/data-model.md).

Two stores: Postgres holds the append-only event log, Neo4j holds the graph.

Run it:
    backend/.venv/bin/uvicorn app.main:app --reload --port 8000
    → http://localhost:8000/docs
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import get_settings
from app.db import engine
from app.graph import driver as graph_driver
from app.routers import events

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Open the Neo4j driver once at boot, close it on shutdown.

    Connecting here rather than at import time means a wrong password fails the
    boot loudly instead of surfacing as a 500 on the first graph request.
    """
    await graph_driver.connect()
    try:
        yield
    finally:
        await graph_driver.disconnect()


app = FastAPI(
    title="realmspace backend",
    description=(
        "Append-only event bus (event-bus-spec.md) + spatial graph "
        "(data-model.md). Multi-tenant: every write and read is tenant-scoped."
    ),
    version="0.2.0",
    lifespan=lifespan,
)

# The Next.js dashboard talks to this from the browser. Wide open on the edge
# box because it is on localhost behind no network; tighten before any deploy.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"] if settings.env == "local" else [],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(events.router)


@app.get("/health", tags=["meta"])
async def health() -> dict[str, object]:
    """Liveness for both stores. Reports each separately so a graph outage is
    distinguishable from a log outage — they fail independently."""
    stores: dict[str, str] = {}

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        stores["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001 — health must report, not raise
        stores["postgres"] = f"error: {type(exc).__name__}"

    try:
        await graph_driver.get_driver().verify_connectivity()
        stores["neo4j"] = "ok"
    except Exception as exc:  # noqa: BLE001
        stores["neo4j"] = f"error: {type(exc).__name__}"

    healthy = all(v == "ok" for v in stores.values())
    return {"status": "ok" if healthy else "degraded", "env": settings.env, "stores": stores}
