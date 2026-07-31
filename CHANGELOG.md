# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Each entry explains what changed **in plain words** first, then the technical detail.
Sections are dated (with time, local timezone +0100) so you can see when things landed.

**Two tracks are running in parallel.** Phase 1's backend is being built twice on
purpose, so the two approaches can be compared before one is adopted:

- `[postgres-track]` — `floats-agent`. Graph as relational node/edge tables in the
  same SQL database as the bus, SQLite by default. Per `docs/adr/001-graph-store.md`.
- `[neo4j-track]` — `antoniorobles/phase1`. Graph in Neo4j, per `data-model.md`,
  `PRD.md` and `privacy.md`, which all name it.

Entries from 2026-07-28 onward carry a track tag. Earlier entries predate the
split and belong to neither.

## [Unreleased] — last updated 2026-07-31 (late)

### Added — 2026-07-31 (late) — `[neo4j-track]` live events reach the browser

- **The dashboard can now watch what the backend sees, as it happens.** Open a
  connection and every event for that activation arrives the moment it lands —
  a person entering the entrance, a dwell finishing, anything. Measured at
  **58ms typical and 175ms worst case** from the event arriving to it appearing
  at the far end, against a 500ms target. Measured against a real running
  server, not estimated.
  *`WS /v1/ws/{tenant_id}/{session_id}` (`backend/app/routers/live.py`), an
  in-process fan-out hub (`app/hub.py`), and a `broadcast` consumer that reads
  the log and pushes — so socket delivery is a bus consumer like everything
  else, not a hook wired into the write path.*

- **Reconnecting after a wifi drop picks up exactly where it left off.** The
  client says which event it last saw; it gets what came after, and nothing it
  already had. On a conference network that is the difference between a clean
  reconnect and a browser re-processing the whole session.
  *`?since_seq=N` is a real cursor into `event_log`, the same discipline the
  consumers use, extended to the last hop. The other track replays from zero
  capped at 200 on every connect — worth comparing.*

- **The socket speaks the shape the dashboard already expects.** The event
  contract the browser declares as canonical uses `eventId`/`tenantId` and
  millisecond timestamps; the backend had been emitting `event_id` and date
  strings. Fixed now, while nothing depends on it, so POD 3 writes one
  integration rather than a translation layer per backend.
  *`EventOut` gains a camelCase alias generator and ms-epoch serialisers, per
  `dashboard/src/lib/contracts/events.ts`. Producers may still POST snake_case.*

- **A closed tab can't take the live view down for the rest of the booth.**
  Dead connections are dropped from the fan-out rather than raising.

- **Unblocked the RFID reader work before it started.** `rfid.read` was in no
  contract anywhere, and the event validator would have rejected it — whoever
  picked up that task would have hit a flat refusal from the API with nothing
  explaining why.
  *Added to `event-bus-spec.md` §3 and to the accepted namespaces. Flagged that
  `contracts/events.ts` still needs the matching type.*

- **Still not authenticated.** The tenant comes from the URL, so anyone who can
  reach the process can watch any activation's live feed by guessing an id.
  Same as the other track today. Localhost only until the auth item lands.

### Added — 2026-07-31 (early) — `[neo4j-track]` the two halves finally meet

- **The system now turns "a camera saw a person" into "someone visited the
  Entrance and stayed 50 seconds."** Until now the backend had a log nothing read
  and a graph nothing wrote. Two background workers close that gap: one watches
  the log and works out zone entries, exits and dwell times; the other turns those
  into the actual graph of who-went-where. Post a detection to the server and a
  moment later the graph has a person, a zone, and the edges between them.
  *`backend/app/consumers/`: `tracker.py` derives `spatial.zone_enter` /
  `zone_exit` / `dwell` and appends them back onto the bus as an ordinary
  producer; `graph_writer.py` projects those into `Person`/`Zone` nodes and
  `ENTERED`/`LEFT`/`DWELLED_IN` edges. Both run as asyncio tasks off the FastAPI
  lifespan and are reported by `GET /health`.*

- **Re-running the whole pipeline changes nothing — proven, not assumed.** If the
  server crashes halfway through, or someone deliberately rewinds it to replay a
  session, it reprocesses everything and produces not one duplicate event, person
  or edge. That property is what makes the "replayable" claim in the bus spec real
  rather than aspirational, and it is what stops a single dwell being counted
  twice in an ROI report.
  *Derived event ids (`consumers/ids.py`) make re-emission a no-op against the
  log's `UNIQUE(event_id)`; `Consumer.on_replay` clears derived in-memory state on
  a cursor rewind. Both verified by deliberately breaking them and confirming the
  guarding tests fail.*

- **A bad event can no longer jam the queue.** An event the workers cannot process
  is retried, then set aside in a "dead letter" list with the reason, and the
  queue moves on. Previously one malformed message would have blocked everything
  behind it.
  *`consumers/base.py` holds the single poll → handle → advance → dead-letter
  loop; failures land in `dead_letter` with the traceback and the cursor advances
  past them (HITL review screen is Phase 3).*

- **The edge and the browser now agree on what "inside a zone" means.** The zone
  geometry was ported from the dashboard rather than rewritten, so a person can't
  be in the Lounge on screen and the Atrium in the report.
  *`consumers/zones.py` is a line-for-line port of
  `dashboard/src/skills/zone-detect.ts`; the 30s dwell threshold matches
  `agents/definitions/dwell.ts`.*

### Fixed — 2026-07-31 (early) — `[neo4j-track]` review pass over Phase 1

- **Redrawing a zone mid-session now actually takes effect.** The tracker cached
  zone shapes the first time it saw them and never looked again, so an operator
  moving a boundary would have kept getting dwell attributed to the old one, with
  nothing to indicate anything was wrong.
  *30s TTL on the zone cache in `consumers/tracker.py`; `forget_zones()` existed
  but was never called.*

- **Four other bugs found before they could bite:** memory that grew for every
  visitor and never shrank; a database write per camera frame, which would have
  been the first thing to miss the sub-500ms target; event types accepted with no
  checking, so a mistyped one would be stored forever and silently never
  processed; and an error endpoint that told anyone asking which internal
  component had failed.
  *Bounded tracker state with oldest-first eviction; one commit per batch instead
  of per event; `type` validated against the `event-bus-spec.md` §3 namespaces;
  `/health` logs detail and returns `"error"`. Each has a test that fails against
  the old code.*

- **The status docs told the truth again.** `architecture.md` was named for the
  whole system but documented only the dashboard; the README and PRD both still
  said the graph store and consumers were unbuilt.

### Added — 2026-07-30 (early) — `[neo4j-track]` the graph store

- **The product's actual subject matter now has somewhere to live.** Sessions,
  people, zones and the relationships between them — who entered what, who
  lingered where — now have a real store with rules the database enforces, rather
  than being implied by a list of events.
  *Neo4j, schema and constraints transcribed from `data-model.md` into
  `backend/app/graph/schema.py`. Eight node types constrained; `Frame` has no id
  to key on, which is now flagged in the doc.*

- **One tenant's data cannot leak into another's, by construction.** Every key in
  the graph starts with the tenant, and every single query the system can make is
  required to name one — there is no way to write a query that reads across
  tenants, because all the query code lives in one place that demands it.
  *All Cypher confined to `backend/app/graph/repository.py`, `tenant_id` a
  required argument on every function.*

- **Known limit, stated rather than buried:** the free edition of Neo4j cannot
  enforce that separation itself — no row-level security, and it will happily
  store a record with no tenant on it. Application code is the only thing
  enforcing it today. Fine while a kit runs one client's activation at a time; a
  real gap once tenants share a server.
  *Verified against the running instance (`51N27: property existence constraint is
  not supported in community edition`). Recorded in `data-model.md` →
  "Store decision".*

- **Database structure is versioned, not typed in by hand.** Anyone cloning the
  repo runs one command and gets an identical graph schema; running it twice does
  nothing.
  *`app/graph/migrations.py` — Alembic's idea in ~60 lines, since Neo4j has no
  equivalent; applied versions recorded as `(:_SchemaVersion)` nodes.*

### Added — 2026-07-28 (night) — `[neo4j-track]` the durable event log

- **The system stopped forgetting.** Everything that happens is written down in
  order, permanently, and can be read back or replayed later. Close the process,
  restart it, and the record is still there.
  *`backend/` — FastAPI + Postgres, `event_log` schema transcribed by hand from
  `event-bus-spec.md` §2 so it provably matches the spec. `POST /events`,
  `GET /events?tenant_id=…&since_seq=…`.*

- **A camera that loses its connection can safely resend.** If a message times out
  the sender genuinely cannot tell whether it arrived, so it sends again — and the
  log recognises it as the same event and does nothing, rather than recording the
  same person twice.
  *Idempotent on a producer-assigned `event_id` via `ON CONFLICT DO NOTHING`;
  duplicates return the original `seq` with `200` rather than `201`.*

- **Proven against a real database, not a stand-in.** The tests run against actual
  Postgres, because the thing being tested is whether Postgres behaves as the spec
  claims — a mock would only replay our own assumptions.

### Known gaps in `[neo4j-track]` as of 2026-07-31

Stated plainly so the two tracks are compared honestly, not on impressions:

- **No authentication.** `tenant_id` is supplied by the caller and not verified, so
  anything that can reach the machine can read any tenant's log and write forged
  events. Localhost only until the RBAC roadmap item lands.
- **Perception is not wired in.** `realmspace.py` still prints to stdout; no
  offline buffer yet.
- ~~No WebSocket bridge~~ — landed 2026-07-31 (late). The dashboard still has to
  be pointed at it (POD 3's job).
- **No gaze, group or pass-by events** — gaze needs pose data perception doesn't
  emit, pass-by is Phase 2.

<!-- entries below this line are the postgres-track and the pre-split work -->


### Added — 2026-07-29 (night) — Phase 2 linchpin: the spatial-event deriver

- **The system now knows when someone actually visits a zone, not just that a
  camera saw a person somewhere.** Until tonight, nothing turned raw camera
  detections into "entered the display," "stayed for 40 seconds," "left." That
  translation — the thing the whole ROI report depends on — now runs live in
  the browser next to the existing tracker, using the same zone maps drawn
  during onboarding. It emits directly onto the durable bus, so the graph and
  (soon) the scorecard see real zone activity the moment a session runs.
  *New `dashboard/src/lib/live-session/spatial-deriver.ts`; wired into
  `lib/live-session/store.ts` (`ingestStats` + `resetSession`) and
  `detector-runtime.ts`; emits `spatial.zone_enter`/`zone_exit`/`dwell`/
  `passby` via the existing `emit()` path. `backend/app/graph_writer.py`
  already projects enter/exit/dwell into graph edges — no backend change
  needed.*
- **Built-in noise filtering, so a flaky camera frame doesn't invent a fake
  visit.** A zone change only counts once it holds for 600ms (kills boundary
  flicker), visits under a second are dropped as noise, and if someone
  disappears mid-visit the exit is flagged `"dropout"` so later reporting can
  discount it. Ending a session closes out anyone still "inside" a zone so
  the last few visits of the night aren't silently lost.
  *Confirm-window state machine + `flushAll("session_end")`; timestamps for
  dwell duration use the real crossing time, not the delayed confirm time, so
  durations stay accurate despite the anti-flicker delay.*
- **A one-frame camera hiccup no longer looks like someone leaving.** The
  detector already tolerates brief missed frames; the new zone logic now sees
  that same tolerant track list instead of only the strictly-confirmed one, so
  it doesn't misread a hiccup as a dropout.
  *`DetectorStats.tracks` added alongside `activeTracks`
  (`lib/live-session/types.ts`), threaded through both detector runtimes
  (`detector-runtime.ts`, `components/viz/WebcamDetector.tsx`).*
- **Checked by hand before trusting it**: 34 scripted scenarios (entry
  confirmation, dwell accuracy, flicker suppression, dropout flagging, passby
  radius, zone switching, sub-second-dwell rejection, session-end flush,
  unconfirmed-track filtering) run against the real deriver source in an
  isolated harness — all pass.

### Fixed — 2026-07-29 (night) — dashboard lint/type hygiene

- Cleared every `eslint` error in the dashboard (7 → 0): three components were
  reading `Date.now()`/`new Date()` directly during render or calling
  `setState` synchronously inside an effect body, both of which React's newer
  purity rules correctly flag as unstable. Fixed with the proper idiom in each
  case — a ticking clock seeded once and only ever updated from an interval
  callback (`StatusBar.tsx`, `live/page.tsx`), an initial-state computed from
  the subscription target instead of set inside the effect (`AuthProvider.tsx`),
  and a `useSyncExternalStore`-based hydration hook replacing a `mounted`
  state+effect pair (`TrafficChart.tsx`, new `hooks/useHydrated.ts`). One
  remaining flag (`PrefabSelector.tsx`) is a false positive — the call is
  inside an `onClick` handler, never render — and is suppressed with a
  one-line, reasoned `eslint-disable`.
  *Also: a stale `useMemo` dependency masking direct state reads
  (`agents/page.tsx`), two unused imports, one `let`→`const`, and a missing
  `useMemo` dependency pair in the twin's zone geometry (`TwinScene.tsx`).*

### Added — 2026-07-27 (night)

- **The research brain is now part of the repo.** A reusable "analyst" skill
  turns dumps of links and papers into dated, written deep-dives stored in
  `docs/research/` — each one checked against what we've actually built, not
  just summarised. The first one analysed a CHI '26 academic paper plus ~40
  links and found: strong independent validation of the whole product idea, a
  big competitor (Cvent) moving toward our benchmark story, and proof that
  "we don't store images" is no longer a unique claim.
  *`~/.agents/skills/realmspace-analyst/SKILL.md` (global skill);
  `docs/research/2026-07-27-chi26-digital-twins-and-cv-dump.md`.*

### Changed — 2026-07-27 (night) — next sprint prepped from research findings

- **Privacy now reads like a regulator wrote it.** The privacy doc explicitly
  cites the UK ICO's biometric-recognition guidance and explains, in
  compliance vocabulary, why realmspace sits outside that strict regime —
  plus visitors can now opt out even *after* their visit.
  *`docs/privacy.md`: ICO section + post-visit opt-out.*
- **The competitor map got four new rows and a sharper moat.** Added the
  privacy-safe occupancy category (XY Sense, Cisco Spaces), event-platform
  incumbents (Cvent — flagged claiming our cross-event benchmark moat),
  lead-capture apps (Popl — integration target), and loyalty wallets
  (Delphize — monitor). Our #2 moat is reworded from "privacy" to the
  consent-gated identity bridge, since no-images is now table stakes.
  *`docs/competitive-landscape.md`.*
- **Phase 2 absorbed the academic lessons.** Two new task lines: session-hygiene
  heuristics (the CHI paper threw away 71% of raw sessions — ours won't poison
  the metrics) and per-touchpoint-type metric normalisation (comparing unlike
  exhibits fairly — the problem academia couldn't solve). The ROI framework
  now also speaks the museum buyer's vocabulary: *attracting power* and
  *holding time*.
  *`docs/roadmap.md` P2; `docs/roi-framework.md` Layers 1–2.*
- **Museums are now a named sales target.** The 50-email validation sprint
  reserves 8–10 slots for museum/cultural-institution leads, and the demo
  Loom script cites the CHI '26 paper as independent proof.
  *`docs/gtm.md`; `docs/README.md` research index; `AGENTS.md` handoff updated.*

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
