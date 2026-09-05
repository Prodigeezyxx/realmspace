# realmspace — Integrations (CRM + Bring-Your-Own)

> Scope: **all top CRMs + a generic bring-your-own connector.** realmspace is a
> public product; we cannot assume the customer's stack. Therefore we build a
> **connector abstraction** with a single normalized handoff contract, never
> hardcoded per-vendor logic in the core.

---

## 1. Design principle: one contract, many adapters

The core system knows nothing about HubSpot or Salesforce. It emits a single,
normalized **Lead Handoff** object onto the event bus. Adapters translate that
object into each destination's API. Adding a CRM = adding an adapter; the core,
the dashboard, and the rules engine never change.

```
                       ┌─────────── normalized LeadHandoff ───────────┐
 realmspace core  ──▶  │  {contact, activation, spatial_intent,        │
 (bus consumer:        │   consent, roi_context, dedupe_key}           │
  "handoff")           └───────────────────────┬──────────────────────┘
                                                │
              ┌──────────────┬──────────────┬───┴────────┬──────────────┬───────────────┐
              ▼              ▼              ▼            ▼              ▼               ▼
          HubSpot       Salesforce      Pipedrive      Zoho        MS Dynamics    BYO (webhook/
          adapter        adapter         adapter      adapter        adapter       CSV/Zapier/API)
```

---

## 2. The normalized LeadHandoff contract

Every adapter receives this shape. It is versioned and additive-only.

```jsonc
{
  "schema": "realmspace.lead_handoff/v1",
  "tenant_id": "t_...",
  "activation": {
    "id": "sess_...", "name": "Pavilion No.7",
    "venue": "Eko Convention Centre", "city": "Lagos",
    "started_at": "...", "ends_at": "..."
  },
  "contact": {                       // present only in PII mode (consent ≥ T1)
    "email": "...", "name": "...", "company": "...", "title": "...",
    "source": "badge|qr|kiosk|form|manual"
  },
  "spatial_intent": {                // the realmspace differentiator
    "zones_visited": ["Entry","Mirror Room","Product Pod A"],
    "top_dwell_zone": "Product Pod A",
    "dwell_seconds_total": 214,
    "surfaces_engaged": ["AR Mirror","Bottle Wall"],
    "attention_score": 268.0,        // Σ(dwell × zone weight), in SECONDS
    "attention_basis": "weighted_dwell_seconds",
    "lead_score": 74,                // or null; see below. Never 0 when unknown
    "lead_score_basis": "spatial/v1",
    "path_summary": "Entered, 3m at AR Mirror, dwelled Product Pod A 2m"
  },
  "consent": {                       // required for any PII emission
    "tier": "T2", "basis": "explicit_optin",
    "copy_version": "consent-2026-06", "captured_at": "..."
  },
  "roi_context": {
    "attribution_model": "influenced",
    "attribution_window_days": 90,
    "activation_cost_share": 120.00
  },
  "dedupe_key": "tenant:email|anon_id",   // idempotency across retries
  "emitted_at": "...", "event_seq": 84213
}
```

**Anonymous handoffs** (no `contact`) are still valid — they carry spatial_intent
for aggregate ROI and can be attached to a lead later if consent arrives. ✅
*built 2026-08-17*, with the different trigger this paragraph asked for: every
person rather than every consent, at `session.ended`, over everyone with no live
`IDENTIFIED_AS` edge. That covers somebody who never consented and somebody who
consented and then withdrew, because a withdrawal returns a person to the
anonymous path.

The different consent story turns out to be no consent at all, and that is the
point rather than a gap: there is no `contact`, no `consent` and nothing in the
payload that names anybody, only the zones and dwells `privacy.md` has always had
running with no consent. **Off unless the operator asks** (`anonymousHandoffs` on
the session config) — a busy day is several hundred of them. They go to the
deployment webhook, the pull API and any bring-your-own hook the tenant has
connected; the CRM adapters decline them, because there is no record to create
for somebody who was never named. Which destination takes one is the adapter's
own `capabilities()["anonymous"]`. Pinned in `event-bus-spec.md` §3.

### Where the two computed fields come from *(added 2026-08-13)*

The shape above says what a handoff contains; these two say nothing about where
the number comes from, and both were filled in when the attribution consumer was
built. Pinned in `event-bus-spec.md` §3, summarised here:

- **`attention_score` is in seconds, not a 0–1 ratio.** The `0.82` above was
  illustrative and misleading. `roi-framework.md` §2 defines dwell-weighted
  attention as `Σ(dwell × weight)`, which is a quantity, and nothing in the
  framework provides a denominator to normalise it. `attention_basis` ships
  beside it so a destination mapping it into a CRM field knows what it holds.
- **`lead_score` is a stated formula, versioned as `spatial/v1`.** Three ratios
  of what the visitor did to what the activation offered — dwell against the
  engagement threshold, funnel depth against the deepest configured zone,
  surfaces used against surfaces present — weighted 0.5/0.3/0.2. Denominators
  are the operator's own configuration, never constants: a booth with one
  product pod and a booth with six must not be scored against the same idea of
  "engaged". A component nobody configured is dropped and the rest
  renormalised; with nothing computable the score is `null`, never `0`.

**Both stages of one lead share a `dedupe_key`.** A handoff goes out when the
visitor consents and again when the session ends, so an adapter's `upsert` must
genuinely be an upsert — a create-only implementation will duplicate every lead
in the client's CRM.

---

## 3. Adapter responsibilities (the interface every connector implements)

```
Adapter interface:
  - authenticate(tenant_credentials)        # OAuth2 / API key, per tenant
  - map(handoff) -> destination_payload     # field mapping (configurable)
  - upsert(payload) -> external_id          # create/update, dedupe-aware
  - retract(external_id)                     # consent withdrawal / erasure
  - healthcheck() -> status
  - capabilities() -> {custom_fields, retract, activities, ...}
```

- **Idempotent upsert** keyed on `dedupe_key` → safe on bus replay; no dup leads.
- **Field mapping is per-tenant config**, not code — customers map
  `spatial_intent.lead_score` → their CRM's custom field in the UI.
- **Capabilities()** lets the UI hide features a CRM can't do (e.g. no retract).

### As built *(added 2026-08-14)*

`backend/app/crm/base.py`. Three things this section left open, decided:

- **`authenticate` is construction** (`CrmAdapter.for_tenant`), not a method you
  call afterwards. The alternative leaves a window in which an unauthenticated
  adapter is a live object somebody can call `upsert` on, and that failure
  surfaces as a 401 from the client's CRM rather than as a mistake caught here.
- **`upsert` may decline.** §2 allows an anonymous handoff and
  `consumers/attribution.py` builds `dedupe_key` as `tenant:email|anon_id`, so a
  handoff with no email is ordinary and reaches an adapter with nothing to key
  on. `None` means "nothing here to send"; raising would park a lead on `/ops`
  that no human action could resolve.
- **Failure is raised, never returned** — §8's 429s included.
  `consumers/base.Consumer` already owns retry, backoff and dead-lettering, and
  an adapter that swallowed its own rate limit would be a second policy
  disagreeing with the first.

### Two more, decided by the adapters after the first *(added 2026-08-17)*

- **`parse_secret` — a credential is not always one string.** HubSpot's is a
  bearer token; Salesforce, Zoho and Dynamics are OAuth2 and need four or five
  fields. Rather than widen `tenant_integration` and redo `secrets.py`'s binding
  of a ciphertext to its `(tenant, provider)`, the secret stays one opaque string
  and an OAuth adapter's string is a JSON document. `PUT` parses it *before*
  encrypting, so an admin pasting a HubSpot token into the Salesforce slot is
  told which fields are missing there and then — the alternative stores cleanly,
  tests green if nobody presses test, and fails on the first lead of a three-day
  activation.
- **`upsert(payload, external_id=…)` — the id we recorded last time.** Only
  HubSpot and Zoho have an upsert we can use unaided. Salesforce and Dynamics
  upsert against a key the client had to create in their own org, and Pipedrive
  has none at all. `crm_link` (migration 0008) already holds the CRM's own id
  from the last push, so the delivery consumer passes it in and the search is the
  last resort behind it rather than the only thing deciding.

Credentials come from `tenant_integration` (migration 0007), encrypted per
tenant per `multi-tenant.md` §2 and opened in exactly one function,
`crm.adapter_for`.

**Not every credential in that table is a destination** *(migration 0010)*.
Phase 5 stores an AI provider key in the same table, and `crm_delivery` fans a
lead out over every active row a tenant has — so without a discriminator an
Anthropic key would be enrolled as a place to send somebody's contact details.
`kind` is `crm` or `llm`, the delivery consumer asks for `crm` explicitly, and
the two registries are separate (`app/crm` and `app/llm`). `PUT /v1/integrations/{provider}` stores one; it is
admin-only, refuses a provider this build has no adapter for, and never returns
the secret back.

---

## 4. Tier 1 adapters (build order, by market share + client demand)

1. **HubSpot** — easiest API, common in mid-market; first adapter (reference
   impl). ✅ *built 2026-08-14* — `backend/app/crm/hubspot.py`. Upsert is
   HubSpot's own `POST /crm/objects/{version}/contacts/batch/upsert` with
   `idProperty: "email"`, not a search-then-create: two handoff stages in flight
   together would both search, both miss and both create, which is the
   duplication §2 warns about with extra steps. The API version is pinned rather
   than tracking "latest", for the reason `lead_score_basis` carries a version —
   a client's CRM outlives our assumptions about the API that wrote into it.
   **Retract is HubSpot's delete, which is a recycling bin restorable for 90
   days.** That is a removal, not an erasure, and the dispatch row says which; a
   true erasure needs account-level GDPR features this adapter does not assume,
   and belongs with the erasure job where the same question has to be answered
   for every destination at once.
2. **Salesforce** — enterprise default; highest ASP deals need it. ✅ *built
   2026-08-17*. OAuth2 refresh token. The upsert is
   `PATCH /sobjects/Lead/{externalIdField}/{dedupe_key}`, which only works
   against a field the org marked as an External ID — there is no stock one, so
   the client creates it and names it in the credential, and `healthcheck` checks
   it exists rather than letting the first lead of an activation discover it. The
   object is `Lead`, not `Contact`: a Contact belongs to an Account, and creating
   accounts for booth visitors would put fictitious companies in a client's CRM.
   An update answers 204 with no body, so the id is fetched — a `crm_link` with no
   `external_id` is a withdrawal that cannot be carried out. Retract is a delete
   into the Recycle Bin, restorable for 15 days, and the dispatch row says so.
3. **Pipedrive** — SMB/agency favourite. ✅ *built 2026-08-17*. API token, and the
   only adapter here with no upsert at all. What stands in for it is the id from
   `crm_link` (§3), with a search by email behind that on a genuine first push —
   which is the right answer anyway, since creating a second Person for an email
   their CRM already knows is the duplication §2 warns about. The residual window
   is both stages of a brand-new contact in flight at the same instant; it is
   stated in the adapter rather than hidden, because the recovery is a client
   merging two Persons. No Organization is created for `company`: inventing one
   per booth visitor would litter a CRM with companies nobody agreed to.
4. **Zoho CRM** — strong in our SAM (Africa + emerging markets). ✅ *built
   2026-08-17*. OAuth2 refresh token, and a real upsert
   (`POST /crm/v6/Leads/upsert`, `duplicate_check_fields: ["Email"]`). Two Zoho
   specifics: the **datacentre is part of the credential**, because `.com`, `.eu`,
   `.in` and `.com.au` are separate accounts and the wrong one authenticates as a
   bad token — which sends an admin off to rotate a credential that was fine; and
   a **refused record arrives inside an HTTP 200**, in the `data` array with the
   code where SUCCESS would be, so every call checks the body and raises. A
   destination that reported success because the transport succeeded would put
   leads on the delivered pile that Zoho had refused.
5. **Microsoft Dynamics 365** — enterprise/regulated. ✅ *built 2026-08-17*. OAuth2
   client credentials against the client's Azure AD — an application user, not a
   person's session, which is the shape enterprises actually approve. The upsert
   is OData against an alternate key on `emailaddress1`, registered in the org;
   `healthcheck` checks for it, same as Salesforce's External ID field. Retract is
   a real delete with no recycle bin, and the dispatch row says that too.

**Salesforce, Zoho and Dynamics all refuse a lead with no surname and no
company.** The answer is a placeholder that says nobody gave us one, never a
surname derived from an email address — that would be a guess wearing a real
name's clothes, and the CRM would never show it as one
(`backend/app/crm/lead_fields.py`).

## 5. Bring-Your-Own connectors (no adapter needed)

For any stack we don't natively support, or customers who want control:

- **Generic Webhook** — POST the `LeadHandoff` JSON to a customer URL (HMAC-signed).
  ✅ `consumers/handoff_delivery.py`. A **deployment** setting: one endpoint for
  the whole install.
- **Zapier / Make** — publish to their webhook so non-technical users route it
  anywhere. ✅ *built 2026-08-17*, `app/crm/zapier.py`, as providers in the
  destination registry rather than a parallel path. They need a per-tenant
  credential, a claim, a retry and a row on `/ops` — all of which `crm_delivery`
  and `tenant_integration` already provide, and a second delivery path would have
  its own idea of what a stuck delivery looks like. The credential is the hook
  URL (https only); the body is signed with the same `sign()` the rule action
  uses, over the exact bytes sent. `external_id` is our contact id and **not** the
  dedupe key, because `crm_link.external_id` is deliberately never redacted and a
  dedupe key there would leave a live email in our database after an erasure.
- **CSV / scheduled export** — for offline or air-gapped clients. ✅
  `GET /v1/handoffs?format=csv`. The scheduled half is a cron calling it; there is
  deliberately no scheduler, because a job runner emailing a file on Tuesdays
  would be a second place a client's leads leave the building with its own retry
  story and its own way of failing quietly.
- **Inbound REST API** — customers pull handoffs from realmspace on their
  schedule. ✅ `GET /v1/handoffs`, cursored on the log's own `seq` — the same
  cursor the WebSocket uses, so a client that stops at 4,180 and comes back
  tomorrow gets every lead since with no gap and no duplicate. Withdrawn leads are
  redacted at read time, because a withdrawal does not rewrite the log; only an
  erasure does.
- **Native SDK later** — thin client libs once demand is proven.

The webhook adapter is effectively "the contract, raw" — it's also how *we*
dogfood new adapters before writing them.

---

## 6. Lead capture (the inbound side of integrations)

Attribution needs an identity to attach. Capture surfaces feed the bus:

- **Badge scan** (event/trade-show badges) — richest, common at B2B events.
- **QR code** → consent + form on the visitor's phone.
- **Kiosk / tablet form** — on-booth capture.
- **Existing lead-capture tools** (Typeform, event platforms) via webhook-in.

Each is a `Surface` emitting a `consent`/`capture` event. Capture is **always
consent-gated** (see `consent-and-identity.md`).

---

## 7. Enrichment (optional, T3 only)

- **Apollo / Clearbit**-style enrichment to fill company/title from an email.
- Gated behind **consent tier T3** and metered for **cost telemetry** (per-lead
  enrichment credits count toward unit economics — see `roi-framework.md`).
- Pluggable like CRMs: an `EnrichmentAdapter` interface, BYO key per tenant.

---

## 8. Reliability (why the bus matters here)

CRMs rate-limit (429), go down, and change APIs. Because handoffs are bus
consumers:

- **Retry with backoff** on transient failure; **replay** from the log on recovery.
- **Dead-letter queue** → surfaced in a **HITL review screen** for manual fix.
- **No lead is ever lost** — the append-only log is the source of truth.

See `event-bus-spec.md` for the consumer/retry semantics and `roadmap.md` for
when each adapter lands.
