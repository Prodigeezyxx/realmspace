# AGENTS.md — realmspace handoff context

> Last updated: 2026-07-27, ~21:45 +0100 · Phase 1 complete · branch `genspark_ai_developer`

## What this is

**realmspace** (always lowercase) — "Watch the room think." The measurement, replay,
and intelligence layer for physical brand experiences. One camera, one laptop → a
queryable graph of attention/dwell/behaviour + 3D digital twin. Sold to any booth
operator (multi-tenant); Floats XR is tenant #1 and the vertical wedge.

Pipeline: **Attract & Immerse → Measure → Attribute & Follow up.** Every feature
must move a client along that line (see `docs/VISION.md`).

## Repo layout

```
dashboard/    Next.js 16 + React 19 + TS. 6 surfaces: / /live /twin /ask /agents /report
backend/      FastAPI edge API: append-only event bus + relational graph projection + WS
perception/   Python YOLOv8 + ByteTrack stub → RealmEvents (stdout JSON or --bus-url)
docs/         15 docs. Start: VISION.md → roadmap.md → event-bus-spec.md → data-model.md
```

## Current state (verified 2026-07-27)

**Phase 1 ("The Spine") is DONE.** Verified live: camera → browser detector (COCO-SSD
+ centroid tracker) → `emit()` → FastAPI `/v1/events` → SQLite `event_log` →
graph_writer → Person nodes → `/v1/graph` → `/live` KPIs. A real camera session
(`ses_m6fl23rwh2a`) recorded 24 anonymous persons / 799 events; longest dwell 284.9s.

| Piece | State |
|---|---|
| Live on-device tracking (browser) | ✅ real |
| Event bus (SQLite default / Postgres ready) | ✅ real, durable, replayable |
| Graph projection + snapshot API | ✅ real (`backend/app/graph_writer.py`) |
| WS bridge bus → dashboard | ✅ (`NEXT_PUBLIC_BUS_URL`, StatusBar pill) |
| `/live` KPIs | ✅ bus-derived via `useSessionBusStats` (survive reload) |
| Perception offline buffer + ordered replay | ✅ tested (`--buffer-file`) |
| RBAC skeleton | ✅ `GET /v1/auth/resolve` |
| Heatmap / twin paths / Ask / Agents / Report | 🟡 still mock-driven (Phase 2+) |

## How to run

```powershell
# Backend (from repo root; venv already exists with deps installed)
backend\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000 --app-dir backend --reload
# → http://localhost:8000/docs  · DB: backend/data/realmspace.db (SQLite, zero-ops)

# Dashboard
cd dashboard; npm run dev   # → http://localhost:3000
# dashboard/.env.local already contains NEXT_PUBLIC_BUS_URL=http://localhost:8000
#   (gitignored — do not commit; Firebase keys are blank = auth disabled locally)

# Perception stub (optional; browser detector is the usual source)
cd perception; python realmspace.py --bus-url http://127.0.0.1:8000 --tenant-id t_floats
```

## Architecture in one breath

Producers (`emit()` in dashboard, `--bus-url` in perception) → append-only,
idempotent `event_log` (tenant+session scoped) → consumers (graph_writer) project
`graph_nodes`/`graph_edges` → REST snapshot + WebSocket fan-out. Dashboard dual-writes:
local durable log (localStorage, offline source of truth) + remote API; WS bridge
mirrors remote events back into the local log. Tenant default `t_floats`
(`lib/tenant/context.ts`); session from `lib/event-context.ts` (default `"default"`,
real sessions look like `ses_...`). Graph is **rebuildable**: wipe `graph_*` rows for
a session and replay its events through `apply_event()`.

## Conventions & user preferences

- **Brand name always lowercase:** realmspace.
- **CHANGELOG style (user requirement):** every entry = **plain-words explanation
  first**, then technical detail in *italics*; sections dated with time (+0100).
- **Consent redline:** `emit()` refuses PII event types without `consentProof`
  (except `consent.captured`). Never bypass — see `docs/consent-and-identity.md`.
- **Bus, not mocks:** new dashboard data should derive from the durable bus/graph,
  not `lib/mock/`. Mocks only remain where no producer exists yet.
- **Minimal changes**, match existing code style; Next.js App Router, Tailwind v4
  (CSS-first in `globals.css`).
- Commit style so far: `feat(p1): ...`, `chore: ...` — concise, scoped.

## Gotchas (learned the hard way)

- **Env vars need a dev-server restart** — Next.js only reads `.env.local` at boot.
- **Backend started without `--reload`** must be restarted to pick up code changes.
- **graph_writer firstSeen bug (fixed 2026-07-27):** never put `firstSeen` in an
  upsert merge — it clobbers the earliest timestamp. Min/max must be handled in the
  follow-up UPDATE.
- **Graph endpoint scope:** check `/v1/graph/{tenantId}/{sessionId}` with the *real*
  session id (`ses_...`), not `default` — live sessions get their own ids.
- **WS hello replay is capped at 200 events** — the KPI hook also polls the graph
  snapshot so counts stay authoritative on long sessions.
- PowerShell: no heredocs; write Python test scripts to a temp file and run them.
- Remote URL is lowercase: `https://github.com/Prodigeezyxx/realmspace.git`.

## What's next — Phase 2: Prove ROI (~2 weeks)

Roadmap absorbs the external 6-week pod doc (2026-07-23, unchanged); pod-week
refs (`W1`–`W6`) annotated inline in `docs/roadmap.md`. Our sequence keeps
**Prove ROI second** (the wedge) — pod W5's measurement items live in P2.

1. **Spatial-event deriver (linchpin)** — zone polygons + `perception.detection`
   → `spatial.zone_enter/zone_exit/dwell/passby` onto the bus. Scorecard, rules
   engine, and graph zone edges all depend on it. Nothing emits spatial.* today.
2. **Report from real session data** — 4-layer scorecard consumer + session
   outcome API + templated report with figures traceable to event seqs.
3. **Twin replays recorded sessions** — data swap from bus, not a rebuild.
4. **Ask the Room for real** — LLM → constrained, validated SQL templates over
   the relational projection (NOT Cypher — ADR-001 decided relational; NLQ
   approach needs its own ADR first). **OPEN DECISION: AI provider key.**
5. Then P3: rules engine (edge, <3s), dispatchers with per-dispatch idempotency
   (keyed on `rule.fired` eventId), DLQ+HITL, cost telemetry, contract additions
   (`rfid.read`, `spatial.tagged`, `intent.scored`, `drift.detected`,
   `calibration.updated`, `crm.retract`), ADR-002 (rule spec as JSON; edge
   fires, browser previews), RFID producer (hardware-gated).

Parallel (founder-led, no code): GTM validation sprint in `docs/gtm.md`
(90s Loom + landing + 50 cold emails; pass = 5 booked demos in 7 days).

## Git state

- Branch of record: `genspark_ai_developer` (also exists on origin; `main` lags).
- Latest: `0b36740` "feat(p1): close out Phase 1 …" (2026-07-27).
- Working tree clean at handoff. Push to `origin genspark_ai_developer`.
