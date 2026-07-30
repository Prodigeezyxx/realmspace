"""
Neo4j connection layer — the graph counterpart to app/db.py.

data-model.md: "Neo4j holds the graph." Postgres keeps the event log and the
timeseries. Two stores on purpose, which means a graph write and an event-log
write cannot share a transaction — consumers must be idempotent instead. That
is already required of them (event-bus-spec.md §4), and it is why every write
in graph/repository.py uses MERGE rather than CREATE.

One AsyncDriver for the whole process: the official driver docs are explicit
that it is created once for the application lifetime and is safe to share
across coroutines. Sessions are cheap and short-lived; the driver is not.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from neo4j import (
    AsyncDriver,
    AsyncGraphDatabase,
    AsyncSession,
    NotificationDisabledClassification,
)

from app.config import get_settings

_driver: AsyncDriver | None = None


def make_driver(uri: str, user: str, password: str) -> AsyncDriver:
    return AsyncGraphDatabase.driver(
        uri,
        auth=(user, password),
        # Suppress only UNRECOGNIZED — "the label `_SchemaVersion` does not
        # exist", which every query against an empty graph produces and which
        # is expected on a fresh install. Deprecation, performance and security
        # notifications still come through; those are worth hearing.
        notifications_disabled_classifications=[
            NotificationDisabledClassification.UNRECOGNIZED
        ],
    )


async def connect() -> AsyncDriver:
    """Open the shared driver. Called once from the FastAPI lifespan handler.

    verify_connectivity() fails fast at startup rather than on the first
    request, so a misconfigured password surfaces as a boot error instead of a
    confusing 500 later.
    """
    global _driver
    if _driver is None:
        s = get_settings()
        _driver = make_driver(s.neo4j_uri, s.neo4j_user, s.neo4j_password)
        await _driver.verify_connectivity()
    return _driver


async def disconnect() -> None:
    global _driver
    if _driver is not None:
        await _driver.close()
        _driver = None


def get_driver() -> AsyncDriver:
    if _driver is None:
        raise RuntimeError("graph driver not connected — call connect() first")
    return _driver


async def get_graph_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one Neo4j session per request.

    Unlike the Postgres dependency in app/db.py there is no commit here —
    session.run() inside an implicit transaction commits on success and rolls
    back on error by itself.
    """
    s = get_settings()
    async with get_driver().session(database=s.neo4j_database) as session:
        yield session
