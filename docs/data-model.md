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
  attribution_model,          // first_touch|last_touch|linear|time_decay|influenced

  // Operator-supplied, NOT measured. Influenced revenue comes from CRM
  // attribution (Phase 4); until then the honest options are the client's own
  // figure or nothing. Null → the ROI ratio reports unknown, never zero.
  revenue_influenced,
  qualified_leads
})
// key: (tenant_id, id)
// written by: POST /v1/sessions

(:Person {
  tenant_id,
  session_id,               // part of the key: anon_id means nothing without it
  anon_id,                  // session-scoped, never re-used across sessions.
                            // "cam-1/P-211" where the booth has cameras declared:
                            // ByteTrack numbers people per process and one process
                            // runs per camera, so every camera has a P-001. Bare
                            // ("P-211") on a one-camera session and on everything
                            // recorded before cameras had ids — see
                            // consumers/ids.py, person_key.
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
  camera_id,                // whose frame the polygon was drawn in. NULL = every
                            // camera, which is every zone drawn before this. A
                            // polygon is normalized *within one frame*, so the
                            // same numbers are different floor in another camera
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
// key: (tenant_id, session_id, id) — re-keyed by graph migration 006; see below

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

(:Outcome {                 // what a lead turned into — Phase 4, migration 004
  tenant_id,
  id,
  dedupe_key,               // the join back to the booth touch (handoff.lead)
  stage,                    // 'won' | 'lost' | 'open'
  value, currency,
  closed_at,
  source,                   // 'operator' | 'crm'
  external_ref              // the deal's id in the system it really lives in
})
// key: (tenant_id, id) — NOT per session; see below

(:Camera {                  // a camera on a booth — Phase 6, migration 005
  tenant_id, session_id,
  id,                       // matches perception's --camera-id exactly
  label,
  privacy_mask,             // the opt-out polygon of privacy.md, flattened
  mask_revision,            // bumped on every calibration; how an edge box
                            // knows the mask it cached is stale
  mask_updated_at
})
// key: (tenant_id, session_id, id) — like Zone after 002; see below
```

**`Outcome` is keyed per tenant, not per session, and that asymmetry is the
point.** A deal belongs to the client, not to the activation it started at. The
link back to an activation is the path through
`(Contact)-[:IDENTIFIED_AS]-(Person)`, which already carries a session — so
keying the deal per session would say that a deal touched by two activations was
two deals, which is precisely the double-count `roi-framework.md` §3's design
principle exists to prevent.

`dedupe_key` is **indexed, not unique**. One lead can legitimately produce
several outcomes — an opportunity that opens, then closes, then a renewal a year
later — and a unique index would refuse the second while silently keeping the
first.

**The consent path, added in Phase 4** (`consent-and-identity.md` §3):

```cypher
(:Contact { tenant_id, id, email, name, company, title, source, dedupe_key })
(:ConsentEvent { tenant_id, id, tier, basis, copy_version,
                 captured_at, captured_by, withdrawn_at })

(Person)-[:IDENTIFIED_AS {via, at}]->(Contact)   // deleted on withdrawal
(Contact)-[:GRANTED]->(ConsentEvent)
(ConsentEvent)-[:PERMITS]->(Person)
(Contact)-[:RESULTED_IN]->(Outcome)
```

Both `Contact` and `ConsentEvent` are tenant-keyed for the same reason `Outcome`
is: a `Person` is session-scoped because an `anon_id` is never reused, but the
human behind it is the same person at every activation they attend.

**`Camera` is the node this list stopped short of, and two events had been
waiting on it since Phase 3.** `drift.detected` and `calibration.updated` are
both keyed on a `camera_id` that had nowhere to live: `Session.camera_count` is
an integer, and nothing in the backend, the graph or perception knew what a
camera was. A privacy mask also has to belong to something, and it belongs to a
camera — a polygon is a region of one camera's frame and means nothing in
another's.

**Keyed per session, like `Zone` after migration 002 and unlike `Contact`.** A
physical camera outlives an activation, but a mask does not: it is drawn against
one booth layout, and an `anon_id` is destroyed at session end anyway. Keying
per tenant would carry yesterday's masked geometry onto today's floor and blank
the wrong pixels — a privacy failure that looks exactly like a working feature.

`privacy_mask` is stored flattened to `[x1,y1,x2,y2,…]` for the same reason
`Zone.polygon` is: Neo4j cannot hold a nested list as a property.
`graph/repository.py` rebuilds pairs on read, so no caller sees that shape.

`mask_revision` is a property rather than part of the key. Two recalibrations of
one camera are two events in the log and one node: the node holds current state,
and the log holds the history — which is why `calibration.updated` takes the
random-`event_id` exception in `event-bus-spec.md` §3.

**`Group` was re-keyed per session in migration 006** *(2026-08-21)*, the same
fix 002 made for `Zone` and `Surface` and for the same reason: a group is
session-scoped because `anon_id` is — this document calls an anon_id
"session-scoped, never re-used across sessions", so a set of them cannot mean
anything outside the session that minted them. Nothing had been lost to the old
key only because nothing had ever written a `Group` node; the migration lands
with the producer (`consumers/grouping.py`), which is the cheap direction. After
a season of activations, two sessions sharing a group id would be reconciled by
hand.

The query below is what this node was declared for, and until that producer
existed it returned nothing — every time, for the whole life of the project.

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
(Person)-[:LOOKED_AT {duration, confidence, started_at}]->(Zone)
        // ^ specified as (Object|Surface) and built against Zone — see below
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

### `LOOKED_AT` points at a Zone, not an Object or Surface

Written here as `(Object|Surface)` and built against `(Zone)`, deliberately, and
recorded rather than left for somebody to find in the Cypher.

A `Surface` in this system carries a `zone_id` and no geometry — there is
nothing in a camera frame to aim a ray at. A `Zone` has a normalized polygon and
an owning camera, which is exactly what casting a heading into the scene needs.
`Object` has no producer at all.

The event contract is unaffected: `spatial.gaze` carries `target_id`, which is
deliberately generic, so surface-level targeting can arrive later without a
migration — it needs an operator to place each surface in the frame, which is a
calibration step that does not exist yet and which every past activation would
be missing.

The edge keeps `confidence` because the measurement deserves to be weighed. It
comes from body pose on a monocular camera: a **facing direction**, not a gaze
vector. There is no depth and no eye tracking, so looking up and looking ahead
are the same reading. `consumers/gaze.py` refuses far more often than it emits,
and that file lists each refusal.


**`DERIVED_FROM` has no writer, deliberately** *(2026-08-18)*. `(:Event)` has a
key constraint in `graph/schema.py` and nothing has ever written one: events live
on the append-only log, which is the record of what happened and the thing a
replay reads. Copying them into the graph to give this edge somewhere to point
would duplicate the log into a store that holds *current state*, and the copy
would drift from the original the moment either changed.

So the supporting event ids travel in the `insight.generated` payload
(`event-bus-spec.md` §3), where a reader resolves them against the log itself —
which is a stronger citation than a graph edge, because it lands on the event as
it was written. `ABOUT` is written, and points at zones, which really are nodes
here.

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

Written against `(:Object)` when this file was; the built edge points at a
`(:Zone)`, for the reason above. The shape of the question is unchanged.

```cypher
MATCH (p:Person)-[g:LOOKED_AT]->(z:Zone {name: 'Bottle Wall'})
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
