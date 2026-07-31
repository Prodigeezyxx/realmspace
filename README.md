# realmspace

> **Watch the room think.**
>
> realmspace is the measurement, replay, and intelligence layer for physical
> brand experiences. One camera. One laptop. A queryable graph of attention,
> dwell, and behaviour — plus a 3D digital twin you can scrub through and ask
> questions of.

This repository ships:

- A polished **Next.js dashboard** with a Tesla-inspired in-car UI aesthetic,
  fully functional with mocked data (`dashboard/`)
- A **Phase-0 Python perception stub** that opens any webcam, runs YOLO
  person detection, and emits structured JSON events (`perception/`)
- A **FastAPI backend** — the durable append-only event bus, the Neo4j spatial
  graph, the consumers that connect them, and a live WebSocket feed (`backend/`)
- The **product spec and supporting documents** (`docs/`)

The live `/live` view performs **real, on-device
person detection** from your webcam using TensorFlow.js + COCO-SSD with a
custom centroid tracker — no backend, no cloud, no frames stored. Walk in
front of your laptop and watch yourself get tracked with a persistent
anonymous ID, live. Everything else (digital twin, reports, agents) is wired
against mocked data that maps 1:1 to the contracts the real backend will
produce. The boundary between "real" and "mocked" is documented explicitly in
`docs/PRD.md` so you can demo honestly.

---

## What's in the dashboard

| Surface | Path | What it shows |
|---|---|---|
| **Landing** | `/` | Agency-targeted product page — hero, the 4-step loop, pricing, privacy posture |
| **Live** | `/live` | **Real webcam + on-device object tracking.** Click "Start live session" — your browser asks for camera permission, loads COCO-SSD (~28 MB, once), then detects people in your frame at 10–22 fps with persistent anonymous IDs. KPIs and the event log update in real time from the detector. Heatmap, AI insights, and zone counters remain mocked. |
| **Twin** | `/twin` | 3D digital twin of the activation — anonymous avatars walking recorded paths, scrubbable timeline, playback speed |
| **Ask** | `/ask` | Plain-English query interface — natural language → Cypher → answer with chart |
| **Agents** | `/agents` | Rule builder — "when X, do Y" with Slack / webhook / screen actions |
| **Report** | `/report` | Client-deliverable post-activation report — funnel, top moments, sponsor exposure, recommendations |

---

## Running the dashboard

```bash
cd dashboard
npm install --legacy-peer-deps     # only the first time
npm run dev
```

Open `http://localhost:3000`.

Stack:

- Next.js 16 (App Router) · React 19 · TypeScript
- Tailwind CSS v4 (CSS-first config in `globals.css`)
- React Three Fiber + drei + three.js (the digital twin)
- Recharts (charts) · motion (animations) · lucide-react (icons)
- Plus Jakarta Sans + JetBrains Mono via `next/font/google`

---

## Running the perception stub

```bash
cd perception
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python realmspace.py               # opens your default webcam
python realmspace.py --headless    # JSON events only, no preview window
```

Output is one JSON event per detection per frame on stdout. See
`perception/README.md` for the full schema.

To feed the backend instead, add `--bus-url` and a device key:

```bash
python realmspace.py --headless --bus-url http://127.0.0.1:8000 \
  --session-id s_demo --api-key "$REALMSPACE_API_KEY"
```

Events buffer to a local file when the bus is unreachable and replay in order
when it returns — each one keeps the id it was born with, so a reconnect stores
them once rather than twice.

---

## Running the backend (event bus)

The durable spine — an append-only Postgres log behind a FastAPI service. See
[`docs/event-bus-spec.md`](docs/event-bus-spec.md) for the design and
[`backend/README.md`](backend/README.md) for full setup.

```bash
brew install postgresql@17 && brew services start postgresql@17
export PATH="/opt/homebrew/opt/postgresql@17/bin:$PATH"
createdb realmspace && createdb realmspace_test

cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env               # Postgres role, NEO4J_PASSWORD, JWT_SECRET
.venv/bin/alembic upgrade head     # create the tables
.venv/bin/python -m app.auth.seed  # prints a dev key and token
.venv/bin/uvicorn app.main:app --reload
```

Open `http://localhost:8000/docs` for a clickable API console generated from
the code.

| Endpoint | Auth | Does |
|---|---|---|
| `POST /v1/auth/token` | none | `{"email": …}` → a signed token |
| `POST /events` | key or token | Append one event. `201` if created, `200` if that `event_id` was already in the log. |
| `GET /events?since_seq=…` | token | Read your tenant's log forward from a cursor. |
| `WS /v1/ws/{tenant}/{session}?token=…` | token | Live feed, `<60ms` typical. |
| `GET /health` | none | Liveness for both stores and the consumers. |

Writes are **idempotent on a producer-assigned `event_id`**, so a producer that
retries after a timeout cannot create a duplicate.

**`tenant_id` is never something the caller supplies** — it comes from the
credential. People use `Authorization: Bearer`, devices (cameras, RFID readers,
kiosks) use `X-API-Key` and can only write. Both are verified locally with no
network call, so the edge box still authenticates when the venue wifi drops.

Run `.venv/bin/pytest` — 57 tests against a real Postgres and a real Neo4j.

Stack: FastAPI · SQLAlchemy 2.0 (async) · asyncpg · Alembic · Neo4j · PyJWT · pytest

---

## Documentation

Full index: [`docs/README.md`](docs/README.md). The ones you'll reach for first:

| Doc | What it covers |
|---|---|
| [`docs/VISION.md`](docs/VISION.md) | The single-pipeline thesis — start here |
| [`docs/roadmap.md`](docs/roadmap.md) | Phased build order + acceptance criteria |
| [`docs/PRD.md`](docs/PRD.md) | Product spec v2 — positioning, ICP, MVP scope, real vs. mocked status |
| [`docs/architecture.md`](docs/architecture.md) | End-to-end system architecture |
| [`docs/event-bus-spec.md`](docs/event-bus-spec.md) | The append-only bus — schema, event taxonomy, replay semantics |
| [`docs/data-model.md`](docs/data-model.md) | Graph schema + example Cypher queries |
| [`docs/multi-tenant.md`](docs/multi-tenant.md) | Tenancy, isolation, RBAC, billing hooks |
| [`docs/gtm.md`](docs/gtm.md) | Go-to-market plan, pricing, cold email template, revenue math |
| [`docs/privacy.md`](docs/privacy.md) | Privacy posture sent to client compliance teams |

---

## Project layout

```
realmspace/
├── README.md                 ← you are here
├── docs/                     ← vision, roadmap, PRD, architecture, specs
├── perception/               ← Python · YOLO + ByteTrack + OpenCV
│   ├── realmspace.py
│   └── requirements.txt
├── backend/                  ← FastAPI · event bus + graph + consumers
│   ├── app/
│   │   ├── models.py         ← event_log / consumer_cursor / dead_letter
│   │   ├── repository.py     ← all SQL against the log
│   │   ├── auth/             ← JWT + device keys; tenant derived, never supplied
│   │   ├── graph/            ← Neo4j schema, migrations, all Cypher
│   │   ├── consumers/        ← tracker, graph writer, broadcast
│   │   └── routers/          ← events, live WebSocket, auth
│   ├── alembic/versions/     ← schema migrations
│   └── tests/
└── dashboard/                ← Next.js · the demo artifact
    ├── src/
    │   ├── app/              ← routes (landing + the 5 app surfaces)
    │   ├── components/       ← UI, chrome, viz, twin, ask, report
    │   └── lib/
    │       ├── mock/         ← fabricated session data
    │       └── utils.ts
    └── package.json
```

---

## What's real vs. mocked (today)

| | Real | Mocked |
|---|---|---|
| Dashboard UI / interactions | ✅ | |
| **Live webcam capture** | ✅ on-device via `getUserMedia` | |
| **Real-time person detection** | ✅ COCO-SSD via TensorFlow.js (WebGL backend) | |
| **Persistent anonymous IDs** | ✅ in-browser centroid tracker | |
| **Live KPIs + event log** | ✅ driven by real detections | |
| 3D digital twin scene | ✅ | data only |
| Heatmap rendering | ✅ | aggregated from mock waypoints |
| Twin avatar paths | | 🟡 hand-authored seed paths |
| Ask the Room — answers | | 🟡 pre-canned, regex-matched |
| Agent rule engine | ✅ UI | 🟡 in-memory state |
| Report numbers | | 🟡 static for the demo |
| Server-side perception (Phase 0) | 🟡 Python stub in `perception/` | |
| **Durable event bus (Postgres)** | ✅ append-only log, idempotent writes, cursor reads | |
| **Graph store (Neo4j)** | ✅ schema from `data-model.md`, tenant-scoped | |
| **Tracker consumer** | ✅ detections → `spatial.zone_enter` / `zone_exit` / `dwell` | |
| **Graph writer consumer** | ✅ events → `Person` / `Zone` nodes and edges | |
| **Replay safety** | ✅ rewind a cursor and nothing duplicates | |
| **Perception → bus** | ✅ `--bus-url`, with offline buffer and idempotent replay | |
| Gaze / group / pass-by events | | 🔲 gaze needs pose data; pass-by is Phase 2 |
| **Auth on the backend API** | ✅ JWT for people, API keys for devices; tenant derived from the credential | 🔲 DB-level row security still to come |

### Live tab — how the camera actually works

1. **Click "Start live session"** in the camera viewport
2. **Browser prompts for camera permission** — must be granted
3. **COCO-SSD downloads on first run** (~28 MB, cached forever after)
4. **Detection loop runs in `requestAnimationFrame`**: each frame goes to TF.js with WebGL backend, COCO-SSD returns bounding boxes + class labels + confidence
5. **Centroid tracker** (`src/lib/tracker.ts`) maintains persistent IDs across frames using greedy nearest-centroid matching with a configurable distance threshold and miss tolerance
6. **Bounding boxes + corner brackets + ID tags** are drawn on a canvas overlay that's mirrored to match the user's view
7. **Live KPIs** ("People now", session length, fps) and the **event log** ("P-003 entered the frame", "P-005 left the frame") are derived directly from detector state

No data leaves the tab. The video element, the model, the canvas, the tracker — all live in your browser. Close the tab and everything is gone.

The mocked pieces are wired against the exact contract the real backend will
produce. When the Phase-1 perception engine lands, swapping is a wiring change,
not a redesign.

The browser's in-memory bus (`dashboard/src/lib/event-bus.ts`) is **not** being
replaced by the Postgres log — it stays as the client-side fan-out for live UI.
The durable log sits behind it, and the dashboard will subscribe over WebSocket.
Same event shapes either way (`docs/event-bus-spec.md` §7).

---

## License

Internal · Floats XR · 2026.
