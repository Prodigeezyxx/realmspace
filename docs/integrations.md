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
    "attention_score": 0.82,
    "lead_score": 74,                // computed from spatial + (opt) enrichment
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
for aggregate ROI and can be attached to a lead later if consent arrives.

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
