"""
realmspace backend — the durable event bus (docs/event-bus-spec.md) and the
spatial graph (docs/data-model.md).

Two stores: Postgres holds the append-only event log, Neo4j holds the graph.

Run it:
    backend/.venv/bin/uvicorn app.main:app --reload --port 8000
    → http://localhost:8000/docs
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import get_settings
from app.consumers.run import build_all
from app.db import engine
from app.graph import driver as graph_driver
from app.routers import events, live

log = logging.getLogger(__name__)
settings = get_settings()


#: Live consumer tasks, so /health can report them and shutdown can cancel them.
_consumer_tasks: list[asyncio.Task[None]] = []


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Open the Neo4j driver and start the bus consumers; tear both down after.

    Connecting the driver here rather than at import time means a wrong password
    fails the boot loudly instead of surfacing as a 500 on the first graph
    request.

    The consumers run as tasks in this process — event-bus-spec.md §4 puts both
    the tracker and the graph writer on the edge, and sharing one event loop is
    what justified the async stack in the first place.
    """
    await graph_driver.connect()

    if settings.consumers_enabled:
        for consumer in build_all():
            _consumer_tasks.append(
                asyncio.create_task(consumer.run_forever(), name=f"consumer:{consumer.name}")
            )

    try:
        yield
    finally:
        for task in _consumer_tasks:
            task.cancel()
        # Wait for them to actually stop, so nothing is mid-write when the
        # driver closes underneath it.
        await asyncio.gather(*_consumer_tasks, return_exceptions=True)
        _consumer_tasks.clear()
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
app.include_router(live.router)


@app.get("/health", tags=["meta"])
async def health() -> dict[str, object]:
    """Liveness for both stores. Reports each separately so a graph outage is
    distinguishable from a log outage — they fail independently."""
    stores: dict[str, str] = {}

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        stores["postgres"] = "ok"
    except Exception:  # noqa: BLE001 — health must report, not raise
        # Deliberately not the exception class or message: /health is
        # unauthenticated, and there is no reason to tell an unauthenticated
        # caller which driver failed and how.
        log.exception("postgres health check failed")
        stores["postgres"] = "error"

    try:
        await graph_driver.get_driver().verify_connectivity()
        stores["neo4j"] = "ok"
    except Exception:  # noqa: BLE001
        log.exception("neo4j health check failed")
        stores["neo4j"] = "error"

    # A consumer task that has died silently is the failure mode that loses data
    # without anything looking broken, so report them by name.
    consumers = {
        (t.get_name().removeprefix("consumer:")): ("running" if not t.done() else "stopped")
        for t in _consumer_tasks
    }

    healthy = all(v == "ok" for v in stores.values()) and all(
        v == "running" for v in consumers.values()
    )
    return {
        "status": "ok" if healthy else "degraded",
        "env": settings.env,
        "stores": stores,
        "consumers": consumers or "disabled",
    }
