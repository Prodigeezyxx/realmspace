# realmspace — backend

Two stores, one service:

- **Postgres** — the durable, append-only event bus
  ([`../docs/event-bus-spec.md`](../docs/event-bus-spec.md))
- **Neo4j** — the spatial graph
  ([`../docs/data-model.md`](../docs/data-model.md))

Roadmap Phase 1, items 1–4 and 6. The tracker and graph-writer consumers
(`app/consumers/`) bridge the two stores in-process, without going over HTTP.

> **This is the `neo4j-track`.** Phase 1's backend is being built twice on
> purpose so the approaches can be compared — the other lives on `floats-agent`
> and puts the graph in relational tables alongside the bus, per
> `docs/adr/001-graph-store.md`. Neither is merged; they share
> [`../CHANGELOG.md`](../CHANGELOG.md), which is updated every session and where
> each entry says which track it belongs to.

> **Authenticated.** Every endpoint except `/health` requires a credential, and
> `tenant_id` is derived from it rather than supplied by the caller. See
> "Credentials" below.

## Setup

### Fastest: compose (from the repo root)

```bash
cp .env.example .env       # set JWT_SECRET and NEO4J_PASSWORD
docker compose up --build
docker compose exec app python -m app.auth.seed
```

Postgres, Neo4j and the backend, both migration systems applied, ~13s cold.
Host ports are remapped (`55432`, `7475`/`7688`) so it coexists with the brew
services below rather than fighting them for a port.

### Or directly, which is better for working on the backend

No image rebuild between edits:

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
cp .env.example .env      # set NEO4J_PASSWORD, the Postgres role, and JWT_SECRET
.venv/bin/alembic upgrade head
.venv/bin/python -m app.auth.seed          # prints a dev key and token
```

`JWT_SECRET` has no default and the app will not start without one. A shipped
default would be a token anyone could forge; generate your own:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
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

## Credentials

Two kinds of caller, because they authenticate differently and want different
things — a camera cannot do an interactive login.

| | Humans | Devices (perception, RFID, kiosk) |
|---|---|---|
| Header | `Authorization: Bearer <jwt>` | `X-API-Key: <key>` |
| Get one | `POST /v1/auth/token` with `{"email": …}` | `python -m app.auth.seed` |
| Can | read and write | **write only** |

```bash
curl -H "X-API-Key: $KEY" -X POST localhost:8000/events -d '…'
curl -H "Authorization: Bearer $JWT" localhost:8000/events
```

The WebSocket takes `?token=<jwt>` instead of a header, because browsers cannot
set headers on a WebSocket handshake. That means the token appears in URLs and
therefore in logs, which is why the TTL is short.

**`tenant_id` is never an input.** `GET /events` has no tenant parameter at all —
it comes from the credential. `POST /events` rejects a body whose tenant
disagrees with the credential rather than silently re-homing the event. This is
the whole point of the auth item, and deleting the parameter is a stronger
guarantee than validating it.

Tokens are signed and verified **locally**, with no network call. `event-bus-spec.md`
§1 requires the edge box to keep working when the conference wifi does not, and
verifying a Firebase ID token needs Google's keys. Firebase is still how the
dashboard establishes identity; the intended flow is to exchange a Firebase
login once at `POST /v1/auth/token`. **That exchange is not built yet** — the
endpoint currently trusts the email it is given, which is fine for local
development and is not authentication. It is the next thing to close.

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

| Endpoint | Auth | Does |
|---|---|---|
| `POST /v1/auth/token` | none | `{"email": …}` → a signed token |
| `GET /v1/auth/resolve` | none | email → org + role (lookup only; grants nothing) |
| `POST /events` | key or token | Append one event. 201 if created, 200 if the `event_id` was already in the log. |
| `GET /events?since_seq=…` | token | Read your tenant's log forward from a cursor. Also accepts `session_id`, `type`, `limit`. **No tenant parameter.** |
| `WS /v1/ws/{tenant}/{session}?token=…` | token | Live feed. `since_seq` for gapless reconnect. |
| `POST /v1/sessions` | token, **admin/operator** | Create or update a session's zones and measurement parameters. Omitting `zones` leaves them alone; sending a list replaces the set. |
| `GET /v1/sessions/{session_id}` | token | The configuration back. 404 if the session was never configured. |
| `GET /v1/sessions/{session_id}/graph` | token | Unique people + dwell per zone. No 404 — an unconfigured session is a legitimate all-zeroes answer. |
| `GET /health` | none | Liveness for **both** stores plus the consumers. |

**Configure a session before running perception against it.** The tracker reads
its zone polygons from the graph, so with no zones it emits nothing at all — no
`spatial.*` events, no graph edges, no metrics — and does so quietly. `POST
/v1/sessions` is the only thing that writes them.

The rest of the graph has no HTTP surface: it is written by in-process
consumers, not by clients. `app/graph/repository.py` is the API.

`event_id` is assigned by the **producer**, not the server. That is what makes
retries safe: a producer whose POST times out resends the same `event_id` and
gets the same `seq` back, with no second row. Valid `type` values are the
taxonomy in `../docs/event-bus-spec.md` §3.

There is deliberately no way to query the log unscoped, or scoped to a tenant
you do not hold a credential for (`../docs/multi-tenant.md` §2).

**Postgres enforces this itself, not just the application.** Migration 0003 puts
row-level security on `event_log`, `consumer_cursor` and `dead_letter`, and the
app connects as `realmspace_app` — neither a superuser nor an owner, because
both bypass policies. Raw SQL naming another tenant returns nothing.

Two things follow that are easy to trip over:

- **A session must declare its tenant** via `db.scope_to_tenant(session, …)`, or
  it sees zero rows. It fails closed. The HTTP path does this automatically once
  a credential is verified; consumers do it per batch. Anything opening its own
  session must do it itself.
- **The scope is transaction-local**, deliberately. `set_config(…, is_local =>
  true)` means a pooled connection cannot carry one request's tenant into the
  next — which would be a cross-tenant leak caused by the fix for cross-tenant
  leaks. A commit therefore clears it.

`auth_user` and `api_key` are deliberately **not** under RLS: authenticating
means reading them to discover which tenant the caller is, so a policy keyed on
that tenant would need the answer before the lookup that produces it.

**This covers the event log, not the graph.** Neo4j Community has no row-level
security, so `app/graph/repository.py` remains the only thing isolating tenants
there. See `../docs/data-model.md` → "Store decision".

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
app/config.py            settings from .env (both stores, auth)
app/main.py              app + lifespan: graph driver and consumer tasks

  auth
app/auth/models.py       auth_user / api_key
app/auth/tokens.py       sign + verify; key hashing
app/auth/principal.py    Principal and the dependencies that derive tenant_id
app/auth/seed.py         `python -m app.auth.seed` — local dev credentials

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
