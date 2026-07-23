# realmspace — Documentation Index

The brand name is always lowercase: **realmspace**.

realmspace is one product on one pipeline:
**Attract & Immerse → Measure → Attribute & Follow up.**
Start with `VISION.md`, then `roadmap.md`.

## Strategy & product
| Doc | What it covers |
|---|---|
| [VISION.md](./VISION.md) | The single pipeline thesis; why one product, why it wins |
| [roadmap.md](./roadmap.md) | Feature-sequenced phases (client-value order) + acceptance criteria |
| [PRD.md](./PRD.md) | Product requirements (v2) — the prototype spec |
| [gtm.md](./gtm.md) | Go-to-market motions, pricing, sales cycle |
| [competitive-landscape.md](./competitive-landscape.md) | Category map, positioning, objections, sizing |
| [roi-framework.md](./roi-framework.md) | ROI proving toolkit (4-layer model, metrics, attribution) |

## Architecture & engineering
| Doc | What it covers |
|---|---|
| [architecture.md](./architecture.md) | Agent architecture in the current app |
| [event-bus-spec.md](./event-bus-spec.md) | Append-only, idempotent, replayable bus — the spine |
| [data-model.md](./data-model.md) | Graph schema (nodes, edges, Cypher) |
| [integrations.md](./integrations.md) | CRM connector abstraction + bring-your-own |
| [multi-tenant.md](./multi-tenant.md) | Tenancy, isolation, RBAC, billing hooks |

## Trust & identity
| Doc | What it covers |
|---|---|
| [consent-and-identity.md](./consent-and-identity.md) | Consent tiers, redlines, anon+PII attribution |
| [privacy.md](./privacy.md) | Client-facing privacy posture |

## Brand
| Doc | What it covers |
|---|---|
| [brand.md](./brand.md) | Canonical brand reference (logo, type, colour, voice) |

---

### Reading order for a new engineer
1. `VISION.md` — what we're building and why
2. `roadmap.md` — what we build in what order
3. `event-bus-spec.md` + `data-model.md` — the spine and the schema
4. The feature doc for your phase (`roi-framework` / `integrations` / `consent-and-identity`)
5. `brand.md` — before touching any UI
