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
| Backend graph / API beyond the bus | ✅ `POST /v1/sessions`, `GET /v1/sessions/{id}[/graph]` (Phase 2 prerequisite) |
| Dashboard wired to the backend | ✅ `NEXT_PUBLIC_BUS_URL` — WS in, `POST /events` out, wizard publishes config |
| Browser-side in-memory bus (live UI fan-out) | ✅ (`dashboard/src/lib/event-bus.ts`) |
| One-command local boot (`docker compose up`) | ✅ Postgres + Neo4j + backend, ~13s cold |
| DB-layer tenant isolation (Postgres RLS) | ✅ event log; ❌ graph (Neo4j Community can't) |

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
- ✅ Harden perception stub: emit into bus, **offline buffer + replay** —
      `perception/bus_client.py` (ported from the `postgres-track`, plus auth and
      the frame dimensions the tracker needs) wired into `realmspace.py` behind
      `--bus-url`. Kill the backend mid-run and events buffer to a local JSONL;
      restart it and they replay oldest-first. **Verified: 11 events posted
      across an outage → 11 rows, 11 distinct ids, in order.** The `event_id` is
      assigned when the event happens, not when it is sent, which is what makes
      the reconnect idempotent rather than duplicating.
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

- ✅ **Prerequisite: session configuration API** — `POST /v1/sessions` +
      `GET /v1/sessions/{id}` + `/graph` (`backend/app/routers/sessions.py`).
      Not on the original Phase 2 list because it belongs to the seam between
      the phases: Phase 1 shipped `upsert_zone` with no caller outside the tests,
      so a real deployment had no zones, the tracker returned early on every
      detection, and nothing downstream had anything to measure. Also carries the
      measurement parameters `roi-framework.md` §5 wants set before the
      activation runs — zone weights, funnel order, engagement threshold,
      activation cost, attribution model. Zone edits invalidate the tracker's
      cache through a new `session.zones_updated` event.
- ✅ **Prerequisite: dashboard ↔ backend wiring** — `NEXT_PUBLIC_BUS_URL` turns on
      a WebSocket feed in (mirrored into the durable local log, `since_seq`
      cursor so a reconnect has no gap), `POST /events` out (buffered in the
      local log across an outage, replayed in order), the wizard publishing to
      `POST /v1/sessions`, and a connection pill. `dashboard/src/lib/bus/`.
      Also the payload translation the two contracts need: the bus pins
      snake_case for the Python producers, this app's contract is camelCase, and
      the mismatch was silent — an untranslated dwell scores as `NaN` plus a
      phantom visitor rather than raising. Tested against a verbatim capture of
      real backend output.
- ✅ Report templated from **real session data** — `/report` renders for every
      session, not only the demo, and every figure traces to an event in the log
      or to a parameter the operator set. The hand-written cover numbers
      (`1,287 visitors`, `4.2×`, four invented recommendations) are gone.
- ✅ **4-layer scorecard** wired to real data — the `DEMO` fallback and the
      hardcoded `activationCost`/`revenueInfluenced` that made the headline ROI
      fiction are deleted. Anything uncomputable renders as `—` **and names what
      is missing**, because "we don't know" and "zero" are different answers.
- ✅ Signature metrics: dwell-weighted attention (per-zone weights from the
      session config), engagement rate (against the configured threshold),
      funnel (in the operator's declared order, never sorted by traffic), CPEV.
      Two definitions corrected against `roi-framework.md` §2 in the process:
      footfall now counts entry-zone crossings rather than anyone entering any
      zone, and returns null when no entry zone is configured; peak concurrency
      is renamed to peak *zone* occupancy, which is what it measures.
      **ROI ratio needs influenced revenue**, which nothing measures until Phase
      4 — it is an optional operator-supplied figure, labelled as the client's
      own on every surface, and blank rather than estimated when absent.
- ✅ **The two missing signals.** Pass-by (Reach) is emitted by the tracker at
      track close-out — one per person per zone, cancelled outright by entering.
      Surface interactions (Engagement) have their whole path built: wizard
      touchpoints become `Surface` nodes, `graph_writer` draws
      `INTERACTED_WITH`, and `trigger_count` survives replay. **No producer was
      invented** — real interactions need booth hardware, so the figure stays a
      marked zero until something POSTs one. Also `session.ended`, without which
      the visitors still in the room at close were never counted at all.
- ✅ **Live ROI tile on the dashboard (day-2 optimisation)** — and the bigger
      thing it exposed: `/live` never read the event bus at all. Every figure
      came from the browser's own webcam tracker or a mock file, so an
      activation fed the way the backend was built for showed nothing. The KPI
      strip now reads the durable log first, the detector second, an honest
      empty third. The tile renders the *same* `Scorecard` the report does, so
      an operator cannot optimise against a number their client will never see.
      Invented deltas (`+2 in last 5m`, `+18 last hour`, `+12% wk`) removed on
      the same grounds as the report's.
- 🟡 **Ask the Room** real: LLM → constrained Cypher (allow-list, validated) →
      graph → answer (replace regex mocks). **Built 2026-08-18 with Phase 5's
      Live Analyst — they are the same endpoint** (`POST /v1/ask`). The regex
      mocks and their invented numbers are deleted, every figure now comes from
      the graph or the log, and `/ask` works for a real activation rather than
      only in demo mode.
      **The allow-list is a catalogue of named, hand-written queries, not
      validated query text** — `docs/adr/003-nl-query-catalogue.md`. The model
      picks a name and fills declared parameters; `tenant_id` and `session_id`
      are injected by the caller and are not parameters any entry declares, so
      the dangerous failure — correct Cypher that silently omits the tenant — is
      unwritable rather than caught.
      🟡 rather than ✅ because open decision 2 is still open: with no provider
      key a deterministic matcher picks the measurement, so the routing is by
      wording rather than by understanding. Every answer says which.
- ✅ Twin plays back **recorded** sessions from the bus — `lib/twin/replay.ts`
      builds paths from the log; positions come from `perception.detection`
      bboxes where they exist and from **zone centres** where they do not, with
      the two rendered and labelled differently because a line between two zone
      centres is not a route anybody walked. Real session bounds replace the
      620-second constant, and the `!isDemo && !detectorRunning` gate is gone —
      it meant a recorded session, the thing the twin exists for, could never be
      replayed. The twin pages `GET /events` itself rather than going through the
      5,000-event local ring buffer, which a real session would overflow.

**Acceptance:** 24h after a session, a client gets a data-true report with the
4-layer scorecard and a stated ROI ratio vs. benchmark; Ask answers a live
question in `< 5s`; twin replays a real recorded session.

---

## Phase 3 — Act: Rules Engine + Staff Prompts · ~1.5 weeks

*Goal: turn signals into real-time action (the "Next-Step" surface), anonymous.*

- ✅ **Event contract additions (additive, before anything else this phase):**
      `rfid.read`, `spatial.tagged`, `intent.scored`, `drift.detected`,
      `calibration.updated`, `crm.retract` — namespaces accepted by the bus,
      payloads pinned in `event-bus-spec.md` §3, mirrored in the browser's
      `RealmEventType` with `crm.retract` classified as PII. Pre-registered so no
      later phase pays a schema-migration tax, and so a producer written in P4 or
      P6 does not meet a 422 that says nothing about why. *(2026-08-11)*
- ✅ **ADR-002: a rule is JSON data, and there is one evaluator**
      (`docs/adr/002-rule-spec.md`). Adopts the spec `floats-agent` already
      ships, verbatim, so the two tracks do not grow two rule languages — and
      records the four places that evaluator must change to be replayable
      (bus-derived window, event-time cooldown, derived `rule.fired` id with
      dispatch idempotency keyed on it, `none` conditions judged at close-out).
      The browser's eight `AgentDefinition`s become presets that compile to the
      same document, ending the split-brain. *(2026-08-11)*
- ✅ **Persist agent rules (Postgres); rules-engine consumer on the bus.** A
      `rules` table under the same forced RLS as the log, `/v1/rules` for an
      operator to author one, and `consumers/rules.py` — a `Consumer` subclass,
      so cursor, backoff and dead-lettering are the ones `/ops` already
      surfaces. Built with ADR-002's four corrections rather than ported: the
      window is a seq-and-time-bounded read of the log (so it survives a
      restart), cooldown compares `occurredAt` (so a replay reproduces the
      original run), `rule.fired` carries a derived id, and a `none` condition
      is judged at a boundary and dated when the silence closed. Two bugs found
      by writing the tests first: a cooldown ordered on the firing's own `seq`
      never applies at all, because a firing is always appended *after* its
      cause — it has to order on the `triggerSeq` it carries. And a threshold
      counts distinct people, not events, or one restless visitor fires a
      five-person rule. *(2026-08-12)*
- ✅ **Real actions: Slack / webhook / screen swap / staff prompt.** A second
      consumer (`consumers/dispatch.py`) reads `rule.fired` and runs one handler
      from `app/actions/`. Idempotency is a `rule_dispatch` row claimed *before*
      the call goes out — Slack is not a database we can put an ON CONFLICT on,
      so the decision moves to one that is. The claim distinguishes three prior
      states: `delivered` refuses (the replay case), `failed` is retaken, and
      `claimed` refuses because a process that died mid-call leaves genuinely
      unknown whether the message arrived. Screen swaps and staff prompts go on
      the bus rather than at the hub, so a tablet that reconnects catches up
      from its cursor. The prompt surface is on `/live`, and it expires its own
      entries: a prompt still shown twenty minutes later is furniture, and it
      teaches the floor to stop reading the panel. *(2026-08-12)*
- ✅ **The browser stops owning rules** (ADR-002's last section). The eight
      `AgentDefinition`s become presets that compile to the document; three of
      them turn out not to be rules at all and now say so instead of being
      padded into the shape. `/agents` reads real rules from `/v1/rules` and
      real firings from `rule.fired` — the hardcoded five agents, `fired: 488`,
      "718 fires today" and "240ms avg latency" are gone on the same grounds the
      report's invented figures were. The browser runs a **dry run** that says
      how often a document would have fired, and dispatches nothing.
      *(2026-08-12)*
- ✅ HITL **dead-letter review** screen — `/ops`, plus `GET /v1/dead-letters`
      with retry and resolve. Retry is offered only for consumers that are pure
      functions of the event (the graph writer); the tracker and broadcast
      decline it *with the reason*, since replaying one event out of sequence
      would give a confident wrong answer or push a stale frame to a live
      screen. Repeat parks collapse onto one row instead of filling the queue.
      **Retries now back off exponentially with a ceiling**, and the chaos test
      the acceptance criterion names is in the suite.
- ✅ **Cost telemetry** meter (`cost.metered` → unit economics) — one emitter
      (`backend/app/cost.py`, deriving the `event_id` from the cause so a replay
      cannot inflate a session's spend), a reader that keeps units apart and
      refuses to total two currencies (`lib/roi/cost.ts`), and a `/live` tile.
      It has a caller now: every dispatched action meters an `action_unit`, so
      the tile fills in as soon as a rule fires. The reading is **1 action**, not
      a price — a default cost per action would be a number invented on a
      client's behalf, which `roi-framework.md` rules out for revenue and this
      refuses for spend. Actions are counted in a unit that is not a currency,
      so they show on their own line and are never added to dollars. Ask's LLM
      calls remain the missing spender, pending a provider key. *(2026-08-12)*

- ✅ **The `< 3s` is measured, not asserted.** `tests/test_phase3_latency.py`
      drives the consumers through their real `run_forever()` loop at the
      configured poll intervals, ingests over `POST /v1/events`, and times from
      the ingest response to the Slack call: **~1.1s** against a local stack.
      The acceptance test alongside it calls `run_once()` three times in
      process order and so had never measured anything — it proves correctness,
      which is a different claim. *(2026-08-13)*
- ✅ **The stranded `claimed` dispatch has a human.** A dispatch whose process
      died between the claim and the outbound call reads `claimed` forever, and
      `claim_dispatch` refuses it — correctly, since whether Slack got the
      message is unknowable from this side. There was no way to ask anybody:
      `get_dispatch` said it was "for `/ops`" and no route read it, so a crash
      mid-call blocked its `(fired_event_id, action_type)` pair permanently.
      `GET /v1/dispatches/stranded` and a verdict endpoint now record what a
      person found — `delivered` closes it, `failed` releases it — and neither
      re-sends, because a retry is the double-post the claim exists to prevent.
      *(2026-08-13)*

**Acceptance:** ✅ "when 5 people dwell at entrance 30s → ping Slack" fires live
in `< 3s` (measured ~1.1s); a forced failure lands in the HITL queue and can be
retried.

---

## Phase 4 — Attribute: Lead Capture + Consent + CRM · ~2–3 weeks

*Goal: bridge anonymous behaviour to identified pipeline, with consent, into any
CRM. This is where "Attribute & Follow up" fully switches on — as new bus
consumers, without touching Phase-1 producers.*

- ✅ **Consent capture** → `POST /v1/consent`, appending `consent.captured` with
      tier, basis and the versioned copy, and `POST /v1/consent/withdraw` for
      `consent.withdrawn`. Payloads pinned in `event-bus-spec.md` §3 and
      mirrored in the browser's contract; both classified PII. The endpoint
      writes to the log and nowhere else — no Contact, no graph — so a kiosk can
      still record a yes when the graph is down, and a deployment can replay its
      whole consent history through a corrected identity consumer without
      re-asking anybody. `consentId` comes from the capture surface, which is
      what stops a kiosk's retry on bad wifi producing two consent records for
      one conversation. The **surfaces** themselves (badge/QR/kiosk hardware)
      are not built. *(2026-08-13)*
- ✅ **Identity consumer**: `consumers/identity.py`, `consent.captured` →
      `(:ConsentEvent)`, `(Person)-[:IDENTIFIED_AS]->(:Contact)`,
      `identity.resolved`. The gate is inside `graph_repo.identify`'s single
      Cypher statement rather than in the consumer, one layer below where
      `consent-and-identity.md` §3 puts it: a check in the consumer is a check a
      second caller can skip, which is the convention the doc is replacing. The
      property this exists for is that **replaying a capture after a withdrawal
      does not re-identify** — the log is append-only, so that capture is on it
      forever. Contact ids derive from the email where there is one, so a
      visitor at two activations is one Contact; from the consent id where there
      is not, because merging people we cannot identify would be inventing a
      fact. *(2026-08-13)*
- ✅ **Re-anonymiser**: `consumers/reanonymise.py`, `consent.withdrawn` → drop
      the link, redact the Contact to a tombstone, stamp the ConsentEvent
      `withdrawn_at`, emit `crm.retract`. The anonymous path survives untouched —
      it was never consent-gated, and deleting it would silently rewrite reports
      already delivered about somebody those reports never named. The consent
      record survives too, because it is the evidence a disputed withdrawal
      would be settled by. *(2026-08-13)*
- ✅ **Attribution consumer**: `consumers/attribution.py` builds
      `LeadHandoff/v1` and emits `handoff.lead` at **two stages** — on
      `identity.resolved` with the path so far, because a trade-show lead is
      worth most while the visitor is still on the floor, and on `session.ended`
      with the complete one. Both carry the same `dedupe_key` so an adapter's
      upsert updates one lead, and different derived `event_id`s so the bus does
      not swallow the second. A withdrawn contact builds nothing, including on a
      replay, because the consumer reads the `IDENTIFIED_AS` edge the
      re-anonymiser deletes. `attribution_window_days` joins the session config
      (30/60/90, default 90). Payload pinned in `event-bus-spec.md` §3, which
      also records where `attention_score` and `lead_score` come from —
      `integrations.md` asked for both and said nothing about their source.
      *(2026-08-13)*
- ✅ **Per-tenant credential store**, which the adapters were blocked on and
      which `multi-tenant.md` §2 has asked for since Phase 1. `tenant_integration`
      (migration 0007, forced RLS) holds one credential per `(tenant, provider)`,
      AES-256-GCM encrypted with the tenant and provider as *associated data* —
      so a row copied between tenants fails to decrypt rather than handing one
      client's token to another's delivery. An unset `credential_encryption_key`
      **refuses to store** rather than writing plaintext; there is no plaintext
      column to fall back to. `PUT/GET/DELETE /v1/integrations/{provider}` and a
      `…/test` healthcheck, all behind a new `require_admin` — `multi-tenant.md`
      §RBAC puts integrations with Admin, and a credential outlives the
      activation an operator runs. The secret is never returned by any endpoint;
      `secretHint` (last 4) answers the only question a reveal would.
      `app/crm/base.py` makes `integrations.md` §3's interface real, with a
      registry in the shape `app/actions` uses. **No dashboard screen** — the
      field-mapping editor is a screen of its own and the half that only holds a
      secret would invite the other half to be improvised. *(2026-08-14)*
- ✅ **HubSpot**, the reference adapter (`integrations.md` §4, open decision 3).
      `consumers/crm_delivery.py` sends every `handoff.lead` to each CRM the
      tenant has connected, claiming under `crm:{provider}` so one lead can go
      to several places and each is claimed, retried and stranded on its own.
      The upsert is HubSpot's own `batch/upsert` with `idProperty: email` — a
      search-then-create has a race the floor would find, and
      `integrations.md` §2 says what that costs. A handoff with no email is
      **declined, not invented**, and closes its claim rather than stranding a
      row no human could resolve. `crm.retract` finally has the reader
      `event-bus-spec.md` §3 always said it had (`consumers/crm_retract.py`):
      it resolves `destination: "all"` through `crm_link` (migration 0008) into
      the destinations that really received the contact, removes the record, and
      redacts the link's `dedupe_key` with the ledger's own function — the row
      stays and stops naming them. Retraction is retryable from `/ops` where
      delivery is not, and it is **not metered**, because a client's unit
      economics should not get worse the more withdrawals they honour. What
      HubSpot's delete actually does — recycling bin, restorable for 90 days —
      is what the dispatch row says it did; a true erasure belongs with the
      erasure job below. *(2026-08-14)*
- ✅ **The rest of the Tier 1 adapters**: Salesforce → Pipedrive → Zoho →
      Dynamics. Each is a module plus a line in the registry, as
      `app/crm/__init__.py` said it would be; three things had to be decided
      first. **A credential is not always one string** — three of the four are
      OAuth2 and need four or five fields, so the secret stays one opaque string
      and theirs is a JSON document, parsed at `PUT` so an admin who pastes a
      HubSpot token into the Salesforce slot is told which fields are missing
      there and then rather than on the first lead of a three-day activation.
      **Three of them have no upsert we can use unaided** — Salesforce and
      Dynamics upsert only against a key the client created in their own org,
      Pipedrive not at all — so `upsert` is now handed the id `crm_link` already
      recorded, and the search-then-create `hubspot.py` argues against is the last
      resort behind it rather than the only thing deciding. **Zoho refuses records
      inside an HTTP 200**, in the `data` array where SUCCESS would be, so every
      call checks the body: a destination reporting success because the transport
      succeeded would put leads on the delivered pile the CRM had refused. The
      error vocabulary, the mapped-field key set and the HTTP client moved out of
      `hubspot.py` and are shared — five copies of the 429/401 mapping is five
      things `/ops` could say about one failure. Tests are transport-stubbed:
      they prove the request we send is the request the vendor documents, not
      that a live account accepts it, and no credentials exist to prove more.
      *(2026-08-17)*
- ✅ **Bring-your-own: signed webhook.** `consumers/handoff_delivery.py` POSTs
      the handoff to `settings.handoff_webhook_url`, signed with `sign()` from
      `app/actions/webhook.py` — the same function the rule action uses, as that
      file asked for. Idempotency is the Phase 3 claim: migration 0006 widened
      `rule_dispatch` with a `kind` column so a stranded lead is legible on
      `/ops` beside a stranded Slack post. No destination configured is not a
      failure — the handoffs are on the log, and a destination added later reads
      them from seq 0. *(2026-08-13)*
- ✅ **Bring-your-own, the rest: Zapier/Make + CSV export + inbound REST.**
      The hooks are providers in the destination registry rather than a parallel
      path — they need a per-tenant credential, a claim, a retry and a row on
      `/ops`, all of which `crm_delivery` and `tenant_integration` already give
      them, and a second delivery path would have its own idea of what a stuck
      delivery looks like. Their `external_id` is our contact id and **not** the
      dedupe key: `crm_link.external_id` is deliberately never redacted, so a
      dedupe key there would leave a live email in our database after an erasure
      had removed every other copy. `GET /v1/handoffs` is the pull half, cursored
      on the log's own `seq` so a client that stops and comes back gets every lead
      since with no gap and no duplicate, with `?format=csv` for the offline
      clients. No scheduler: a job runner emailing a file on Tuesdays would be a
      second place a client's leads leave the building. Withdrawn leads are
      redacted at read time, because a withdrawal does not rewrite the log.
      *(2026-08-17)*
- ✅ **Anonymous handoffs** (`integrations.md` §2 allows a handoff with no
      `contact`). The different trigger is `session.ended` over everyone with no
      live `IDENTIFIED_AS` edge — which makes two cases one, somebody who never
      consented and somebody who consented and then withdrew, because a
      withdrawal returns a person to the anonymous path. The different consent
      story turns out to be no consent at all, and that is the point rather than
      a gap: nothing in the payload names anybody, and it carries the zones and
      dwells `privacy.md` has always had running with no consent. `contact` and
      `consent` are **omitted**, not blanked. **Off unless the operator asks**,
      the opposite of every other session setting, because a busy day is several
      hundred of them reaching the same destinations an identified lead does.
      Two things it broke on the way: `totals.leads` would have quietly changed
      from "people who gave us their details" to "people who walked in", and
      `/ops` would have filled with a dispatch row per visitor per CRM recording
      that nothing was sent. *(2026-08-17)*
- ✅ **Erasure job (tenant-scoped, GDPR Art. 17)** — `POST /v1/erasure` (admin),
      `consumers/erasure.py`, migration 0009. Withdrawal was already doing
      everything `consent-and-identity.md` §5 literally asks for and one thing it
      does not mention: the email is also in `consent.captured`'s contact object
      and in every `handoff.lead` built from it, and an erasure that leaves those
      is not an erasure. So the endpoint appends the ordinary
      `consent.withdrawn` — the existing path runs first, not reimplemented — plus
      `erasure.requested` for the part it cannot reach. **It is the one thing that
      updates an `event_log` row**, and only `payload`, and only where a name is:
      no row deleted, no seq reused, so a replay reproduces the same events with a
      name missing from a few of them. **It refuses until the retraction has
      landed**, because erasing the Contact first would delete the record the
      re-anonymiser reads to build `crm.retract` — an erasure that reports success
      and leaves the data in the client's CRM. And a track id is held as
      `(session_id, anon_id)`, never bare: `P-012` at two activations is two
      people, and erasing the wrong one is data loss that looks like compliance.
      *(2026-08-17)*
- 🔲 (opt, T3) enrichment adapter (Apollo/Clearbit), metered — **the one item
      of Phase 4 deliberately left open.** Marked optional from the start, and it
      needs a provider key that does not exist in this repo, which is the same
      block Ask the Room sits behind (open decision 2). `app/crm/base.py`'s
      interface is the shape an `EnrichmentAdapter` would take, and
      `tenant_integration` already holds the per-tenant key it would need.
- ✅ **The outcome model, which nothing in the repo had.** `data-model.md`'s node
      list stopped at `Frame`, so every attribution claim in
      `roi-framework.md` — the whole of Layer 4 — rested on a thing that did not
      exist. `(:Outcome)` (graph migration 004, tenant-keyed like `Contact`,
      because a deal belongs to the client and not to the activation it started
      at), `outcome.recorded` pinned in `event-bus-spec.md` §3 and classified
      PII, and `POST /v1/outcomes` for an operator to record one — idempotent on
      a caller-supplied id, because a duplicated outcome doubles the numerator of
      the ROI ratio a client is shown. *(2026-08-13)*
- ✅ **Attribution ledger + CFO one-pager.** `GET /v1/ledger/{session}`, with
      `?format=csv` because "exportable" is half of what `roi-framework.md` §4
      asks for, and a `/ledger` page that is the one-pager and the evidence
      under it at two zoom levels. Built from the **log, not the graph**: an
      auditor asks what was known and when, and the graph is current state — a
      withdrawal redacts the Contact, so a graph-built ledger could not show the
      touch ever happened. Which obliges it to redact at read time, since the log
      keeps the name forever: a withdrawn lead keeps its row, its timestamps and
      its consent basis, and loses its name. The window is inclusive at the
      boundary, out-of-window deals stay listed and uncounted, and `linear` /
      `time_decay` **refuse with the reason** rather than inventing a share
      across a journey this system does not observe. `revenue_influenced` is now
      measured, with the operator's stated figure shown beside it rather than
      replaced. *(2026-08-13)*

**Acceptance:** ✅ a consented badge scan produces a Contact linked to its
spatial path, a LeadHandoff lands in HubSpot with spatial_intent fields, a
withdrawal retracts it, and the attribution ledger reconciles booth-touch →
outcome. All four clauses held from 2026-08-14; what landed after that is the
width the phase promised — four more Tier 1 CRMs, the remaining bring-your-own
destinations, the anonymous handoff and the erasure job.

**One caveat on the CRMs, stated rather than left to be discovered.** No live
account exists for any of the five, so each adapter is proven against the API its
vendor documents and against nothing else. The first real portal will find
something; what it should not find is a duplicated lead or a withdrawal that did
not go, which is what the tests are pointed at.

---

## Phase 5 — Follow-up automation + Intelligence · ~2 weeks

*Goal: close the loop with contextual follow-up and smarter agents.*

- 🟡 **Contextual SDR** draft agent (path-aware follow-up emails, consent ≥ T2) —
      `consumers/sdr.py`. Reads the `final` handoff, checks consent, writes
      `followup.drafted`; `/followups` is the review list. **It drafts and does
      not send**, which is what the bullet says and what the absence of an email
      provider forces: sending wants its own consent question, a suppression
      list, a bounce story and an audit of who pressed the button, and half of
      that behind a button labelled Send would be worse than none. The payload
      carries `grounded_in` — the exact zones, surfaces and dwell the draft was
      allowed to reference — so a reviewer checks a sentence against the
      measurements rather than trusting it. 🟡 for the provider, as above: with
      no key the draft is composed rather than written.
- 🟡 **Live Analyst agent** (NL queries over the live bus) — `POST /v1/ask`, and
      the same endpoint that closes Phase 2's Ask the Room. Questions the graph
      cannot answer ("when was it busiest") are served from the log, which is
      what "over the live bus" means. See ADR-003 above.
- 🔲 **Floor orchestrator** (predict density, staff allocation) — if demand
      proven. **Deferred deliberately, with two preconditions unmet** *(checked
      2026-08-18, so the next person to ask finds the answer here)*:
      1. *Its own stated gate.* No pilot has asked for it.
      2. *A prediction model.* It appears nowhere in either track except this
         line — no data model, no event type, no acceptance criteria — and the
         only other mention anywhere is a research note on `floats-agent`:
         "Prediction models (ObjectForesight) are a P5 floor-orchestrator
         input". So it always presumed a trajectory-prediction model this repo
         does not have and has not chosen. That is a second open decision, and
         nobody had written it down.

      `floats-agent` has not built it either — the identical 🔲 line, no code.
      Building it now would mean inventing the specification and then writing
      the acceptance criteria to test against it. The measurable half (live
      occupancy against a configured capacity, prompting through the Phase 3
      rules engine) is buildable today and is *measurement, not prediction*; if
      demand arrives, that is where to start, and it belongs to whoever asks.
- 🟡 **LLM insight generation every N minutes → `insight.generated`** —
      `consumers/insights.py`, `app/llm/digest.py`, `/live`.
      **Built to `floats-agent`'s specification, adopted verbatim** the way
      ADR-002 adopted their rule spec, so the two tracks do not grow two insight
      contracts: *"periodic bounded graph snapshot → `insight.generated` with
      text + supporting event IDs; shown on `/live`; click-through opens the
      underlying events"*, plus their token-bounded digest.
      Their acceptance clause — *"insights on `/live` trace to source events"* —
      is what shaped it. The digest is computed from the **log**, not the graph,
      because the graph can tell you a zone's average dwell and can never tell
      you which events say so. Every claim carries the ids behind it, the panel
      opens them, and a ref that no longer resolves is shown rather than dropped.
      Triggered on **event time** with fixed contiguous windows, so a replay
      reproduces the same insights; writing it walked into ADR-002's own trap
      (an insight is always appended after the events it summarises, so a
      `before_seq` lookup can never see one) and the fix is recorded in the
      consumer. Anonymous by construction — the digest reads only `spatial.*`
      and `surface.interaction`. 🟡 for the provider, as above: with no key the
      sentence is composed from the measurements rather than written.

**Also landed here, because both new consumers needed it:** the **T2 gate**
(`app/consent_tier.py`). `consent-and-identity.md` §3 has said since Phase 4 that
"CRM sync consumers refuse to emit a contact without a ≥T2 consent … enforced in
code (the bus consumer), not by convention", and nothing read `tier`. It does
now, in one place, used by the CRM delivery and the SDR both.

**Acceptance:** 🟡 post-session, a consented lead receives a draft follow-up
referencing the exact zones/surfaces they engaged, insights on `/live` trace to
source events (the clause adopted from `floats-agent`), and the analyst answers
live NL queries — **verified end to end on 2026-08-18**: a T2 visitor's draft named the zone they dwelled longest in and the
surface they touched, and the T1 visitor beside them got none. The analyst
answers live NL queries against measured data, checked question by question
against the graph.

The insight half was verified the same way: two contiguous five-minute windows
summarised from a seeded floor, and the first insight's four citations opened to
exactly the Product Pod events whose dwells sum to the 300 seconds it claimed —
not the window's other traffic.

**Phase 5 is otherwise complete.** Every item is built or deliberately deferred
with its reasons recorded; nothing is left merely unstarted.

What is not met is the phrase "AI" in the phase title. With open decision 2 open,
routing is by wording and the draft is composed from a template. The seam is
built and tested — a provider is one adapter module and one `PUT` — but until a
key exists this phase is 🟡, and claiming otherwise would be the kind of thing
this codebase deletes from reports.

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
| Latency SLA (< 3s) | ✅ measured at **~1.1s** detection → Slack, through the consumers' real poll loop (`tests/test_phase3_latency.py`). Local stack, one process: what it covers is the number of poll intervals a firing waits through, which is the part that regressed silently before. Network to a real webhook and a smaller edge box are on top. |
| CV model drift | calibration UI + drift telemetry — P6 |
| Failure states (CRM 429, consent revoked) | dead-letter + HITL, withdrawal flow — P3/P4 |
| Attribution decay (30/60/90d) | window on outcome edge — P4 |
| Multi-booth / cross-event aggregation | multi-tenant benchmark — P6 |
| Cost telemetry / unit economics | `cost.metered` meter — P3 |
| Group visits | `Group` node (in `data-model.md`) — P1/P2 |
| Negative signals (pass-by/skip) | ✅ `spatial.passby` — tracker emits at close-out for a zone approached within `tracker_passby_radius` and never entered |
| Session hygiene (boundary flicker, track dropout, sub-second dwells) | ✅ confirm window + dropout sweep + minimum dwell in `consumers/tracker.py`, ported from the postgres-track's `spatial-deriver.ts` (CHI '26: 71% of raw sessions invalid without them) |

---

## Open technical decisions (to confirm as we build)

1. **Graph store:** Neo4j (matches docs) vs. embedded SQLite/DuckDB graph for
   the edge box (lighter, offline-friendly). *Lean: decide at start of P1.*
2. **AI provider:** OpenAI / Anthropic / Gemini for Ask + SDR. *Still open as of
   2026-08-18, and now the only thing Phase 5 is waiting on.* No key exists in
   the repo.
   **Narrowed 2026-08-18:** it no longer blocks anything but the model itself.
   The provider seam (`app/llm/`), the per-tenant key storage (migration 0010's
   `kind`), the query catalogue, the prompts, the token metering, the consent
   gate and both surfaces are built and tested against a deterministic provider
   that answers from real data and says that it did. Choosing a vendor is now one
   adapter module — `crm/hubspot.py` is the worked example, and
   `docs/adr/003-nl-query-catalogue.md` lists what the adapter owes.
   Ask's regex mocks are deleted either way: they returned invented numbers, and
   waiting for a provider was never a reason to keep those.
3. **First CRM confirmed:** HubSpot as reference adapter (P4). *Closed
   2026-08-17:* all five Tier 1 adapters plus the Zapier/Make hooks are built and
   registered. What is still open is not a decision but an absence — no live
   account exists for any of them, so every adapter is proven against the API its
   vendor documents and none against a real portal.
4. **Repo:** continue on `genspark_ai_developer` in the main `realmspace` repo.
