# realmspace — backend

The durable event bus. Append-only, idempotent, replayable —
see [`../docs/event-bus-spec.md`](../docs/event-bus-spec.md).

This is roadmap Phase 1, item 1. Nothing consumes the log yet; the tracker and
graph-writer consumers are the next items and will call `app/repository.py`
directly rather than going over HTTP.

## Setup

Postgres 17 via Homebrew, plus a venv:

```bash
brew install postgresql@17
brew services start postgresql@17
export PATH="/opt/homebrew/opt/postgresql@17/bin:$PATH"   # already in ~/.zshrc

createdb realmspace
createdb realmspace_test

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env      # then edit the role name if yours differs
```

## Schema

The schema is the SQL from spec §2, applied by a hand-written Alembic migration:

```bash
.venv/bin/alembic upgrade head
psql realmspace -c '\d event_log'
```

`app/models.py` mirrors that migration. To prove they haven't drifted:

```bash
.venv/bin/alembic revision --autogenerate -m "drift check"
# the generated upgrade() must be `pass` — then delete the file
```

## Run

```bash
.venv/bin/uvicorn app.main:app --reload --port 8000
# http://localhost:8000/docs
```

| Endpoint | Does |
|---|---|
| `POST /events` | Append one event. 201 if created, 200 if the `event_id` was already in the log. |
| `GET /events?tenant_id=…&since_seq=…` | Read forward from a cursor, ordered by `seq`. Also accepts `session_id`, `type`, `limit`. |
| `GET /health` | Liveness. |

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

Runs against the real `realmspace_test` database, not a mock — the point is to
prove Postgres constraints behave the way the spec claims.

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
app/config.py       settings from .env
app/db.py           async engine, session factory, request dependency
app/models.py       event_log / consumer_cursor / dead_letter
app/schemas.py      request + response shapes
app/repository.py   all SQL against the log
app/routers/        HTTP surface
alembic/versions/   0001 = the spec's SQL, written by hand
```
