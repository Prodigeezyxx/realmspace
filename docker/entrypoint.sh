#!/usr/bin/env bash
#
# Bring the schema up to date, then serve.
#
# There are two migration systems because there are two stores: Alembic for
# Postgres, and a small hand-rolled runner for Neo4j (which has no Alembic).
# Both have to run before the app serves a request, or the first one fails on a
# missing table or a missing constraint.
#
# Both are idempotent — that is what migration runners are for — so this is safe
# on every restart, not just the first.

set -euo pipefail

echo "==> waiting for postgres"
# compose's `depends_on: service_healthy` already gates on this, but a container
# can also be restarted on its own, and a five-second wait beats a crash loop.
until python -c "
import asyncio, sys
from sqlalchemy import text
from app.db import engine
async def ping():
    async with engine.connect() as c:
        await c.execute(text('SELECT 1'))
asyncio.run(ping())
" 2>/dev/null; do
  sleep 1
done

echo "==> waiting for neo4j"
until python -c "
import asyncio
from app.graph.driver import connect, disconnect
async def ping():
    await connect()
    await disconnect()
asyncio.run(ping())
" 2>/dev/null; do
  sleep 1
done

echo "==> applying postgres migrations"
alembic upgrade head

echo "==> applying graph schema"
python -m app.graph.migrations upgrade

echo "==> starting api"
# --host 0.0.0.0, not the default. uvicorn binds loopback otherwise, which
# inside a container means "reachable only from inside this container" — the
# single most common reason a containerised service starts cleanly and then
# refuses every connection from the host.
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
