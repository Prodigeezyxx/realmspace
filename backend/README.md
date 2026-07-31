# realmspace — backend

Two stores, one service:

- **Postgres** — the durable, append-only event bus
  ([`../docs/event-bus-spec.md`](../docs/event-bus-spec.md))
- **Neo4j** — the spatial graph
  ([`../docs/data-model.md`](../docs/data-model.md))

Roadmap Phase 1, items 1–3. The tracker and graph-writer consumers
(`app/consumers/`) bridge the two stores in-process, without going over HTTP.

> **This is the `neo4j-track`.** Phase 1's backend is being built twice on
> purpose so the approaches can be compared — the other lives on `floats-agent`
> and puts the graph in relational tables alongside the bus, per
> `docs/adr/001-graph-store.md`. Neither is merged; they share
> [`../CHANGELOG.md`](../CHANGELOG.md), which is updated every session and where
> each entry says which track it belongs to.

> **Not authenticated yet.** `tenant_id` is caller-supplied and unverified, so
> anything that can reach this process can read any tenant's log and write forged
> events. Localhost only until the RBAC item in `../docs/roadmap.md` lands.

## Setup

```bash
# Postgres — the event log
brew install postgresql@17
brew services start postgresql@17
export PATH="/opt/homebrew/opt/postgresql@17/bin:$PATH"   # already in ~/.zshrc
createdb realmspace
createdb realmspace_test

# Neo4j — the graph (pulls openjdk@21 + cypher-shell)
brew install neo4j
brew services start neo4j
cypher-shell -u neo4j -p neo4j -d system \
  "ALTER CURRENT USER SET PASSWORD FROM 'neo4j' TO '<your-password>';"

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env      # set NEO4J_PASSWORD and the Postgres role
```

Neo4j browser: `http://localhost:7474` · Bolt: `bolt://localhost:7687`

## Schema

**Postgres** — the SQL from spec §2, applied by a hand-written Alembic migration:

```bash
.venv/bin/alembic upgrade head
psql realmspace -c '\d event_log'
```

`app/models.py` mirrors that migration. To prove they haven't drifted:

```bash
.venv/bin/alembic revision --autogenerate -m "drift check"
# the generated upgrade() must be `pass` — then delete the file
```

**Neo4j** — constraints and indexes from `data-model.md`. Neo4j has no Alembic,
so `app/graph/migrations.py` rebuilds the idea: numbered migrations recorded as
`(:_SchemaVersion)` nodes in the graph itself.

```bash
.venv/bin/python -m app.graph.migrations upgrade
.venv/bin/python -m app.graph.migrations status
cypher-shell -u neo4j -p <pw> "SHOW CONSTRAINTS;"
```

A graph database has no CREATE TABLE — it will store any property on any node.
The schema *is* the constraints and indexes; the node shapes in `data-model.md`
are a contract the application keeps, not something the database checks.

## Tenant isolation — read this before writing graph code

Every uniqueness key starts with `tenant_id`, but Neo4j Community **cannot
enforce tenant isolation at the database layer**, which is what
`multi-tenant.md` §2 asks for. Verified on this instance:

```
CREATE CONSTRAINT … REQUIRE n.tenant_id IS NOT NULL
→ 51N27: Property existence constraint is not supported in community edition
```

There is also no row-level security and no second database. So the only thing
enforcing isolation is `app/graph/repository.py`, which holds **every** Cypher
statement in the system and takes `tenant_id` as a required argument on every
function.

**Do not write Cypher anywhere else.** If it leaks into a router or a consumer,
the guarantee disappears and nothing will fail to tell you. Recorded as an open
item in `data-model.md` → "Store decision".

## Run

```bash
.venv/bin/uvicorn app.main:app --reload --port 8000
# http://localhost:8000/docs
```

| Endpoint | Does |
|---|---|
| `POST /events` | Append one event. 201 if created, 200 if the `event_id` was already in the log. |
| `GET /events?tenant_id=…&since_seq=…` | Read forward from a cursor, ordered by `seq`. Also accepts `session_id`, `type`, `limit`. |
| `GET /health` | Liveness for **both** stores, reported separately. |

The graph has no HTTP surface yet — it is written by in-process consumers, not
by clients. `app/graph/repository.py` is the API.

`event_id` is assigned by the **producer**, not the server. That is what makes
retries safe: a producer whose POST times out resends the same `event_id` and
gets the same `seq` back, with no second row. Valid `type` values are the
taxonomy in `../docs/event-bus-spec.md` §3.

`tenant_id` is required on reads — there is deliberately no way to query the log
unscoped (`../docs/multi-tenant.md` §2). Isolation is enforced at the
application layer today; database-level row security is Phase 6 hardening.

## Test

```bash
.venv/bin/pytest -v
```

Runs against real databases, not mocks — the point is to prove the stores
behave the way the specs claim.

The two halves isolate differently, and it's worth knowing why. Postgres tests
get their own `realmspace_test` database. Neo4j tests can't: Community edition
is single-database (`SHOW DATABASES` returns only `neo4j` and `system`), so they
share the dev instance and isolate by `tenant_id`, wiping tenants prefixed
`t_test` before each test. That turns the limitation into a check — if tenant
scoping breaks anywhere, the graph tests bleed into each other and fail.

## Before writing a consumer

Read the ordering guarantees in
[`../docs/event-bus-spec.md`](../docs/event-bus-spec.md) §2 first — in
particular that **`seq` is ordered but not contiguous**, and that commit order
is not `seq` order under concurrent producers. Both have non-obvious
consequences for cursor handling.

Use `repository.read_events()` rather than hand-rolling the query; it already
polls `seq > last_seq` and scopes by tenant.

## Layout

```
app/config.py            settings from .env (both stores)
app/main.py              app + lifespan that opens/closes the graph driver

  the event bus — Postgres
app/db.py                async engine, session factory, request dependency
app/models.py            event_log / consumer_cursor / dead_letter
app/schemas.py           request + response shapes
app/repository.py        all SQL against the log
app/routers/             HTTP surface
alembic/versions/        0001 = the spec's SQL, written by hand

  the graph — Neo4j
app/graph/driver.py      one AsyncDriver for the process
app/graph/schema.py      migration 001: constraints + indexes
app/graph/migrations.py  versioned runner, tracked as (:_SchemaVersion) nodes
app/graph/repository.py  ALL Cypher lives here — see "Tenant isolation" above
```
