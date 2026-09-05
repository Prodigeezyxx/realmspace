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
#
# This waits as the *owner*, not as the app role, and the distinction is the
# whole boot order. Migration 0003 is what creates `realmspace_app`, so on a
# fresh volume the app role does not exist until the migration below has run.
# Waiting as the app role therefore waits for something only the next step can
# produce, and the container sits at "waiting for postgres" forever with the
# error swallowed by the 2>/dev/null this loop needs to stay quiet.
until python -c "
import asyncio
from sqlalchemy import text
from app.config import get_settings
from app.db import make_engine
async def ping():
    engine = make_engine(get_settings().migration_url)
    async with engine.connect() as c:
        await c.execute(text('SELECT 1'))
    await engine.dispose()
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

# Migration 0003 creates the app role deliberately without a password: on a
# developer's machine the app reaches Postgres over the unix socket under trust
# auth, and a password committed to git is not a password. In compose there is
# no socket — the app crosses the container network to `postgres:5432`, where
# the image's default scram-sha-256 rejects a passwordless role. So the role is
# given one here, from the environment, after the migration that creates it.
#
# Set through set_config rather than interpolated into the statement: ALTER ROLE
# is a utility statement and takes no bind parameters, and format(%L) quotes
# whatever the password happens to contain.
if [ -n "${APP_DB_PASSWORD:-}" ]; then
  echo "==> setting the app role password"
  python -c "
import asyncio, os
from sqlalchemy import text
from app.config import get_settings
from app.db import make_engine
async def setpw():
    engine = make_engine(get_settings().migration_url)
    async with engine.begin() as c:
        await c.execute(
            text(\"SELECT set_config('realmspace.app_pw', :pw, true)\"),
            {'pw': os.environ['APP_DB_PASSWORD']},
        )
        await c.execute(text('''
            DO \$\$ BEGIN
              EXECUTE format('ALTER ROLE realmspace_app PASSWORD %L',
                             current_setting('realmspace.app_pw'));
            END \$\$;
        '''))
    await engine.dispose()
asyncio.run(setpw())
"
fi

# Prove the app can actually connect as itself before serving. Without this the
# first request is where a broken app credential surfaces, as a 500 from a
# container that reported a clean start.
echo "==> checking the app connection"
python -c "
import asyncio
from sqlalchemy import text
from app.db import engine
async def ping():
    async with engine.connect() as c:
        await c.execute(text('SELECT 1'))
asyncio.run(ping())
"

echo "==> applying graph schema"
python -m app.graph.migrations upgrade

echo "==> starting api"
# --host 0.0.0.0, not the default. uvicorn binds loopback otherwise, which
# inside a container means "reachable only from inside this container" — the
# single most common reason a containerised service starts cleanly and then
# refuses every connection from the host.
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
