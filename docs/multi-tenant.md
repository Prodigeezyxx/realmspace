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
  **Status:** done for Postgres — `backend/alembic/versions/0003_rls.py` puts
  forced RLS on `event_log`, `consumer_cursor` and `dead_letter`, and the app
  connects as a role that is neither superuser nor owner (both bypass policies).
  **Not possible for the graph:** Neo4j Community has no row-level security, no
  per-tenant database and no property-existence constraint, so that half stays
  application-enforced. Closing it needs an Enterprise licence or a different
  store — see `data-model.md` → "Store decision".
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

### As built *(2026-08-18)* — `backend/app/auth/principal.py`

All four roles have been in `USER_ROLES` since Phase 1 and **only two of them did
anything**: `admin` and `operator` were checked, so `analyst` and `viewer` were
both "neither of those" and therefore identical. An analyst could not do what the
table above grants them, and a client-facing viewer could reach everything an
analyst could. A role that changes no behaviour is a label.

**The table is now a capability map, not a ladder.** That distinction is the
reason the two collapsed: this is a *matrix* — an Operator runs activations and
does not build agents, an Analyst builds agents and does not run activations —
and a ladder of "reader < operator < admin" cannot express it.

| Capability | What it gates | Held by |
|---|---|---|
| `read` | dashboards, reports, events, insights, rule reads | all four |
| `run_activation` | session config, consent capture, outcomes, `/ops` | admin, operator |
| `author_rules` | writing a rule document | admin, analyst |
| `ask` | `POST /v1/ask` | admin, analyst |
| `leads` | `/v1/handoffs`, `/v1/followups` — contact PII | admin, analyst |
| `manage_org` | integrations, erasure | admin |

**The table is read as a deny-list.** A permission matrix where omission grants
nothing is the only kind that means anything, and two consequences are worth
stating plainly rather than leaving to look like oversights:

- **An operator cannot query Ask.** §3 puts "query Ask" with Analyst, and the
  person running the room is arguably who most wants to ask it a question. This
  is the deliberate reading; both it and the alternative are one line, and the
  test that pins it names itself.
- **A viewer cannot read leads.** They are "the sponsor/brand stakeholder" —
  the client reading a report about their activation, not their sales team
  reading the list of who attended. `/v1/handoffs` and `/v1/followups` return
  contact emails.

**Admin is a strict superset**, which this table does not say. An owner locked
out of their own floor on the morning of an event is a support ticket rather than
a security property, and an admin can grant themselves any role regardless — so
denying them would be theatre with a cost.

**An unrecognised role grants nothing.** `auth_user.role` is free text, and the
old gate admitted any human role by name — which is how three test suites ran for
weeks against a role called `"reader"` that was never in `USER_ROLES`.

**Device keys hold no capability at all**, not even `read`. An API key exists to
append events; a key taped inside a booth kit is the credential most likely to
walk out of a venue.

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
