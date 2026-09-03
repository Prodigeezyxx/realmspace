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

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import get_settings
from app.consumers import run as consumer_run
from app.consumers.run import build_all
from app.db import engine
from app.graph import driver as graph_driver
from app.routers import (
    ask,
    auth,
    calibration,
    consent,
    consumers,
    dead_letters,
    dispatches,
    erasure,
    events,
    followups,
    handoffs,
    insights,
    integrations,
    kiosk,
    ledger,
    live,
    outcomes,
    plan,
    rules,
    retention as retention_router,
    sessions,
    share,
    touch,
    users,
)

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
            # Registered as well as started, so POST /v1/consumers/{name}/drain
            # reaches the instance that is actually running — the tracker's
            # output depends on state it has built from the stream, and a fresh
            # instance sharing its cursor would emit something else entirely.
            consumer_run.register_running(consumer)
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
        consumer_run.clear_running()
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

# Order matters, and it is the reverse of how it reads: `add_middleware` puts
# each new layer *outside* the previous one, so the error catcher is registered
# first precisely so CORS ends up wrapping it.
#
# An unhandled exception otherwise reaches Starlette's ServerErrorMiddleware,
# which sits outside everything added here, and its 500 carries no
# `access-control-allow-origin`. A browser then blocks the response and the
# fetch rejects — from JavaScript that is indistinguishable from a server that
# is not running, which is how the session wizard came to report "the bus is
# unreachable" about a backend that was up and had already stored the session.
#
# An `@app.exception_handler(Exception)` does not fix it: Starlette routes the
# catch-all handler to ServerErrorMiddleware too, so it still answers from
# outside CORS. It has to be a middleware, and it has to be under CORS.
@app.middleware("http")
async def errors_the_browser_can_read(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception:  # noqa: BLE001 — the point is that nothing escapes
        # The traceback belongs in the log, not in a response to a caller.
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "internal error — see the backend log"},
        )


# The Next.js dashboard talks to this from the browser. Wide open on the edge
# box because it is on localhost behind no network; tighten before any deploy.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"] if settings.env == "local" else [],
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(auth.router)
app.include_router(ask.router)
app.include_router(events.router)
app.include_router(events.alias_router)
app.include_router(live.router)
app.include_router(sessions.router)
# The only routes in the application that accept a share token, and the only
# unauthenticated read path into tenant data. `routers/share.py` has what a
# leaked URL is worth.
app.include_router(share.router)
app.include_router(touch.router)
# After sessions: both mount /v1/sessions, and this one's paths are
# strictly longer, so no route here can shadow one there.
app.include_router(calibration.router)
app.include_router(consent.router)
app.include_router(kiosk.router)
app.include_router(outcomes.router)
app.include_router(erasure.router)
app.include_router(followups.router)
app.include_router(handoffs.router)
app.include_router(insights.router)
app.include_router(ledger.router)
app.include_router(dead_letters.router)
app.include_router(dispatches.router)
app.include_router(consumers.router)
app.include_router(plan.router)
app.include_router(rules.router)
app.include_router(retention_router.router)
app.include_router(integrations.router)
# Users are org administration, beside integrations: multi-tenant.md §3
# puts both with Admin, and both outlive the activation an operator runs.
app.include_router(users.router)


@app.get("/health", tags=["meta"])
async def health(response: Response) -> dict[str, object]:
    """Readiness for both stores and the consumers.

    Reports each separately so a graph outage is distinguishable from a log
    outage — they fail independently.

    **Degraded returns 503, not 200.** That is the difference between a health
    endpoint and a health-shaped endpoint. Docker's HEALTHCHECK and compose's
    `depends_on: service_healthy` both decide purely on the status code, so an
    always-200 response would report a container with a dead Neo4j or a crashed
    consumer as healthy — hiding precisely the silent failure this endpoint
    exists to surface. The body still describes what is wrong, for a human.
    """
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
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ok" if healthy else "degraded",
        "env": settings.env,
        "stores": stores,
        "consumers": consumers or "disabled",
    }
