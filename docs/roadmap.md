# realmspace — Roadmap

> Sequenced by **client value** (see `VISION.md` §4), not by technical layer.
> Governing rule: **prove one vertical slice end-to-end before widening.**
> The existing prototype stays the center of gravity — we harden and extend it.

Legend: ✅ exists today · 🟡 mocked/partial · 🔲 to build.

---

## Phase 0 — Foundations already in place (audit)

| Capability | State |
|---|---|
| Next.js dashboard: landing, live, twin, ask, agents, report | ✅ |
| **Live on-device tracking** (TF.js + COCO-SSD + centroid tracker) | ✅ (real) |
| Python perception stub (YOLO + ByteTrack → JSON) | 🟡 stub |
| Docs: PRD, architecture, data-model, privacy, gtm | ✅ |
| Docs: vision, brand, roi, integrations, consent, event-bus, tenancy, competition | ✅ (this set) |
| Backend event bus / graph / API | 🟡 FastAPI + SQLite bus/graph (`backend/`); Postgres schema ready |


**Also parallel (founder-led, no code):** run the `gtm.md` validation sprint —
90s Loom + landing + 50 cold emails. **Pass = 5 booked demos in 7 days.**

---

## Phase 1 — The Spine (make the backend real) · ~2 weeks · ✅ DONE 2026-07-27

*Goal: replace the mocked/in-memory backend with a real, replayable event bus +
graph, wired to the existing dashboard. Multi-tenant from the first commit.*

- ✅ FastAPI service + **append-only event bus** (`event-bus-spec.md`) — SQLite locally; Postgres schema ready (`backend/`, `docker-compose.yml`)
- ✅ Graph store + schema from `data-model.md`; `tenant_id` everywhere
      (`multi-tenant.md`, `docs/adr/001-graph-store.md` — relational projection)
- ✅ Consumers: graph writer (edge, near-real-time); tracker still browser/YOLO-side
- ✅ WebSocket: bus → dashboard (bridge via `NEXT_PUBLIC_BUS_URL`); perception POSTs into bus
- ✅ Perception stub hardened: emits into bus (`--bus-url`) with **offline buffer +
      ordered replay** (`--buffer-file`) when the API is unreachable
- ✅ `/live` KPIs read from the durable bus/graph when the camera is idle — unique
      visitors, recorded session span, and the recorded traffic curve are no longer
      mock-derived (`useSessionBusStats`)
- ✅ Auth resolves user → org → role (RBAC skeleton — `GET /v1/auth/resolve`)

**Acceptance:** real camera → real event in Postgres log → real graph node → real
`/live` KPI, tenant-scoped, works offline then replays on reconnect. `< 500ms`
detection → dashboard. *(Met on the SQLite edge default: offline buffer + ordered
replay verified; `/live` KPIs bus-derived; full Postgres swap is one
`docker compose up` away and remains a deploy-time choice.)*

---

## Phase 2 — Prove ROI (the #1 client job) · ~2 weeks

*Goal: turn the report into a real, defensible ROI proof. This is the wedge.*

- 🔲 Report templated from **real session data** (replace static numbers)
- 🔲 **4-layer scorecard** (Reach/Engagement/Affinity/Pipeline) — `roi-framework.md`
- 🔲 Signature metrics: dwell-weighted attention, engagement rate, funnel, CPEV
- 🔲 Live ROI tile on the dashboard (day-2 optimisation)
- 🔲 **Ask the Room** real: LLM → constrained Cypher (allow-list, validated) →
      graph → answer (replace regex mocks). *Needs AI provider key.*
- 🔲 Twin plays back **recorded** sessions from the bus (replace seed paths)

**Acceptance:** 24h after a session, a client gets a data-true report with the
4-layer scorecard and a stated ROI ratio vs. benchmark; Ask answers a live
question in `< 5s`; twin replays a real recorded session.

---

## Phase 3 — Act: Rules Engine + Staff Prompts · ~1.5 weeks

*Goal: turn signals into real-time action (the "Next-Step" surface), anonymous.*

- 🔲 Persist agent rules (Postgres); rules-engine consumer on the bus
- 🔲 Real actions: Slack / webhook / screen swap / **staff prompt** (< 3s SLA)
- 🔲 HITL **dead-letter review** screen for failed actions
- 🔲 **Cost telemetry** meter (`cost.metered` → unit economics)

**Acceptance:** "when 5 people dwell at entrance 30s → ping Slack" fires live in
`< 3s`; a forced failure lands in the HITL queue and can be retried.

---

## Phase 4 — Attribute: Lead Capture + Consent + CRM · ~2–3 weeks

*Goal: bridge anonymous behaviour to identified pipeline, with consent, into any
CRM. This is where "Attribute & Follow up" fully switches on — as new bus
consumers, without touching Phase-1 producers.*

- 🔲 **Consent capture** surfaces (badge/QR/kiosk/form) → `consent.captured`
      (`consent-and-identity.md`) with versioned copy + tiers/redlines
- 🔲 **Identity consumer**: consent-gated anon → Contact link (anon+PII modes)
- 🔲 **Attribution consumer**: build normalized **LeadHandoff**, apply
      model + window (`integrations.md`, `roi-framework.md`)
- 🔲 **CRM adapters** in order: HubSpot → Salesforce → Pipedrive → Zoho →
      Dynamics; each idempotent, retract-capable
- 🔲 **Bring-your-own**: signed webhook + Zapier/Make + CSV export
- 🔲 Withdrawal / erasure flows (re-anonymise, `crm.retract`)
- 🔲 (opt, T3) enrichment adapter (Apollo/Clearbit), metered
- 🔲 **Attribution ledger** + **CFO one-pager** (`roi-framework.md`)

**Acceptance:** a consented badge scan produces a Contact linked to its spatial
path, a LeadHandoff lands in HubSpot with spatial_intent fields, a withdrawal
retracts it, and the attribution ledger reconciles booth-touch → outcome.

---

## Phase 5 — Follow-up automation + Intelligence · ~2 weeks

*Goal: close the loop with contextual follow-up and smarter agents.*

- 🔲 **Contextual SDR** draft agent (path-aware follow-up emails, consent ≥ T2)
- 🔲 Live Analyst agent (NL queries over the live bus)
- 🔲 Floor orchestrator (predict density, staff allocation) — if demand proven
- 🔲 LLM insight generation every N minutes → `insight.generated`

**Acceptance:** post-session, a consented lead receives a draft follow-up
referencing the exact zones/surfaces they engaged; analyst answers live NL queries.

---

## Phase 6 — Platform hardening (public product) · ~2 weeks

*Goal: the things required to sell to the public at scale.*

- 🔲 **Multi-tenant** isolation hardened + RBAC complete (`multi-tenant.md`)
- 🔲 **Cloud replay sync** (anonymised) + **cross-activation benchmark** dataset
- 🔲 **Billing** hooks (Stripe) + plan metering/limits
- 🔲 Calibration UI + **CV drift telemetry**; multi-camera fusion
- 🔲 **Brand re-token** UI to the brand book (Sora/Inter/IBM Plex Mono, orange
      `#FF5C00` / teal `#00D4AA` / base `#0A0B10`) — `brand.md`
- 🔲 Deploy pipeline, exports, onboarding polish

**Acceptance:** a third-party operator self-serves signup → activation → report,
fully isolated, billed, on-brand.

---

## Timeline (indicative)

```
Week:              1   2   3   4   5   6   7   8   9  10  11  12
P1 Spine         ██████████
P2 Prove ROI              ██████████
P3 Act                            ███████
P4 Attribute/CRM                       ██████████████
P5 Follow-up                                       ██████████
P6 Platform                                                ██████████
GTM sprint       ████ (parallel, founder-led)
1st paid pilot                    ★ embedded (Pavilion tier)
CRM attribution live                            ★
```

---

## Blind spots → living engineering tickets

From the founder architecture dump; each is designed-for, not hoped-for:

| Blind spot | Where it's handled |
|---|---|
| Offline / edge reliability | `event-bus-spec.md` §5 (local log + replay) — P1 |
| Latency SLA (< 3s) | edge-run rules engine — P3 |
| CV model drift | calibration UI + drift telemetry — P6 |
| Failure states (CRM 429, consent revoked) | dead-letter + HITL, withdrawal flow — P3/P4 |
| Attribution decay (30/60/90d) | window on outcome edge — P4 |
| Multi-booth / cross-event aggregation | multi-tenant benchmark — P6 |
| Cost telemetry / unit economics | `cost.metered` meter — P3 |
| Group visits | `Group` node (in `data-model.md`) — P1/P2 |
| Negative signals (pass-by/skip) | `spatial.passby` event — P2 |

---

## Open technical decisions (to confirm as we build)

1. **Graph store:** ✅ **DECIDED** — relational `graph_nodes` / `graph_edges` in the
   same SQL DB as the event bus (SQLite edge default; Postgres when available).
   Neo4j / Apache AGE deferred until Ask needs native Cypher.
   See `docs/adr/001-graph-store.md`.
2. **AI provider:** OpenAI / Anthropic / Gemini for Ask + SDR. *Needed for P2.*
3. **First CRM confirmed:** HubSpot as reference adapter (P4).
4. **Repo:** continue on `genspark_ai_developer` in the main `realmspace` repo.
