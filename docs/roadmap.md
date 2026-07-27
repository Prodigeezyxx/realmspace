# realmspace — Roadmap

> Sequenced by **client value** (see `VISION.md` §4), not by technical layer.
> Governing rule: **prove one vertical slice end-to-end before widening.**
> The existing prototype stays the center of gravity — we harden and extend it.
>
> **Reconciliation note (2026-07-27):** this roadmap absorbs the external
> *6-Week Pod Roadmap v1 (2026-07-23)*, which remains unchanged. Pod-week
> references (`W1`–`W6`) are annotated inline so the two documents cross-read.
> Our sequencing deliberately differs from the pod doc in one place: **Prove ROI
> stays second** (the wedge — `VISION.md` §4), so the pod doc's W5 measurement
> deliverables land here in P2, ahead of rules/identity/CRM.

Legend: ✅ exists today · 🟡 mocked/partial · 🔲 to build.

---

## Phase 0 — Foundations already in place (audit)

| Capability | State |
|---|---|
| Next.js dashboard: landing, live, twin, ask, agents, report | ✅ |
| **Live on-device tracking** (TF.js + COCO-SSD + centroid tracker) | ✅ (real) |
| Python perception stub (YOLO + ByteTrack → JSON) | ✅ emits to bus |
| Session onboarding wizard (prefabs, zones, touchpoints) | ✅ |
| Docs: PRD, architecture, data-model, privacy, gtm | ✅ |
| Docs: vision, brand, roi, integrations, consent, event-bus, tenancy, competition | ✅ |
| Backend event bus / graph / API | ✅ FastAPI + SQLite (`backend/`); Postgres schema ready |

**Also parallel (founder-led, no code):** run the `gtm.md` validation sprint —
90s Loom + landing + 50 cold emails. **Pass = 5 booked demos in 7 days.**

---

## Phase 1 — The Spine (make the backend real) · ~2 weeks · ✅ DONE 2026-07-27

*Goal: replace the mocked/in-memory backend with a real, replayable event bus +
graph, wired to the existing dashboard. Multi-tenant from the first commit.*
*(= pod W1, minus RLS/JWT/RFID which land in later phases.)*

- ✅ FastAPI service + **append-only event bus** (`event-bus-spec.md`) — SQLite locally; Postgres schema ready (`backend/`, `docker-compose.yml`)
- ✅ Graph store + schema from `data-model.md`; `tenant_id` everywhere
      (`multi-tenant.md`, `docs/adr/001-graph-store.md` — relational projection)
- ✅ Consumers: graph writer (edge, near-real-time); tracker still browser/YOLO-side
- ✅ WebSocket: bus → dashboard (bridge via `NEXT_PUBLIC_BUS_URL`); perception POSTs into bus
- ✅ Perception stub hardened: emits into bus (`--bus-url`) with **offline buffer +
      ordered replay** (`--buffer-file`) when the API is unreachable
- ✅ `/live` KPIs read from the durable bus/graph when the camera is idle
      (`useSessionBusStats`) — unique visitors, session span, traffic curve
- ✅ Auth resolves user → org → role (RBAC skeleton — `GET /v1/auth/resolve`)

**Acceptance:** real camera → real event in durable log → real graph node → real
`/live` KPI, tenant-scoped, works offline then replays on reconnect. `< 500ms`
detection → dashboard. *(Met on the SQLite edge default: verified live
2026-07-27 — 24 anonymous persons / 799 events from one real camera session;
offline buffer + ordered replay test-passed. Postgres remains a deploy-time
choice, one `docker compose up` away.)*

**Deliberately deferred from pod W1:** Postgres RLS (→ P6), JWT middleware
(→ P3/P6, see open decision 5), RFID producer (→ P3), tenant switcher UI (→ P6).

---

## Phase 2 — Prove ROI (the #1 client job) · ~2 weeks

*Goal: turn the report into a real, defensible ROI proof. This is the wedge.
(Absorbs the pod doc's W5 measurement deliverables, pulled forward per
`VISION.md` §4: scorecard, session outcome API, report generator, live ROI
tile, twin replay.)*

- 🔲 **Spatial-event deriver** (the linchpin producer): zone polygons +
      `perception.detection` → `spatial.zone_enter` / `zone_exit` / `dwell` /
      `passby` onto the bus. The scorecard, the rules engine (P3), and the graph
      writer's zone edges all depend on this. Edge-side consumer.
- 🔲 **4-layer scorecard consumer** (Reach/Engagement/Affinity/Pipeline) —
      real formulas from `roi-framework.md`, computed over the graph
- 🔲 **Session outcome API** — REST endpoint returning the full scorecard per
      session, tenant-filtered; powers report + live tile + external pulls
- 🔲 Report generator: templated from **real session data** (same layout, real
      numbers); figures cite source event seqs so an auditor can trace any number
- 🔲 Live ROI tile on `/live` (day-2 optimisation)
- 🔲 Signature metrics: dwell-weighted attention, engagement rate, funnel, CPEV
- 🔲 Twin plays back **recorded** sessions from the bus (replace seed paths;
      virtual clock + scrub seeks the cursor — data swap, not a rebuild)
- 🔲 **Ask the Room** real: LLM → **constrained, validated query templates over
      the relational projection** (allow-list; rejects off-schema input) →
      answer. *Needs AI provider key + ADR for template-vs-Cypher (see open
      decision 3).* Regex mocks removed.
- 🔲 Metric definitions locked for v1 scorecard (formulas frozen in repo)

**Acceptance:** 24h after a session, a client gets a data-true report with the
4-layer scorecard and a stated ROI ratio; Ask answers a live question in
`< 5s`; twin replays a real recorded session; every figure traces to event seqs.

---

## Phase 3 — Act: Rules Engine + Staff Prompts + RFID · ~2 weeks

*Goal: turn signals into real-time action on the edge (< 3s), anonymous.
(= pod W2, plus RFID ingestion from pod W1/W2.)*

- 🔲 **Event contract additions (additive, before anything else this phase):**
      `rfid.read`, `spatial.tagged`, `intent.scored`, `drift.detected`,
      `calibration.updated`, `crm.retract` — pre-registered so no later phase
      pays a schema-migration tax
- 🔲 **ADR-002: rule spec as JSON data** (trigger/condition/action) + one
      evaluator in the edge engine + browser preview reuse. Existing browser
      agent runtime becomes *simulation/preview* of edge rules — no split-brain
- 🔲 Rules-engine consumer (edge-local, `< 3s` SLA): evaluates persisted,
      tenant-scoped rules against `spatial.*` / `surface.*` events; emits
      auditable `rule.fired`
- 🔲 Persisted rules schema (survives restart) + rules composer UI
      (NL-assisted, plain-English → rule spec → operator confirms)
- 🔲 Action dispatchers: Slack / HMAC-signed webhook / screen swap /
      **staff prompt** — each with healthcheck, retry policy, cost meter, and
      **per-dispatch idempotency keyed on the `rule.fired` eventId** (retry
      never double-posts)
- 🔲 Dead-letter with attempt counting + exponential retry + **HITL review
      screen** (inspect, fix state, retry to success)
- 🔲 **Cost telemetry**: `cost.metered` emitters on anything that costs money
      (LLM tokens, action units) + cost tile on `/live` — unit economics from
      real data
- 🔲 **RFID producer** (hardware-gated, parallel; slips without blocking the
      phase): `rfid.read` onto the bus (MQTT/serial), reader→zone calibration
      config; **vision × RFID fusion** consumer → `spatial.tagged`
      (reader position + RSSI decay + track proximity; no biometrics)
- 🔲 Auth middleware unification (verify Firebase JWT → tenant + role) so rule
      saves are authenticated — see open decision 5

**Acceptance:** "when 5 people dwell at entrance 30s → ping Slack" fires live in
`< 3s`; a forced failure lands in HITL and retries to success without
duplicating; `cost.metered` visible per session; (if hardware present) an RFID
pickup correlates to the nearest tracked person as `spatial.tagged`.

---

## Phase 4 — Attribute: Lead Capture + Consent + CRM · ~2–3 weeks

*Goal: bridge anonymous behaviour to identified pipeline, with consent, into any
CRM. (= pod W3 + W4.) New bus consumers only — no producer changes.*

- 🔲 **Consent capture** surfaces: badge scan / QR (visitor phone, versioned
      copy) / kiosk tablet / form webhook-in → `consent.captured`
      (`consent-and-identity.md`), tiers T1/T2/T3 + redlines
- 🔲 **Consent copy versioning** — `copy_version` stamped on every consent
      event; auditable trail; management UI per activation
- 🔲 **Identity consumer**: consent-gated anon → Contact link →
      `identity.resolved`; **consumer-side write-time invariant** refuses
      `IDENTIFIED_AS` without a non-withdrawn ConsentEvent in the same
      transaction (complements the existing producer-side redline in `emit()`)
- 🔲 **Intent-signal scoring v1** (rules-based: dwell × zone funnel × surface ×
      RFID where present) → `intent.scored`; **live intent overlay + top-N
      intent list** on `/live`
- 🔲 **LeadHandoff v1 schema + validator** (additive-only versioning; carries
      `spatial_intent`, consent basis, `dedupe_key`)
- 🔲 **Attribution consumer**: assemble LeadHandoff, apply model
      (first/last/linear/influenced) + window (30/60/90d) → `handoff.lead`
- 🔲 **CRM adapters** (interface: auth/map/upsert/retract/health/capabilities):
      HubSpot (reference) → Salesforce → Pipedrive → **BYO HMAC-signed
      webhook**; per-tenant field mapping as config; retry-with-backoff (429
      recoverable); retract-capable
- 🔲 **CRM connection UI**: OAuth + field mapping + healthcheck — wire HubSpot
      to a tenant in < 10 min
- 🔲 **Withdrawal / erasure flows**: `consent.withdrawn` → drop link,
      re-anonymise, `crm.retract` to capable adapters; visitor + admin UI
- 🔲 Handoff replay tool (re-emit any session from cursor)
- 🔲 **Attribution ledger** CSV export (touch → outcome + basis) + CFO one-pager
      (`roi-framework.md`)
- 🔲 (opt, T3) enrichment adapter (Apollo/Clearbit), metered

**Acceptance:** a consented badge scan produces a Contact linked to its spatial
path; a LeadHandoff lands in HubSpot with `spatial_intent` fields; a withdrawal
retracts it; the ledger reconciles booth-touch → outcome.

---

## Phase 5 — Follow-up automation + Intelligence · ~2 weeks

*Goal: close the loop with contextual follow-up and smarter agents.
(= pod W5's agent deliverables.)*

- 🔲 **Insight agent**: periodic bounded graph snapshot → `insight.generated`
      with text + supporting event IDs; shown on `/live`; click-through opens
      the underlying events
- 🔲 **Event snapshotting for LLM** — per-session digest, token-bounded
- 🔲 **Contextual SDR** draft agent (path-aware follow-up emails, consent ≥ T2)
      — draft lands in CRM outbox
- 🔲 Live Analyst agent (NL queries over the live bus, reuses P2's template
      engine)
- 🔲 Floor orchestrator (predict density, staff allocation) — if demand proven

**Acceptance:** post-session, a consented lead receives a draft follow-up
referencing the exact zones/surfaces they engaged; insights on `/live` trace to
source events; analyst answers live NL queries.

---

## Phase 6 — Platform hardening (public product) · ~2 weeks

*Goal: everything an outside operator needs to run realmspace unassisted.
(= pod W6.)*

- 🔲 **Multi-tenant isolation hardened**: Postgres RLS + invariant tests
      (tenant-A JWT can never read tenant-B) + chaos suite; external review ready
- 🔲 **RBAC complete**: owner / admin / operator / viewer, enforced at API
      middleware and mirrored in UI; per-tenant API rate limiting
- 🔲 **Cloud replay sync** (opt-in per tenant, anonymised at the edge) →
      **cross-tenant benchmark dataset** → "vs. network median" report tile
- 🔲 **Calibration UI**: cameras, zones, RFID readers — on-site setup < 15 min;
      reader location/range as config, `calibration.updated` on the bus
- 🔲 **CV drift telemetry**: confidence distributions vs rolling baseline →
      `drift.detected` alerts; multi-camera fusion
- 🔲 **Self-serve onboarding**: signup → workspace → activation → calibration →
      session → first report, no ops hand-holding
- 🔲 **Release pipeline**: blue/green, forward-compatible migrations (one
      release), one-command rollback; consumer replay tool for ops
- 🔲 **Billing hooks** (Stripe) + plan metering/limits
- 🔲 **Exports polish**: PDF / CSV / JSON / ledger, auditor-ready
- 🔲 **Terminal SDK v1** documented (third parties emit `surface.interaction`)
- 🔲 **Runbook**: kit prep · calibration · incident · HITL — any operator can
      run a deployment
- 🔲 **Brand re-token** UI to the brand book (Sora/Inter/IBM Plex Mono, orange
      `#FF5C00` / teal `#00D4AA` / base `#0A0B10`) — `brand.md`
- 🔲 Evaluation harness re-run on the v1 build (regression sign-off)

**Acceptance:** a third-party operator self-serves signup → activation → report,
fully isolated, billed, on-brand; external review passes on tenant isolation;
releases roll back in one command.

---

## Engineering targets (standing SLOs — measured, not aspirational)

| Target | Current status |
|---|---|
| `< 500ms` edge event → dashboard | Path verified live; no formal histogram yet — add `recordedAt − occurredAt` telemetry in P2 |
| `< 3s` rule fire → action | P3 |
| `< 5s` NL query answer (p95) | P2 (Ask) |
| `0` events lost on network drop | ✅ buffer + ordered replay, test-passed (P1) |
| `0` PII writes without consent | ✅ producer redline today; consumer-side txn invariant in P4 |
| `100%` consumers idempotent | By upsert design; chaos-proven in P6 |
| `≥ 99%` graph write success | Dead-letter exists; attempt counting + alerting in P3/P6 |
| `≥ 95%` detector precision (booth-lit) | Eval harness in P6 (DSR-owned) |

---

## Timeline (indicative, solo/small-team pace; pod-week refs in brackets)

```
Week:              1   2   3   4   5   6   7   8   9  10  11  12
P1 Spine [W1]    ██ (done)
P2 Prove ROI [W5→]    ██████████
P3 Act [W2]                      ██████████
P4 Attribute [W3+W4]                     ██████████████
P5 Follow-up [W5]                                    ██████████
P6 Platform [W6]                                                 ██████████
GTM sprint       ████ (parallel, founder-led)
1st paid pilot                    ★ embedded (Pavilion tier)
CRM attribution live                                ★
```

---

## Blind spots → living engineering tickets

| Blind spot | Where it's handled |
|---|---|
| Offline / edge reliability | ✅ `event-bus-spec.md` §5 — buffer + replay shipped P1 |
| Latency SLA (< 3s) | edge-run rules engine — P3 |
| CV model drift | drift telemetry + calibration UI — P6 |
| Vision × RFID misalignment | reader calibration + RSSI-weighted joins — P3/P6 |
| Failure states (CRM 429, consent revoked) | dead-letter + HITL, withdrawal flow — P3/P4 |
| Action idempotency on retry | dispatcher dedupe on `rule.fired` eventId — P3 |
| Attribution decay (30/60/90d) | window on outcome edge — P4 |
| Multi-booth / cross-event aggregation | cloud replay + benchmark — P6 |
| Cost telemetry / unit economics | `cost.metered` + tile — P3 |
| Group visits | `Group` node (in `data-model.md`) — P2 |
| Negative signals (pass-by/skip) | `spatial.passby` from the deriver — P2 |
| LLM hallucination in NL query | constrained templates + validator pre-exec — P2 |
| Split-brain rules (browser vs edge) | ADR-002: edge fires, browser previews — P3 |

---

## Open technical decisions (to confirm as we build)

1. **Graph store:** ✅ **DECIDED** — relational `graph_nodes` / `graph_edges` in
   the same SQL DB as the event bus (SQLite edge default; Postgres when
   available). See `docs/adr/001-graph-store.md`.
2. **AI provider:** OpenAI / Anthropic / Gemini for Ask + insights + SDR.
   *Needed for P2.*
3. **NLQ approach:** constrained parameterised SQL templates over the
   relational projection (recommended, zero new infra) vs. native Cypher
   (requires standing up Neo4j/AGE — revisits ADR-001). *ADR needed before P2's
   Ask work.*
4. **ADR-002 — rule spec & runtime:** JSON rule spec; edge evaluator (Python,
   in-process) fires; browser runtime previews. *Needed for P3.*
5. **Auth unification:** verify Firebase JWT at FastAPI middleware → tenant +
   role (composes with existing Firebase auth; replaces resolve-only skeleton).
   *Needed by P3 rule saves; completed in P6 RBAC.*
6. **First CRM confirmed:** HubSpot as reference adapter (P4).
7. **RFID stack:** readers, protocol (MQTT/serial), tag format. *Before P3 RFID
   work; hardware-gated.*
8. **Intent scoring:** rules-based v1 (recommended) vs. small learned model.
   *Start of P4.*
9. **Deployment target for cloud consumers** (managed vs. self-hosted) and
   **observability stack** (OpenTelemetry / Prometheus / vendor). *P6.*
10. **Repo:** continue on `genspark_ai_developer` in the main `realmspace` repo.
