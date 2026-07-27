# realmspace — Multi-Tenancy (public product)

> realmspace is sold to **any booth/activation operator**, not only Floats.
> That makes multi-tenancy a day-one architectural requirement, not a later
> refactor. This doc defines tenant isolation, onboarding, and billing hooks.

---

## 1. Tenant model

```
Organization (tenant)         e.g. "Floats", "Agency X", "Brand Y"
  └── Workspace / Team
        └── User (roles)
        └── Activation (session)   the unit of work realmspace measures
              └── Zones / Surfaces / Events / Contacts (consent-gated)
              └── Integrations (per-tenant CRM creds, BYO webhooks)
```

- **Tenant = Organization.** Everything (events, contacts, integrations,
  billing, benchmarks) is scoped by `tenant_id`.
- **Floats is just tenant #1** — the flagship reference customer, on the same
  code path as everyone else. No special-casing.

---

## 2. Isolation

- **Data:** every table carries `tenant_id`; every query is tenant-scoped;
  row-level security (or equivalent) enforced at the DB layer, not just the app.
- **Event bus:** `event_log` is partitioned/filtered by `tenant_id`; consumer
  cursors are per `(consumer, tenant_id)` (see `event-bus-spec.md`).
- **Edge deployments:** a physical kit runs one tenant's activation at a time;
  the local log is stamped with that tenant. Cloud sync lands in the tenant's
  isolated store.
- **Secrets:** each tenant's CRM/enrichment credentials are stored encrypted,
  per-tenant, never shared.
- **AI calls:** prompts are tenant-scoped; no cross-tenant data in context.

---

## 3. Roles (RBAC)

| Role | Can |
|---|---|
| **Owner / Admin** | manage org, billing, integrations, users |
| **Operator** | run activations, see live dashboard, receive staff prompts |
| **Analyst / Marketer** | build agents, query Ask, own follow-up sequences, ROI reports |
| **Viewer / Client** | read-only dashboard + report (the sponsor/brand stakeholder) |

Maps cleanly to the actors in the founder architecture (Booth Operator, Event
Manager, Marketer, Sales Owner). Application roles are separate from *hosted
route access* — see repo access rules if/when deployed.

---

## 4. Onboarding (self-serve, because public)

1. Sign up → create Organization.
2. Create first Activation (name, venue, dates) → the session wizard already
   exists (`/sessions/new`).
3. Pick a **prefab layout** + draw zones (exists: `PrefabPicker`, `ZoneEditor`).
4. Connect an integration (CRM or BYO webhook) — optional, for follow-up.
5. Set consent copy + tier + attribution model/window (ROI planned-in up front).
6. Run: edge kit or webcam → live dashboard.

Floats-specific fast path: pre-configured kits ship with the tenant + prefab
already provisioned.

---

## 5. Billing hooks (design now, wire later)

Pricing tiers already exist in `gtm.md` (Booth / Pavilion / Campaign / Partner).
Multi-tenant makes them enforceable:

- **Metering:** per-activation, per-camera, Ask-the-Room queries, enrichment
  credits, LLM tokens (via `cost.metered` events → `roi-framework.md` unit
  economics).
- **Plan limits:** cameras, retention window, # agents, # integrations per tier.
- **Partner revenue share:** white-label tenants with an 80/20 split flag.
- **Provider:** Stripe (recommended) behind a thin billing abstraction; not in
  the MVP hot path.

---

## 6. What this changes about the build

- Add `tenant_id` to every schema from the first backend commit (cheap now,
  expensive later).
- Auth (Firebase today) must resolve a user → org → role on every request.
- The cross-activation **benchmark dataset** (the network effect and a real moat)
  is only possible *because* we're multi-tenant: anonymised aggregates across
  tenants power "vs. network median" in ROI proving. Never expose one tenant's
  raw data to another — only modelled, anonymised benchmarks.

See `roadmap.md` for when tenancy, RBAC, and billing land (tenancy + RBAC are
early; billing is post-first-revenue).
