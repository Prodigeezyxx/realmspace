"""
realmspace backend — the durable event bus (docs/event-bus-spec.md).

Run it:
    backend/.venv/bin/uvicorn app.main:app --reload --port 8000
    → http://localhost:8000/docs
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers import events

settings = get_settings()

app = FastAPI(
    title="realmspace event bus",
    description="Append-only, idempotent, replayable. See docs/event-bus-spec.md.",
    version="0.1.0",
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
async def health() -> dict[str, str]:
    return {"status": "ok", "env": settings.env}
