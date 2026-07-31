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
| Backend event bus (durable, Postgres) | ✅ (`backend/` — see Phase 1) |
| Backend graph / API beyond the bus | 🔲 |
| Browser-side in-memory bus (live UI fan-out) | ✅ (`dashboard/src/lib/event-bus.ts`) |

**Also parallel (founder-led, no code):** run the `gtm.md` validation sprint —
90s Loom + landing + 50 cold emails. **Pass = 5 booked demos in 7 days.**

---

## Phase 1 — The Spine (make the backend real) · ~2 weeks

*Goal: replace the mocked/in-memory backend with a real, replayable event bus +
graph, wired to the existing dashboard. Multi-tenant from the first commit.*

- ✅ FastAPI service + Postgres **append-only event bus** (`event-bus-spec.md`)
      — `backend/`; schema per §2, idempotent on `event_id`, cursor reads,
      tested against real Postgres. Nothing produces into it or consumes from
      it yet; `consumer_cursor` and `dead_letter` exist but are still inert.
- ✅ Graph store + schema from `data-model.md`; `tenant_id` everywhere
      (`multi-tenant.md`) — Neo4j (open decision #1 resolved in favour of the
      docs); `backend/app/graph/`. Schema covers 8 of the 9 node types, every
      key starting with `tenant_id`; migration runner; all Cypher confined to
      one repository module. `Frame` is unconstrained because `data-model.md`
      gives it no id to key on.
      **Caveat:** Community edition cannot enforce tenant isolation at the DB
      layer, so it is application-enforced — see `data-model.md` → "Store
      decision".
      Write functions exist for `Person`/`Zone`/`Session` and the `ENTERED` /
      `DWELLED_IN` edges — the subset the next item needs first. The remaining
      upserts and relationships land with the consumers that call them, rather
      than as code nothing exercises.
- ✅ Consumers: tracker, graph writer (edge, real-time) — `backend/app/consumers/`.
      Both run as tasks in the API process and are reported by `/health`.
      Tracker emits `spatial.zone_enter` / `zone_exit` / `dwell`;
      `gaze` / `group` / `passby` are not built (gaze needs pose data perception
      doesn't emit; passby is P2 per the blind-spots table below).
      Replay is a proven no-op — derived event ids plus `on_replay` state
      clearing, both verified by deliberately breaking them.
- ✅ WebSocket: bus → dashboard `/live` — `WS /v1/ws/{tenant_id}/{session_id}`,
      path and envelope matching the other track so POD 3 integrates once.
      `since_seq` is a real cursor, so a reconnect has no gap and no duplicates.
      **Measured 58ms median / 175ms worst** against the <500ms target.
      Swapping `/live` over to it is POD 3's job; perception → bus is the next
      item, so the feed carries HTTP-posted events until then.
- 🔲 Harden perception stub: emit into bus, **offline buffer + replay**
- ✅ Auth resolves user → org → role (RBAC skeleton) — `backend/app/auth/`.
      JWT for people, API keys for devices (a camera cannot log in), both signed
      and verified locally so the edge box still authenticates with no network.
      **`tenant_id` is now derived from the credential, not supplied**: the
      query parameter is gone, cross-tenant writes are refused, and the socket
      checks the path against the token. Week 1 1.6's outcome — "cross-tenant
      read fails" — holds at the application layer.
      **Not yet:** Postgres row-level security (1.6's stated mechanism, next
      item — and it can only cover the Postgres half, since Neo4j Community has
      no equivalent), and `POST /v1/auth/token` still trusts the email it is
      given rather than verifying a Firebase login.

**Acceptance:** real camera → real event in Postgres log → real graph node → real
`/live` KPI, tenant-scoped, works offline then replays on reconnect. `< 500ms`
detection → dashboard.

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

1. **Graph store:** Neo4j (matches docs) vs. embedded SQLite/DuckDB graph for
   the edge box (lighter, offline-friendly). *Lean: decide at start of P1.*
2. **AI provider:** OpenAI / Anthropic / Gemini for Ask + SDR. *Needed for P2.*
3. **First CRM confirmed:** HubSpot as reference adapter (P4).
4. **Repo:** continue on `genspark_ai_developer` in the main `realmspace` repo.
