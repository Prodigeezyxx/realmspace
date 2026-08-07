"""
A tiny migration runner for the graph — Alembic's idea, rebuilt in ~60 lines.

Neo4j has no Alembic. Without something like this, the schema would be a set of
Cypher statements somebody once pasted into a shell, with no record of what ran
where — exactly the problem Alembic exists to solve for Postgres.

So: numbered migrations applied in order, with applied versions recorded as
(:_SchemaVersion {version, applied_at}) nodes in the graph itself. Same contract
as the Postgres side — clone the repo, run one command, get an identical schema;
run it twice, nothing happens.

Usage:
    backend/.venv/bin/python -m app.graph.migrations upgrade
    backend/.venv/bin/python -m app.graph.migrations status
    backend/.venv/bin/python -m app.graph.migrations downgrade   # destructive
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone

from neo4j import AsyncSession

from app.config import get_settings
from app.graph import schema
from app.graph.driver import connect, disconnect

# version -> (description, statements, drop_statements)
MIGRATIONS: dict[str, tuple[str, list[str], list[str]]] = {
    "001": (
        "graph schema from data-model.md: tenant-scoped constraints + indexes",
        schema.statements(),
        schema.drop_statements(),
    ),
    "002": (
        "Zone and Surface are keyed per session, as Person already was",
        schema.REKEY_002,
        schema.REKEY_002_DOWN,
    ),
}


async def applied_versions(session: AsyncSession) -> set[str]:
    result = await session.run(
        "MATCH (v:_SchemaVersion) RETURN v.version AS version ORDER BY version"
    )
    return {record["version"] async for record in result}


async def upgrade(session: AsyncSession) -> list[str]:
    """Apply every migration not yet recorded. Returns what it ran."""
    done = await applied_versions(session)
    ran: list[str] = []

    for version in sorted(MIGRATIONS):
        if version in done:
            continue
        description, statements, _ = MIGRATIONS[version]

        # Each statement runs on its own: Neo4j will not accept schema changes
        # and data writes in one transaction, and constraint creation is
        # implicitly committed anyway.
        for stmt in statements:
            await session.run(stmt)

        await session.run(
            "MERGE (v:_SchemaVersion {version: $version}) "
            "ON CREATE SET v.applied_at = $now, v.description = $description",
            version=version,
            now=datetime.now(timezone.utc).isoformat(),
            description=description,
        )
        ran.append(version)

    return ran


async def downgrade(session: AsyncSession) -> list[str]:
    """Drop schema in reverse order. Does not touch data."""
    done = await applied_versions(session)
    reverted: list[str] = []

    for version in sorted(MIGRATIONS, reverse=True):
        if version not in done:
            continue
        _, _, drops = MIGRATIONS[version]
        for stmt in drops:
            await session.run(stmt)
        await session.run(
            "MATCH (v:_SchemaVersion {version: $version}) DELETE v", version=version
        )
        reverted.append(version)

    return reverted


async def status(session: AsyncSession) -> tuple[set[str], set[str]]:
    done = await applied_versions(session)
    return done, set(MIGRATIONS) - done


async def _main(command: str) -> int:
    settings = get_settings()
    driver = await connect()
    try:
        async with driver.session(database=settings.neo4j_database) as session:
            if command == "upgrade":
                ran = await upgrade(session)
                print(f"applied: {ran or 'nothing — already up to date'}")
            elif command == "downgrade":
                reverted = await downgrade(session)
                print(f"reverted: {reverted or 'nothing'}")
            elif command == "status":
                done, pending = await status(session)
                print(f"applied: {sorted(done) or '[]'}")
                print(f"pending: {sorted(pending) or '[]'}")
            else:
                print(f"unknown command: {command}", file=sys.stderr)
                return 2
    finally:
        await disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(sys.argv[1] if len(sys.argv) > 1 else "status")))
