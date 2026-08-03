# Data model

The graph is the product. Every detection from the perception engine becomes
a node or edge. Every interactive surface emits structured events into the
same graph. Every LLM-generated insight is persisted as a first-class node
linked back to the events that triggered it.

Implemented in `backend/app/graph/` — see `schema.py` for the constraints and
`repository.py` for the only place Cypher is written.

## `tenant_id` is on every node

`multi-tenant.md` §2: "every table carries `tenant_id`; every query is
tenant-scoped." That applies here — **every node below carries `tenant_id`**,
and it is the first element of every uniqueness key so a lookup for one tenant
never touches another's nodes, and two tenants can independently use the same
zone id without colliding.

Neo4j Community **cannot enforce this** (see "Store decision" at the bottom), so
`backend/app/graph/repository.py` is the only thing that does. Every function
there takes `tenant_id` as a required argument.

## Nodes

```cypher
(:Session {
  tenant_id,                // required on every node — multi-tenant.md §2
  id, client, campaign, venue, city,
  started_at, ends_at,
  agency_name, booth_width_m, booth_depth_m,
  camera_count,

  // Measurement parameters — set at setup, before the activation runs
  // (roi-framework.md §5). Stored, never inferred later: a parameter chosen
  // after the numbers are in is a parameter chosen to flatter them.
  engaged_threshold_seconds,  // dwell above this counts as "engaged" (default 60)
  activation_cost,            // denominator of CPEV, CPQL and the ROI ratio
  currency,                   // 3-letter code, default 'USD'
  attribution_model           // first_touch|last_touch|linear|time_decay|influenced
})
// key: (tenant_id, id)
// written by: POST /v1/sessions

(:Person {
  tenant_id,
  session_id,               // part of the key: anon_id means nothing without it
  anon_id,                  // session-scoped ("P-211"), never re-used across sessions
  first_seen, last_seen,
  total_dwell_seconds,
  attention_score,          // 0..1, rolling
  appearance_summary        // VLM-derived: "person in red coat", never biometric
})
// key: (tenant_id, session_id, anon_id)

(:Zone {
  tenant_id, session_id,
  id, name, type,           // see ZoneType below
  polygon,                  // normalized booth coords, flattened [x1,y1,x2,y2,…]
  color,
  capacity,                 // optional, for crowding alerts

  weight,                   // ROI weight, default 1.0 — dwell-weighted attention
                            // is Σ(dwell × weight); not all dwell is equal
                            // (roi-framework.md §2, Layer 2)
  funnel_order              // position in entry → experience → product → capture
})
// key: (tenant_id, id)
// written by: POST /v1/sessions

(:Object {
  tenant_id, session_id,
  id, label,                // 'Bottle Wall', 'Mannequin', 'Display Plinth'
  class,                    // YOLO class
  position,                 // world coords
  first_seen, last_seen
})
// key: (tenant_id, id)

(:Surface {
  tenant_id, session_id,
  id, label, type,          // 'ar'|'game'|'screen'|'rfid'|'scent'|'product'
  zone_id, position,
  trigger_count,
  active                    // currently powered / responding
})
// key: (tenant_id, id)

(:Group {                   // co-visiting people — roadmap.md blind-spot "Group visits"
  tenant_id, session_id,
  id,
  size,                     // member count at last evaluation
  first_seen, last_seen,
  cohesion                  // 0..1, how consistently they moved together
})
// key: (tenant_id, id)

(:Event {
  tenant_id, session_id,
  id, type,                 // 'enter'|'exit'|'dwell'|'gaze'|'trigger'|'group'
  timestamp, confidence,
  meta
})
// key: (tenant_id, id)

(:Insight {
  tenant_id, session_id,
  id, text,
  generated_by,             // 'llm' | 'rule' | 'manual'
  timestamp, confidence
})
// key: (tenant_id, id)

(:Frame {                   // optional, only for opt-in replay debugging
  tenant_id, session_id,
  timestamp, frame_id, masked_image_url
})
// no key — see note below
```

**`Frame` has no uniqueness key and is therefore the one node type with no
constraint in `backend/app/graph/schema.py`.** It is the only node declared
without an `id`, and `frame_id` alone is not unique across sessions. Before
anything writes `Frame` nodes (opt-in replay debugging, Phase 5) it needs a key
— `(tenant_id, session_id, frame_id)` is the obvious candidate.

### ZoneType

The canonical list is `ZoneType` in `dashboard/src/lib/session/types.ts` — the
session wizard writes these today, so the app is the source of truth:

```
entry | reveal | engagement | lounge | retail
sponsor | demo | press | exit | privacy_masked | other
```

`privacy_masked` matters beyond taxonomy: it marks zones where detection is
suppressed or frames are never retained (`privacy.md`).

## Relationships

```cypher
(Person)-[:ENTERED]->(Zone)
(Person)-[:LEFT]->(Zone)
(Person)-[:DWELLED_IN {duration, started_at, ended_at}]->(Zone)
(Person)-[:LOOKED_AT {duration, confidence, started_at}]->(Object|Surface)
(Person)-[:INTERACTED_WITH {duration, kind}]->(Surface)
(Person)-[:NEAR {distance_m, duration}]->(Person)
(Person)-[:GROUP_MEMBER_OF]->(Group)

(Surface)-[:LIVE_IN]->(Zone)
(Object)-[:LIVE_IN]->(Zone)
(Object)-[:CO_OCCURRED_WITH {count}]->(Object)

(Event)-[:INVOLVED]->(Person)
(Event)-[:OCCURRED_IN]->(Zone)
(Event)-[:TRIGGERED_BY]->(Surface)
(Insight)-[:DERIVED_FROM]->(Event)
(Insight)-[:ABOUT]->(Zone|Surface|Person|Group)
```

Relationships are written with `MERGE`, never `CREATE`. Graph writes come from
bus consumers, which are at-least-once (`event-bus-spec.md` §4) — and because
the graph is a separate store from the log, a graph write and a cursor advance
cannot share a transaction, so replay after a crash *will* re-apply writes.
`MERGE` makes that harmless. Both endpoints are always matched with `tenant_id`,
so an edge can never be drawn between two tenants.

## Example Cypher queries

These are the queries the "Ask the Room" LLM is prompted with. The LLM picks
from a constrained library to avoid hallucinating bad Cypher.

> **These examples are illustrative and are not tenant-scoped.** They predate
> `multi-tenant.md` and none of them filters on `tenant_id`, so running one
> as-written against a shared instance would read across tenants. Before the
> allow-list is built in Phase 2, every entry needs `{tenant_id: $tenant_id}` on
> each matched node. `backend/app/graph/repository.py:dwell_by_zone` is the
> scoped version of the second example and shows the shape.

### Unique visitors today

```cypher
MATCH (p:Person)
WHERE p.first_seen >= date()
RETURN count(DISTINCT p) AS unique_visitors
```

### Longest average dwell per zone

```cypher
MATCH (p:Person)-[d:DWELLED_IN]->(z:Zone)
RETURN z.name AS zone, avg(d.duration) AS avg_dwell, count(p) AS visitors
ORDER BY avg_dwell DESC
```

### Conversion: Mirror Room → RFID capture

```cypher
MATCH (p:Person)-[:DWELLED_IN]->(m:Zone {name: 'Mirror Room'})
OPTIONAL MATCH (p)-[:INTERACTED_WITH]->(r:Surface {label: 'Memory RFID'})
WITH count(p) AS visited_mirror, count(r) AS converted
RETURN visited_mirror, converted,
       100.0 * converted / visited_mirror AS conversion_pct
```

### Visitors who held gaze on Bottle Wall > 10 s

```cypher
MATCH (p:Person)-[g:LOOKED_AT]->(o:Object {label: 'Bottle Wall'})
WHERE g.duration > 10
OPTIONAL MATCH (p)-[:INTERACTED_WITH]->(r:Surface {label: 'Memory RFID'})
RETURN p.anon_id, g.duration AS gaze_seconds,
       r IS NOT NULL AS converted
ORDER BY g.duration DESC
```

### Groups in Lounge for 4 min+

```cypher
MATCH (g:Group)-[:GROUP_MEMBER_OF]-(p:Person)-[d:DWELLED_IN]->(z:Zone {name: 'Lounge'})
WITH g, z, count(p) AS members, max(d.duration) AS group_dwell
WHERE members >= 3 AND group_dwell >= 240
RETURN g.id, members, group_dwell
ORDER BY group_dwell DESC
```

### Compare today vs. yesterday

```cypher
MATCH (p:Person)
WITH date(p.first_seen) AS day, count(p) AS visitors,
     avg(p.total_dwell_seconds) AS avg_dwell
RETURN day, visitors, avg_dwell
ORDER BY day DESC LIMIT 2
```

### The full activation funnel

```cypher
MATCH (entry:Zone {type: 'entry'})
MATCH (p:Person)-[:ENTERED]->(entry)
OPTIONAL MATCH (p)-[:DWELLED_IN]->(mirror:Zone {type: 'experience'})
OPTIONAL MATCH (p)-[:DWELLED_IN]->(product:Zone {type: 'product'})
OPTIONAL MATCH (p)-[:DWELLED_IN]->(lounge:Zone {type: 'lounge'})
OPTIONAL MATCH (p)-[:INTERACTED_WITH]->(rfid:Surface {label: 'Memory RFID'})
RETURN
  count(p) AS entered,
  count(mirror) AS reached_experience,
  count(product) AS reached_product,
  count(lounge) AS reached_lounge,
  count(rfid) AS captured_memory
```

## Why graph, not just Postgres

The reason graph wins for this domain isn't performance — it's *expressivity
for the LLM*. Asking GPT-4o to translate

> "Show me visitors who looked at the Bottle Wall for >10s and then visited
> the Lounge with 2 or more people who also looked at it"

into a JOIN-heavy SQL query is brittle. The same translation to Cypher is
near-trivial because the query mirrors the natural-language structure.

Postgres still holds the timeseries and the report cache. Neo4j holds the
graph. Qdrant holds embeddings of all `Insight` and `appearance_summary`
fields for semantic search.

## Store decision (Phase 1)

`roadmap.md` open decision #1 — Neo4j vs. an embedded/Postgres graph — was
**resolved in favour of Neo4j**, matching what was already written in
`PRD.md:156`, `PRD.md:302`, `privacy.md:48` and this document. `privacy.md` is
client-facing, which set the bar for deviating above an engineering preference.

Two consequences are being carried knowingly rather than buried:

**1. Tenant isolation is application-enforced, not database-enforced.**
`multi-tenant.md` §2 asks for isolation "enforced at the DB layer, not just the
app". Neo4j Community cannot do that. Verified against the running instance:

```
CREATE CONSTRAINT … REQUIRE z.tenant_id IS NOT NULL
→ 51N27: Property existence constraint is not supported in community edition
SHOW DATABASES → only `neo4j` and `system`   (multi-database is Enterprise)
```

So nothing at the database layer stops a node being written without a
`tenant_id`, or a query reading across tenants. The compensating control is that
`backend/app/graph/repository.py` holds every Cypher statement in the system and
requires `tenant_id` on every function. **If Cypher leaks out of that module,
the guarantee is gone silently.** Closing this properly needs a Neo4j Enterprise
licence or a different store — open for the supervisor.

Not a live risk in Phase 1: an edge kit runs one tenant's activation at a time
(`multi-tenant.md` §2). It becomes one when tenants share a cloud instance.

**2. Packaging deviates from `PRD.md:315`.** That line specifies
`docker compose up` for Neo4j + Postgres + Qdrant. Both stores are installed via
Homebrew instead (`brew services`), because Postgres was already set up that way
in the previous item and it avoids a Docker Desktop dependency on the edge box.
The architecture is unchanged — only the packaging. Qdrant is not installed;
nothing needs embeddings before Phase 2.

Schema is applied by a small migration runner rather than by hand:

```bash
backend/.venv/bin/python -m app.graph.migrations upgrade
backend/.venv/bin/python -m app.graph.migrations status
```

Versions are recorded as `(:_SchemaVersion)` nodes in the graph, so the command
is re-runnable and the applied state is queryable — the same contract Alembic
gives the Postgres side.

## Anonymisation invariants

These are enforced at write time, not asked nicely after the fact:

- `Person.anon_id` is `${session_id}_P_${monotonic_int}`. It is impossible to
  resolve to a real person without the perception laptop's session keys,
  which are destroyed at session close.
- `Person.appearance_summary` is the only descriptive field. It is a
  short VLM-generated phrase like "person in dark coat". It must never
  include identifying features (face shape, named clothing brands, name
  tags, etc.) — this is enforced by a content filter on write.
- `Frame.masked_image_url` is only populated for opt-in zones; faces are
  blurred before storage.
- All raw camera frames are kept in a ring buffer that flushes every 60s
  unless an explicit replay handle has captured them.
