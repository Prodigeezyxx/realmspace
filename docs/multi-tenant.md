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

1. Sign up → create Organization. ✅ *(2026-08-21)*
2. Create first Activation (name, venue, dates) → the session wizard already
   exists (`/sessions/new`).
3. Pick a **prefab layout** + draw zones (exists: `PrefabPicker`, `ZoneEditor`).
4. Connect an integration (CRM or BYO webhook) — optional, for follow-up.
5. Set consent copy + tier + attribution model/window (ROI planned-in up front).
6. Run: edge kit or webcam → live dashboard.

Floats-specific fast path: pre-configured kits ship with the tenant + prefab
already provisioned.

### Step 1, as built *(2026-08-21)*

Steps 2–6 had existed since Phase 2 — the wizard, prefabs, the zone editor,
integrations, consent config, run. Step 1 did not, and its absence made the rest
unreachable for anybody new: a user existed only if somebody had run
`python -m app.auth.seed` on the server. The dashboard meanwhile shipped a
"Create your account" tab that produced a Firebase identity and then hit
`401 unknown user` forever, because a Firebase account is only half a login here
— the address still has to map to a tenant and a role.

`POST /v1/auth/signup` creates the Organization. Migration 0011 gives it
somewhere to live: a `tenant` table, the registry `repository.list_tenants` has
been predicting since Phase 1 ("a real registry is Phase 6"). That function still
reads the **event log**, deliberately — the registry says which organisations
exist, the log says which have work, and pointing the consumer loop at the
registry would make all sixteen consumers poll every organisation that ever
signed up, forever.

Three properties worth stating, because each is a thing the endpoint refuses:

- **It goes through the same identity gate as `POST /token`.** One
  `_verified_email`, one place that decides whose address this is, including the
  503 on a deployment with no Firebase project. An unauthenticated endpoint that
  hands out organisations must not have its own opinion about who somebody is.
- **It always creates a new organisation and never joins one.** No
  email-domain matching. §3's own reasoning for `/resolve` applies with more
  force here: defaulting somebody into a tenant is how a person ends up quietly
  holding a role nobody granted them.
- **An address that already has an account is refused, not upserted.**
  `auth_user.email` is globally unique and a user has one tenant, so an upsert
  would not create a second account — it would move that person out of their own
  organisation, leaving their sessions, leads and integrations behind.

Joining an existing organisation therefore has exactly one path: `POST /v1/users`,
behind `require_admin` per §3's "manage org, billing, integrations, **users**".
It is also what the client report's "Share with client" calls, with the role
fixed to `viewer` — §3 defines Viewer as "the sponsor or brand stakeholder", so
sharing a report *is* granting that seat.

**No email is sent.** There is no email provider in this repo — the absence
Phase 5's SDR sits behind, where the recorded decision was that it "drafts and
does not send". The response carries `emailSent: false` as a field rather than
only in prose, so a UI cannot render "invitation sent" by assuming.

Two guards on that endpoint, both protecting somebody who is already a customer:
an address belonging to another organisation is refused rather than moved, and
**the last admin cannot be removed or demoted** — an organisation with no admin
can never invite anybody, connect an integration, or honour a GDPR Article 17
erasure, and there is no recovery short of `psql`.

---

## 5. Billing hooks (design now, wire later)

Pricing tiers already exist in `gtm.md` (Booth / Pavilion / Campaign / Partner).
Multi-tenant makes them enforceable:

- **Metering:** per-activation, per-camera, Ask-the-Room queries, enrichment
  credits, LLM tokens (via `cost.metered` events → `roi-framework.md` unit
  economics).
  ✅ **Built** — `backend/app/cost.py`, with the `event_id` derived from the
  cause so a replay cannot inflate a client's spend. Its callers are every
  dispatched rule action (`action_unit`) and a model-backed Ask (`llm_tokens`).
  The gap is Ask: with no provider configured the deterministic answers report
  no tokens, so they leave no record at all — which is why `GET /v1/plan`
  reports Ask usage as *unknown* rather than zero.
- **Plan limits:** cameras, retention window, # agents, # integrations per tier.
  ✅ **Built** — `tenant.plan` (migration 0012) and `backend/app/plans.py`, the
  one place that says what a tier allows. Enforced at `POST /v1/sessions`
  (cameras), `PUT /v1/rules/{id}` (agents), `PUT /v1/integrations/{provider}`
  (integrations), and as a **read window** on `GET /events` and the activation
  listing. Refusals are **402**, not 403: 403 means "your role may not" and its
  remedy is an admin, while this means "your plan does not include" and its
  remedy is commercial.
  Only the numbers `gtm.md` states are enforced — two tiers say nothing about
  agents and none says anything about integrations, and inventing a default
  there would be inventing a commercial term. `roadmap.md`'s Phase 6 bullet
  carries the grid and the two silences.
  Retention clamps reads and deletes nothing; a purge is its own 🔲 with its
  reasons. `GET /v1/handoffs` and `GET /v1/ledger/{id}` are deliberately exempt
  — both redact from a tenant-wide read of withdrawals, and a floor there would
  hide a withdrawal and un-redact a name.
- **Partner revenue share:** white-label tenants with an 80/20 split flag. 🔲 —
  waits on the provider below, being a division of money nothing yet collects.
- **Provider:** Stripe (recommended) behind a thin billing abstraction; not in
  the MVP hot path. 🔲 — the credential does not exist in this repo. Changing a
  tenant's plan is deliberately **not** an endpoint until it does: a self-serve
  upgrade with no payment path is a free upgrade. `python -m app.plans set`
  is the operator action in the meantime.

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
