# realmspace — Consent & Identity

> Identity is **in scope**, but only ever through an explicit consent gate.
> Attribution runs in **anonymous mode by default** and unlocks **PII mode
> per-person** only after a recorded consent event. This document defines the
> consent tiers, the hard redlines, and how anonymous and PII data coexist in
> one model.

This is a moat, not a compliance chore. "We monitor space, not people" is only
credible if it's enforced in the schema — see the invariants in
`data-model.md`, which this document extends.

---

## 1. The two modes, one model

| | Anonymous mode (default) | PII mode (consent-gated) |
|---|---|---|
| Person ID | `${session}_P_${n}` (e.g. `P-217`), session-scoped | Anonymous ID **linked to** an identified `Contact` |
| Contains PII? | Never | Yes — name, email, company, etc. |
| Powers | heatmaps, dwell, funnels, ROI reach/engagement | lead capture, CRM sync, personalised follow-up |
| Requires | nothing | a recorded `ConsentEvent` for that person |
| Reversible | n/a | Yes — consent withdrawal re-anonymises (see §5) |

Everything is anonymous until a **consent event** flips one person into PII mode.
Before the event: pure telemetry. After: an attributed lead. **No PII is ever
written without a consent basis recorded on the same transaction.**

---

## 2. Consent tiers (redlines)

Each tier is a strict superset of the one above. The tier is stored on the
`ConsentEvent` and enforced at write time.

| Tier | What the visitor agreed to | What realmspace may do | Redline (never) |
|---|---|---|---|
| **T0 — Ambient** | Nothing; anonymous analytics signage only | Anonymous telemetry, aggregate ROI | No PII, no identity, no faces, ever |
| **T1 — Capture** | "Take my details" (badge/QR/form) | Store contact PII, link to their path, basic follow-up | No enrichment, no CRM sync beyond agreed purpose |
| **T2 — Contact** | Explicit opt-in to be contacted / marketed to | CRM sync, personalised follow-up (SDR), enrichment | No selling/sharing data to third parties |
| **T3 — Enrich & profile** | Opt-in to enrichment & profiling | Third-party enrichment (Apollo/Clearbit), lead scoring | No cross-activation re-identification without T3 at each |

**Universal redlines (all tiers, non-negotiable):**
- ❌ No facial recognition, ever. No face embeddings computed or stored.
- ❌ No cross-session / cross-camera re-identification of *anonymous* people.
- ❌ No PII write without a `ConsentEvent` on the same transaction.
- ❌ No raw video leaves the edge device. AI calls receive structured summaries only.
- ❌ Consent is per-activation by default; it does not silently roll over.

---

## 3. The consent event (schema addition)

Extends the graph in `data-model.md`:

```cypher
(:Contact {                      // only exists in PII mode
  id,                            // stable per tenant, NOT the anon_id
  email, name, company, title,
  source,                       // 'badge'|'qr'|'kiosk'|'form'|'manual'
  created_at
})

(:ConsentEvent {
  id,
  tier,                         // 'T1'|'T2'|'T3'
  basis,                        // 'explicit_optin'|'contract'|'legitimate_interest'
  copy_version,                 // exact consent wording shown, versioned
  captured_at,
  captured_by,                  // surface/operator that captured it
  expires_at,                   // optional; default = session/activation end
  withdrawn_at                  // set on withdrawal (§5)
})

// the gate: links an anonymous Person to a Contact, justified by consent
(Person)-[:IDENTIFIED_AS {via: 'badge', at: ts}]->(Contact)
(Contact)-[:GRANTED]->(ConsentEvent)
(ConsentEvent)-[:PERMITS]->(Person)     // scope of what the consent covers
```

**Write-time enforcement:** the `IDENTIFIED_AS` edge cannot be created unless a
non-withdrawn `ConsentEvent` of the required tier exists in the same transaction.
CRM sync consumers refuse to emit a contact without a `≥T2` consent. This is
enforced in code (the bus consumer), not by convention.

---

## 4. The consent capture flow (in the pipeline)

```
Anonymous path (P-217)  ──dwell/funnel──▶ ROI (no consent needed)
        │
        ▼  visitor scans badge / QR / fills kiosk form
   Consent surface shows versioned copy + tier choice
        │
        ├── declines ─▶ stays anonymous (T0). Path still counts for ROI.
        │
        └── accepts (T1+) ─▶ ConsentEvent written ─▶ IDENTIFIED_AS edge ─▶
                              Contact created ─▶ (T2+) eligible for CRM sync
                              ─▶ (T3) eligible for enrichment
```

Consent copy is **versioned** so we can always prove exactly what a person
agreed to and when. The kiosk/QR/badge capture is a `Surface` that emits a
`consent` event onto the bus like any other signal.

---

## 5. Withdrawal & failure states (must be designed, not hoped)

- **Consent withdrawn mid-visit** → set `withdrawn_at`; a bus consumer
  re-anonymises: drops the `IDENTIFIED_AS` edge, deletes/redacts the `Contact`
  per policy, and emits a `crm.retract` where the CRM supports it. The anonymous
  path survives for aggregate ROI.
- **Right to erasure (GDPR Art. 17 / CCPA)** → tenant-scoped erasure job that
  removes a `Contact` and all PII edges, keeping only anonymised aggregates.
- **Enrichment API 429 / CRM down** → replayable bus means the handoff retries;
  it never silently drops. Failed handoffs land in the HITL dead-letter queue.
- **Attribution decay** → after the window (30/60/90d) the booth-touch → outcome
  link ages out of active attribution but stays in the audit ledger.

---

## 6. Compliance posture (public product)

Because realmspace is sold to the public across jurisdictions:

- **GDPR / UK DPA** — anonymous mode collects no personal data (Art. 4(1)); PII
  mode is lawful only with a recorded basis (consent/contract/LI) on the
  `ConsentEvent`. DPIA template shipped per tenant.
- **CCPA/CPRA** — no "personal information" in anonymous mode; PII mode honours
  opt-out & deletion.
- **NDPA (Nigeria)** and other local regimes — same model; tier + basis are
  configurable per tenant/region.
- **Per-tenant configurability** — a tenant can disable PII mode entirely
  (pure-anonymous deployments), or restrict max tier, or region-lock enrichment.

See `privacy.md` for the client-facing posture doc, `integrations.md` for how
consent tiers gate CRM sync, and `roi-framework.md` for which metrics need which
tier.
