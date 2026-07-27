# realmspace edge API

FastAPI service that implements the Phase 1 spine:

- Append-only **event bus** (`docs/event-bus-spec.md`)
- **Relational graph projection** (`docs/adr/001-graph-store.md`)
- **Graph writer** consumer (bus → nodes/edges)
- **WebSocket** fan-out for the dashboard `/live` bridge

## Quick start (SQLite — zero deps beyond pip)

```bash
cd backend
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
# source .venv/bin/activate

pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000/docs

Default DB file: `backend/data/realmspace.db`

## Postgres (optional)

```bash
# set DATABASE_URL then start uvicorn
set DATABASE_URL=postgresql://realmspace:realmspace@localhost:5432/realmspace
```

Schema is identical; `docker-compose.yml` at the repo root starts Postgres when Docker is available.

## Key endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | liveness + db backend |
| `POST` | `/v1/events` | append one event (idempotent on `eventId`) |
| `POST` | `/v1/events/batch` | append many |
| `GET` | `/v1/events` | read / replay from `after_seq` |
| `GET` | `/v1/graph/{tenant}/{session}` | graph snapshot |
| `WS` | `/v1/ws/{tenant}/{session}` | live event stream |

## Env

See `.env.example`.
