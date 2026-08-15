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
for aggregate ROI and can be attached to a lead later if consent arrives. *Not
built yet:* the attribution consumer emits only for identified contacts, because
an anonymous handoff needs a different trigger (every person, not every consent)
and a different consent story. Tracked in `roadmap.md` Phase 4.

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

Credentials come from `tenant_integration` (migration 0007), encrypted per
tenant per `multi-tenant.md` §2 and opened in exactly one function,
`crm.adapter_for`. `PUT /v1/integrations/{provider}` stores one; it is
admin-only, refuses a provider this build has no adapter for, and never returns
the secret back.

---

## 4. Tier 1 adapters (build order, by market share + client demand)

1. **HubSpot** — easiest API, common in mid-market; first adapter (reference impl).
2. **Salesforce** — enterprise default; highest ASP deals need it.
3. **Pipedrive** — SMB/agency favourite.
4. **Zoho CRM** — strong in our SAM (Africa + emerging markets).
5. **Microsoft Dynamics 365** — enterprise/regulated.

## 5. Bring-Your-Own connectors (no adapter needed)

For any stack we don't natively support, or customers who want control:

- **Generic Webhook** — POST the `LeadHandoff` JSON to a customer URL (HMAC-signed).
- **Zapier / Make** — publish to their webhook so non-technical users route it anywhere.
- **CSV / scheduled export** — for offline or air-gapped clients.
- **Inbound REST API** — customers pull handoffs from realmspace on their schedule.
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
