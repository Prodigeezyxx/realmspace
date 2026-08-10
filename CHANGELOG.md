# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Each entry explains what changed **in plain words** first, then the technical detail.
Sections are dated (with time, local timezone +0100) so you can see when things landed.

## [Unreleased] — last updated 2026-08-10

### Added — 2026-08-10 — Phase 2: Ask the Room (NLQ → SQL, real)

- **You can now ask the room questions in plain English and get real answers.**
  The Ask the Room feature is no longer a regex-mocked demo — it runs real
  queries against the durable event bus and returns live numbers, tables, and
  chart-ready data. Fourteen constrained SQL templates cover every meaningful
  query (unique visitors, average dwell, dwell by zone, zone entries, person
  paths, person dwell time, surface interactions, passbys, engaged visitors,
  session span, event type breakdown, peak concurrency, traffic over time),
  with a graceful fallback message when a question falls outside the supported
  set. When `OPENROUTER_API_KEY` is configured, an LLM selects and
  parameterizes the right template from the natural-language question;
  otherwise a regex stub maps common phrasings to templates at zero cost. The
  new `POST /v1/ask` endpoint returns the answer, chart type suggestion, and
  structured data (table rows, labels, values) — exactly the contract the
  dashboard's Ask page expects.
  *New `backend/app/ask.py` module: `QueryTemplate` dataclass with 14
  templates (4 number, 4 bar, 1 line, 2 table, 1 pie, 2 fallback), per
  ADR-001 relational projection over `graph_nodes`/`graph_edges` +
  `event_log`; `_call_llm()` → OpenRouter OpenAI-compatible client with
  temperature 0.1; `_stub_match()` regex fallback; `ask()` public entry
  point. New `AskRequest`/`AskResponse` Pydantic models in
  `backend/app/models.py`. Config extended with `openrouter_api_key` and
  `openrouter_model` (`poolside/laguna-xs-2.1:free` default) in
  `backend/app/config.py`. Verified against `ses_m6fl23rwh2a` (951 events,
  24 people) and `s_score_b58a7c3b` (22 events, full spatial types) — 14/14
  templates return correct, traceable answers.*

### Added — 2026-08-02 — Phase 2: the twin replays recorded sessions from the bus

- **The 3D twin now replays real recorded sessions — no more hand-drawn paths.**
  Pick any recorded session from a new dropdown and the twin rebuilds itself
  from the bus: camera detections become per-person waypoints on the booth
  floor, zone entries become zone visits, surface interactions become counts,
  and dwell events become dwell totals. The virtual clock spans the real
  session's first-to-last event and the scrubber seeks across it, so a client
  can watch an actual 24-person, 6.5-minute activation play back — or a
  53-person one — not a demo script. When no recording exists yet the page
  says so and falls back to demo tracks with an honest pill, and live
  camera-mode is untouched. Verified end-to-end against both real sessions on
  the edge bus (951 and 1,048 events).
  *New bus→tracks reducer `dashboard/src/lib/twin/replay-tracks.ts`
  (`reduceReplaySession` + generic `positionsAtFrom`); new hooks
  `useTwinReplay()` (local log first, remote bus upgrades) and
  `useRecordedSessions()` (local + remote merged); `TwinScene` gained a
  `tracks` prop (People / PersonTrail / heatmap fallback); `/twin` page wires
  the picker, source pills ("recorded replay" vs "demo replay"), real visitor
  list and real surface counts.*
- **The bus can now list recorded sessions, and detections carry frame size.**
  A replay picker needs a session catalog: the edge API grew
  `GET /v1/sessions/{tenant_id}` (session id, event count, first/last event
  time, most recent first) backed by a single GROUP BY over `event_log`.
  Detections now also carry their sensor frame dimensions, so future
  recordings reconstruct booth coordinates exactly instead of assuming the
  640×480 default (pre-contract sessions fall back gracefully).
  *`backend/app/bus.py` `list_sessions()` + `SessionMeta` model + endpoint;
  `DetectionPayload` gained optional `frameWidth`/`frameHeight` (additive
  contract change), emitted by `live-session/store.ts`; dashboard helpers
  `remoteSessionList()` and `remoteReadAll()` (paged past the 500-event
  cap), `bus/log.ts` `listLocalSessions()`.*
- **Backend regressions re-verified**: scorecard_test (20+ assertions) and
  smoke_test pass against the new endpoint; `GET /v1/sessions/t_floats`
  returns all 12 recorded partitions.

### Added — 2026-08-01 (evening) — Phase 2: the live ROI tile on `/live`

- **Operators can now watch the ROI number move while the room is running.**
  A new "Live ROI" tile sits at the top of the live screen's side column for
  real (non-demo) sessions: the big ROI-per-dollar figure with its benchmark
  verdict pill, engagement rate, average dwell, and the holding-time index —
  all refreshed every 15 seconds straight from the edge API. It's the
  day-2 optimisation surface: the scorecard the report will print tomorrow
  is the scorecard the operator is steering today. When the bus has no
  events yet, the tile says so instead of showing anything fake.
  *New `dashboard/src/components/live/LiveRoiTile.tsx` (polling via
  `useSessionOutcome({ pollMs })`); mounted in `live/page.tsx` for
  non-demo sessions only — the pitch demo's look is untouched.*

- **The v1 scorecard formulas are now frozen in the repo.** A client's report
  must be reproducible forever, so every metric — hygiene rules, the four
  layers, expected-dwell presets, economics inputs — is written down once in
  `docs/metric-definitions.md` with its formula, source events, and the two
  reference implementations that must stay in lockstep. Any future change is
  a versioned v2, never an edit-in-place.
  *New `docs/metric-definitions.md`; roadmap P2 items "signature metrics"
  and "metric definitions locked" marked done.*

### Added — 2026-08-01 (late afternoon) — Phase 2: the report now runs on real session data

- **The client report is no longer a static mock.** When a session has real
  events on the bus, `/report` now renders a data-driven report: hero numbers
  (unique visitors, avg dwell, pass-by, CPEV), a first-touch funnel ("where
  visitors went first" — a real path-order signal, not a made-up sequence),
  zone-by-zone dwell, top moments (peak concurrency time + longest single
  dwell, both clock-tagged), and rule-generated recommendations that each
  cite the figure that produced them. The static pitch copy still renders
  when there is no real data — the demo stays honest.
  *New `dashboard/src/components/report/ReportLive.tsx`; `report/page.tsx`
  picks live report vs. demo via a new shared `useSessionOutcome()` hook
  (also refactored into `RoiScorecard.tsx` so card and page share one fetch).*
- **Every report number now has a paper trail.** The outcome API grew the
  per-figure detail the report needs: a zone breakdown (entries, visitors,
  avg + total dwell — computed over the bus where the collapsed graph
  projection would lose multi-visit dwells), the first-touch funnel,
  `peakConcurrencyAt` (when the peak crowd happened), and `longestDwell`
  (who, where, how long, when). The report footer and the headline cite the
  event count, and recommendations name their source metric.
  *`scorecard.py`: zone_breakdown + funnel + peak time + longest dwell;
  new `ZoneOutcome` / `FunnelStepOutcome` / `LongestDwell` models; the
  dashboard mirrors them in `contracts/outcome.ts`.*
- **Verified by hand again**: 20+ assertions extended (zone breakdown sums,
  funnel shares, peak/longest-dwell fields, empty-session edges) plus a live
  HTTP check against a real server process (11 events → 2 visitors, dropout
  discounted, funnel + longest dwell present).

### Added — 2026-08-01 (afternoon) — Phase 2: the session outcome API (scorecard is now server-authoritative)

- **The ROI scorecard is now a real backend endpoint, computed from the
  durable event stream — not just a client-side calculation.** A session's
  four layers (Reach / Engagement / Affinity / Pipeline) are now served as
  `GET /v1/sessions/{tenant}/{session}/outcome` by the edge API, so the
  report and (later) the live tile all pull one authoritative number set
  that any auditor can trace back to the bus. The dashboard's scorecard
  card now asks the edge API first, falls back to the local durable log,
  and only then to demo data — with a "source" pill always stating which
  one is showing.
  *New `backend/app/scorecard.py` (consumer over `bus.read()`; formulas
  mirror `dashboard/src/lib/roi/scorecard.ts` in lockstep) +
  `SessionOutcome` models + `GET /v1/sessions/{tenant_id}/{session_id}/
  outcome` in `backend/app/main.py`; `remoteSessionOutcome()` in
  `dashboard/src/lib/bus/remote.ts`; new `contracts/outcome.ts` contract;
  `RoiScorecard.tsx` now fetches remote-first with a local/demo fallback
  and a visible source pill.*
- **The scorecard now discounts noisy data instead of silently reporting
  it** — the CHI '26 field-study lesson applied to our own numbers. A visit
  that ends in a tracked-person dropout (track disappeared mid-zone) is
  excluded from dwell stats and counted separately, and dwells longer than
  2 hours (a sensor hang, not a visit) are dropped as noise. The card shows
  a "data quality" line so a client never sees cooked numbers pretending to
  be clean.
  *Dropout discounting keys on the `reason="dropout"` flag on the preceding
  `spatial.zone_exit`; duration sanity capped at `MAX_DWELL_SEC` (2h);
  both exposed in `SessionOutcome.hygiene`.*
- **Exhibits are now compared fairly, per type — the metric the CHI '26
  paper said the field couldn't produce.** "Holding time index" reports
  average dwell relative to an expected dwell for the zone's kind
  (entry/reveal/engagement/lounge/retail/sponsor/demo/press/exit…), so a
  45-second screen visit and a 2-minute demo are judged against their own
  baselines, not against each other. v1 expected-dwell presets are frozen
  in the backend and overridable per call.
  *`holdingTimeByKind` + `holdingTimeIndex` on the engagement layer;
  `EXPECTED_DWELL_SEC_BY_KIND` presets in `scorecard.py`, overridable via
  `expectedDwellSecByKind` query param; zone kinds/weights passed in via
  `zoneConfig` until a `session.started` producer carries them.*
- **Verified by hand before trusting it**: 20+ assertions over a scripted
  session (weighted attention math, dropout exclusion, insane-dwell drop,
  per-kind normalization, engagement threshold, pipeline economics, ROI
  benchmark verdicts, empty-session edge) all pass
  (`backend/app/scorecard_test.py`), plus a live HTTP check against a real
  server process.

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
