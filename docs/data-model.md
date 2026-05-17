# Data model

The graph is the product. Every detection from the perception engine becomes
a node or edge. Every interactive surface emits structured events into the
same graph. Every LLM-generated insight is persisted as a first-class node
linked back to the events that triggered it.

## Nodes

```cypher
(:Session {
  id, client, campaign, venue, city,
  started_at, ends_at,
  agency_name, booth_width_m, booth_depth_m,
  camera_count
})

(:Person {
  anon_id,                  // session-scoped ("P-211"), never re-used across sessions
  first_seen, last_seen,
  total_dwell_seconds,
  attention_score,          // 0..1, rolling
  appearance_summary        // VLM-derived: "person in red coat", never biometric
})

(:Zone {
  id, name, type,           // 'entry'|'experience'|'product'|'lounge'|'exit'
  polygon,                  // normalized booth coords
  color,
  capacity                  // optional, for crowding alerts
})

(:Object {
  id, label,                // 'Bottle Wall', 'Mannequin', 'Display Plinth'
  class,                    // YOLO class
  position,                 // world coords
  first_seen, last_seen
})

(:Surface {
  id, label, type,          // 'ar'|'game'|'screen'|'rfid'|'scent'|'product'
  zone_id, position,
  trigger_count,
  active                    // currently powered / responding
})

(:Event {
  id, type,                 // 'enter'|'exit'|'dwell'|'gaze'|'trigger'|'group'
  timestamp, confidence,
  meta
})

(:Insight {
  id, text,
  generated_by,             // 'llm' | 'rule' | 'manual'
  timestamp, confidence
})

(:Frame {                   // optional, only for opt-in replay debugging
  timestamp, frame_id, masked_image_url
})
```

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

## Example Cypher queries

These are the queries the "Ask the Room" LLM is prompted with. The LLM picks
from a constrained library to avoid hallucinating bad Cypher.

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
