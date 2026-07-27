# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Each entry explains what changed **in plain words** first, then the technical detail.
Sections are dated (with time, local timezone +0100) so you can see when things landed.

## [Unreleased] — last updated 2026-07-27, 22:15 +0100

### Changed — 2026-07-27 (late evening)

- **The plan grew teeth.** We merged a detailed 6-week team plan (the "pod
  roadmap") into our own roadmap without changing ours at its core: proving ROI
  to clients still comes second, right after the spine. Everything the pod plan
  added — RFID tag reading, a rules engine that pings Slack in under 3 seconds,
  consent capture flows, CRM sync, calibration tools, and quality targets — is
  now slotted into the right phase with notes on what depends on what. We also
  caught three things the pod plan forgot: nothing in the system yet turns raw
  camera detections into zone enter/dwell events (now the first task of Phase
  2), retried actions could post to Slack twice (dispatchers now dedupe), and
  six event types needed later are now registered up front so nobody has to
  rework the schema mid-build.
  *`docs/roadmap.md` rewritten: pod-week refs (W1–W6) annotated per phase;
  spatial-event deriver added as P2 linchpin; contract additions
  (`rfid.read`, `spatial.tagged`, `intent.scored`, `drift.detected`,
  `calibration.updated`, `crm.retract`) queued for P3; ADR-002 (rule spec as
  JSON, edge fires / browser previews) + auth unification + NLQ-templates-vs-
  Cypher added to open decisions; standing SLO table added; AGENTS.md updated.*

### Added — 2026-07-27 (evening)

- **The camera script no longer loses data when WiFi drops.** If the backend is
  unreachable, the Python perception script writes events to a small file on disk.
  When the backend comes back, it sends the saved events first — oldest first —
  then carries on live. Verified with an automated test: kill the bus, buffer 2
  events, restart, all 3 arrive in the right order and appear in the graph.
  *Offline buffer + ordered replay in `perception/realmspace.py` (`--buffer-file`,
  default `.bus-buffer.jsonl`); replay happens before each new post.*

- **The live dashboard numbers are now real even after you close the tab.** The
  "Unique today", session length, and traffic chart on `/live` used to fall back to
  fake numbers (or zeros) when the camera wasn't running. Now they're computed from
  the permanent event record — the same record the backend keeps — so a recorded
  session still shows its true numbers, and they survive a page reload.
  *New `useSessionBusStats` hook derives KPIs from the durable local log (which
  mirrors the remote bus over WebSocket) and polls `/v1/graph/{tenant}/{session}`
  so "unique visitors" is the graph's own Person-node count. Wired into the four
  KPI tiles + traffic panel on `/live`.*

### Changed — 2026-07-27 (evening)

- **Phase 1 is done.** The spine — camera → event → stored → graph → dashboard —
  works end to end on the laptop with zero cloud. Postgres is a deploy-time choice
  now, not a build task.
  *All Phase 1 roadmap checkboxes ticked in `docs/roadmap.md`.*

### Added — 2026-07-27 (earlier)

- **The system now has a memory.** Before, the dashboard forgot everything when you
  closed the tab. Now there's a small server that writes down every event (person
  entered, person left, dwell) in order, forever, and can replay them later.
  *ADR 001 — graph store decision: relational node/edge tables in the same SQL DB as
  the event bus (SQLite locally / Postgres when available). Neo4j/AGE deferred until
  Ask needs native Cypher (`docs/adr/001-graph-store.md`).*

- **A real backend exists now.** It's a small FastAPI service that receives events,
  stores them, builds the graph of who-dwelled-where, and streams live updates to the
  dashboard over a WebSocket. It also knows who you are (basic roles: admin, operator,
  analyst, viewer).
  *Edge API (`backend/`): append-only event bus, graph writer consumer, graph snapshot
  API, WebSocket fan-out, and RBAC skeleton (`GET /v1/auth/resolve`).*

- **It works without the cloud.** The whole thing runs on the laptop with a simple
  built-in database file — nothing to install beyond Python. Postgres is ready for
  when we want it (one `docker compose up` away).
  *SQLite default store for zero-ops local/edge runs; Postgres schema +
  `docker-compose.yml` included.*

- **The Python camera script can talk to the backend.** Run it with `--bus-url` and
  every person it sees gets posted to the server as a proper event, instead of just
  printing to the terminal.
  *Perception → bus: `realmspace.py --bus-url http://127.0.0.1:8000` posts
  `session.*` and throttled `perception.detection` RealmEvents.*

- **The dashboard can talk to the backend too.** Set one environment variable and the
  dashboard sends its events to the server, listens for the server's events, and shows
  a little pill in the status bar so you know the connection is live.
  *Dashboard remote bus bridge: `NEXT_PUBLIC_BUS_URL` enables dual-write from
  `emit()`, WebSocket replay/mirror into the local durable log, and a StatusBar bus
  pill.*

- **Live camera detections are now real events.** When the browser tracks a person,
  that detection is written to the permanent log — locally, and to the server when
  it's configured.
  *Live detector emits `perception.detection` onto the durable bus (local + remote
  when configured).*

### Changed — 2026-07-27

- **Phase 1 of the roadmap moved forward** — the spine of the product (bus + graph +
  live connection) is mostly in place.
  *Roadmap Phase 1 checkbox updates in `docs/roadmap.md`.*

## [0.1.0] — 2026-07-22, 11:00 +0100 (prior prototype)

- **The demo dashboard.** Six screens (landing, live camera tracking, 3D twin, ask,
  agents, report) — the live camera tracking was real, everything else used fake data
  that matches the shape of real data.
  *Next.js dashboard, client-side durable event log + contracts + multi-tenant
  context + ROI scorecard, Phase-0 YOLO perception stub (stdout JSON).*
