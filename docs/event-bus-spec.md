# realmspace — Event Bus Spec

> The spine that carries a signal from perception → graph → action → CRM.
> **Append-only, idempotent, replayable.** It is plumbing for the follow-up
> features (see `VISION.md` §6), not a separate product. Every capability —
> attribution, CRM sync, SDR follow-up, ROI — is a **consumer** on this bus.

---

## 1. Why a bus, and why this shape

Real-world constraints (see `blind-spots` in `roadmap.md`) force the design:

- **Conference WiFi is unreliable** → the bus must run **locally on the edge
  device** and survive going offline.
- **Latency SLA < 3s for live actions** → real-time consumers run on the edge; no
  cloud round-trip in the hot path.
- **CRMs/enrichment fail** → producers must never block on consumers; failed
  consumers **replay** instead of losing data.

**MVP decision: the bus is a Postgres append-only table, not Kafka.**
Rationale: runs offline on a laptop, zero ops, gives idempotency + replay for
free, sufficient for 6–50 deployments/yr. Revisit Pub/Sub/Kafka only at platform
scale (H3). This is deliberately boring infrastructure.

---

## 2. The log

```sql
CREATE TABLE event_log (
  seq          BIGSERIAL PRIMARY KEY,     -- monotonic order (replay cursor)
  event_id     UUID UNIQUE NOT NULL,      -- producer-assigned; dedupe key
  tenant_id    TEXT NOT NULL,
  session_id   TEXT NOT NULL,
  type         TEXT NOT NULL,             -- see §3
  payload      JSONB NOT NULL,
  occurred_at  TIMESTAMPTZ NOT NULL,      -- when it happened (edge clock)
  recorded_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON event_log (tenant_id, session_id, seq);
CREATE INDEX ON event_log (type);

CREATE TABLE consumer_cursor (
  consumer     TEXT NOT NULL,
  tenant_id    TEXT NOT NULL,
  last_seq     BIGINT NOT NULL DEFAULT 0,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (consumer, tenant_id)
);

CREATE TABLE dead_letter (
  id           BIGSERIAL PRIMARY KEY,
  consumer     TEXT NOT NULL,
  event_seq    BIGINT NOT NULL,
  error        TEXT NOT NULL,
  attempts     INT NOT NULL DEFAULT 0,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at  TIMESTAMPTZ
);
```

- **Idempotency:** `event_id` is UNIQUE. Re-inserting a duplicate is a no-op.
  Consumers must also be idempotent on `event_id` (upserts, not blind inserts).
- **Replay:** a consumer replays by resetting `consumer_cursor.last_seq`.
- **Ordering:** `seq` is the single source of truth for order per tenant/session.
- **`seq` is ordered but NOT contiguous.** `BIGSERIAL` draws its number *before*
  the conflict check, so a deduped insert burns a number and leaves a permanent
  gap (1, 3, 4…); rolled-back transactions do the same. Consumers must poll
  `WHERE seq > last_seq`. A consumer asking for `last_seq + 1` stalls forever on
  an event that will never exist.
- **Commit ordering ≠ `seq` ordering.** `seq` is assigned at INSERT; the row
  only becomes visible at COMMIT. With concurrent producers a consumer can read
  `seq` 6 while 5 is still uncommitted, advance its cursor past 5, and silently
  never process it. Not a live risk while the edge box has a single writer, but
  it becomes real the moment a second producer appears (e.g. `surface.interaction`
  in §3). Mitigations: keep one writer per tenant, or have consumers lag their
  cursor by a few seconds so in-flight transactions land first.

---

## 3. Event taxonomy (v1)

Producer → bus → consumers. Types are namespaced and additive-only.

| Type | Producer | Payload gist | Key consumers |
|---|---|---|---|
| `perception.detection` | edge perception | person bbox, conf, frame | tracker, graph |
| `spatial.zone_enter` / `zone_exit` | tracker | anon_id, zone, ts | graph, rules, ROI |
| `spatial.dwell` | tracker | anon_id, zone, duration | graph, rules, ROI |
| `spatial.gaze` | tracker | anon_id, object, duration | graph, ROI |
| `spatial.group` | tracker | group_id, members | graph |
| `spatial.passby` | tracker | anon_id, adjacent (negative signal) | ROI |
| `surface.interaction` | booth surface | surface_id, anon_id, kind | graph, ROI |
| `rfid.read` | RFID reader (MQTT/serial) | reader_id, tag_id, ts | identity, graph, ROI |
| `spatial.tagged` | vision × RFID fusion | anon_id ↔ tag_id, confidence | identity, graph, ROI |
| `consent.captured` | capture surface | tier, basis, copy_version | identity, CRM gate |
| `consent.withdrawn` | anywhere | contact_id | re-anonymiser, CRM retract |
| `identity.resolved` | identity consumer | anon_id ↔ contact | CRM, follow-up |
| `rule.fired` | rules engine | rule_id, action | action consumers |
| `intent.scored` | intent consumer | anon_id, score, band | rules, report, handoff |
| `handoff.lead` | attribution | normalized LeadHandoff | CRM adapters |
| `crm.retract` | re-anonymiser | contact_id, destination, reason | CRM adapters |
| `insight.generated` | LLM/agents | text, refs | graph, dashboard |
| `cost.metered` | consumers | tokens/credits/$ | cost telemetry |
| `drift.detected` | perception telemetry | camera_id, metric, observed vs baseline | ops, calibration UI |
| `calibration.updated` | calibration UI (operator) | camera_id, kind, revision | tracker (cache invalidation) |
| `session.started` / `session.ended` | operator | session meta | report, sync |
| `session.zones_updated` | `POST /v1/sessions` | zone_ids, zone_count, by | tracker (cache invalidation) |

**Six of these have no producer yet** — `rfid.read`, `spatial.tagged`,
`intent.scored`, `drift.detected`, `calibration.updated`, `crm.retract`. They are
registered here, and their namespaces accepted by `backend/app/schemas.py`,
*before* the phases that emit them (P3 rules/RFID, P4 capture, P6 calibration).
Additive-only is only free if the additions land ahead of the code: a producer
that meets a 422 from a namespace check has nothing in the error to tell its
author that the taxonomy, not their request, is the thing refusing them.

### Payloads pinned so far

The gist column above is not enough to write a producer against. Types are
pinned here as they get implemented.

**`perception.detection`** — producer: edge perception:

```json
{
  "anon_id":      "P-012",        // from ByteTrack; session-scoped, never reused
  "bbox":         [x1, y1, x2, y2],   // PIXELS, xyxy (what YOLO returns)
  "confidence":   0.91,
  "frame_width":  1280,           // required — see below
  "frame_height": 720
}
```

`anon_id` is canonical — it matches `Person.anon_id` in `data-model.md`.
**`person_id` is also accepted**, because the producer on the other track sends
`anonId`/`person_id` and `perception/realmspace.py` is a file both tracks share:
one script has to work against either backend or the comparison breaks. Consumers
read `anon_id` first and fall back.

`frame_width` / `frame_height` are **required**. Bounding boxes arrive in pixels
but `Zone.polygon` is normalized 0–1, so without the frame size there is no way
to tell which zone a detection is in. The tracker dead-letters detections that
omit them rather than guessing. (Same normalisation the browser does in
`dashboard/src/skills/zone-detect.ts`.)

**`rfid.read`** — producer: RFID reader bridge. Added for Week 1 task 1.11; the
reader speaks MQTT or serial and a small bridge turns each read into an event.
`tag_id` is the badge, not a person — linking it to a `Person` is the identity
consumer's job and is consent-gated (`consent-and-identity.md`).

```json
{
  "reader_id": "rdr-entrance",
  "tag_id":    "E280-1160-6000-0209",   // the badge, not a person
  "rssi":      -54,                     // dBm; the fusion consumer's distance proxy
  "at":        "2026-08-11T10:04:02Z"
}
```

Mirrored in `dashboard/src/lib/contracts/events.ts` as `RfidReadPayload`.

**`spatial.tagged`** — producer: the vision × RFID fusion consumer:

```json
{
  "anon_id":    "P-012",
  "tag_id":     "E280-1160-6000-0209",
  "reader_id":  "rdr-entrance",
  "confidence": 0.72,
  "method":     "rssi_proximity",   // reader position + RSSI decay + track proximity
  "at":         "2026-08-11T10:04:02Z"
}
```

**A correlation, not an identity, and the distinction is the privacy posture.**
It says a badge was probably carried by a tracked person — a guess, which is why
it has a `confidence` and a `method` rather than being a fact. It is still
anonymous: `tag_id` is a badge and `anon_id` is a track, and neither is a
`Contact`. Linking either to a person is the identity consumer's job and is
consent-gated (`consent-and-identity.md`). A consumer that reads this as
identity has skipped the gate.

`method` is recorded because RFID fusion is tuned per venue — reader geometry
and RSSI decay differ per room — and a stored score whose derivation is unknown
cannot be re-judged after the fact.

**`spatial.zone_enter`** — producer: tracker:
`{ "anon_id", "zone_id", "at" }`

**`spatial.zone_exit`** — producer: tracker:
`{ "anon_id", "zone_id", "at", "entered_at", "reason" }`

**`spatial.dwell`** — producer: tracker:
`{ "anon_id", "zone_id", "duration", "started_at", "ended_at", "exceeded_threshold", "reason" }`
— `duration` in seconds; `exceeded_threshold` compares it to the per-session
dwell threshold (30s today, matching `agents/definitions/dwell.ts`).

`reason` is `"move"` or `"dropout"`. **A dropout duration is a lower bound, not
a measurement**: the track stopped being detected inside the zone and the visit
was closed at its last sighting, so the person may well have stayed longer.
Anything averaging or ranking dwell should be able to tell the two apart —
`spatial.zone_exit` carries the same field for the same reason.

### Session hygiene: what counts as a visit

Raw polygon membership is not a visit, and the difference is not cosmetic. The
tracker applies three rules before emitting anything (`backend/app/consumers/
tracker.py`, ported from the postgres-track's `spatial-deriver.ts`, whose header
cites CHI '26 — 71% of raw sessions are invalid without them):

| Rule | Setting | What it prevents |
|---|---|---|
| **Confirm window** | `tracker_zone_confirm_seconds` (0.6s) | Someone on a zone edge crossing it at frame rate. Each wobble is otherwise a complete enter/exit/dwell triple, inflating footfall and halving average dwell, with nothing in the data to show for it. |
| **Minimum dwell** | `tracker_min_dwell_seconds` (1.0s) | Clipping a corner counting as time spent. The exit still fires; only the dwell is dropped. |
| **Dropout sweep** | `tracker_dropout_seconds` (20s) | A track vanishing inside a zone emitting *nothing at all* — the visit silently discarded, which under-reports precisely the long stays at the far end of a booth. Also the only moment a pass-by can be judged. |
| **Pass-by radius** | `tracker_passby_radius` (0.08) | Without a radius, every visitor is a pass-by for every zone they didn't visit and the signal means nothing. |

**The sweep does not skip tracks with no confirmed zone.** It used to, and that
excluded precisely the pass-by case — a visitor who never enters a zone is the
signal, not an absence of one.

**A limit worth knowing.** All of this runs on *event time*, so a session that
simply stops — no `session.ended`, no further events of any kind — leaves its
last tracks open. `session.ended` is the fix; a wall-clock timer would close
them but would also make a replay produce different events from the original
run, which is a worse trade.

Timestamps use the **real crossing**, never the moment of confirmation, so the
window is not charged to the visitor. All of it runs on event time rather than
wall clock, which is what keeps a replay byte-identical.

**Consequence for producers:** a zone must be seen twice, at least the confirm
window apart, to register at all. Real perception at ~20fps satisfies this in 12
frames; sparse synthetic input does not, and will produce silence.

**`spatial.passby`** — producer: tracker:
`{ "anon_id", "adjacent_zone_id", "closest_dist", "at", "reason" }`

The Reach layer's negative signal (`roi-framework.md` §2): came within
`tracker_passby_radius` of a zone and never entered it. `closest_dist` is the
nearest normalized approach over the whole session.

**Only knowable at close-out.** Until a track ends, somebody loitering outside a
zone might still walk in, so this is emitted when the track is finalised —
either by dropout or by `session.ended` — never while they are still in frame.
One per person per zone for the session, not per approach: someone pacing
outside a stand is one person who declined it, not twelve. Entering the zone at
any point cancels it outright; a visitor cannot be both the engagement and the
skip.

**`session.ended`** — producer: an operator (the dashboard's End session button):
`{ "sessionId", "endedBy" }`

The doors have shut. The tracker finalises every track still open in that
session — closing dwells and judging pass-bys — because no further detections
are coming and the dropout sweep needs a later event to run on. Without it the
tail of every activation is silently lost, and the visitors still in the room at
the end are exactly the engaged ones.

**`session.zones_updated`** — producer: `POST /v1/sessions` (an operator):
`{ "zone_ids": ["z_entry", …], "zone_count": 3, "by": "u_op" }`

Announces that a session's zones or measurement parameters changed. The tracker
caches zone polygons on its hot path and subscribes to this to drop that cache,
so a redraw takes effect on the next detection rather than up to a TTL later.

Its `event_id` is **random, not derived** — the exception to the rule below, and
for the reason the rule gives: this is a producer recording something that
genuinely just happened. Two edits to the same zone set are two distinct facts,
and deriving the id from the zone ids would collapse them onto one, silently
discarding the second edit.

**`intent.scored`** — producer: the intent consumer. **Provisional.**
`{ "anon_id", "score", "band", "signals", "model_version" }`

`score` is 0..1 and `band` is `"cold" | "warm" | "hot"` — the band is stored
rather than recomputed, because the thresholds are a per-tenant setting and a
report rendered next year must show the band the operator acted on, not the one
today's thresholds would produce. `signals` is the feature map the score came
from (dwell, revisits, surface touches), kept so a score can be explained to a
client who disputes it. `model_version` is what makes a replay reproducible: the
same events through a newer scorer give a different number, and without the
version there is no way to tell that apart from a data change.

Marked **provisional**: nothing pins the scoring model yet. The type is
registered now so P4's producer does not arrive to a 422, and the payload is
re-pinned here when that producer lands.

**`drift.detected`** — producer: perception telemetry:
`{ "camera_id", "metric", "observed", "baseline", "window_seconds", "severity" }`

CV drift is on the risk register (`roadmap.md`) with calibration UI + drift
telemetry as the mitigation. `metric` is what moved — `detection_rate`,
`confidence_mean`, `track_length` — and both `observed` and `baseline` are
carried so the event states the comparison it is making instead of asserting a
verdict someone later cannot check. `severity` is `"warn" | "critical"`.

About the *camera*, not a visitor, but session-scoped like everything on the bus:
drift matters exactly when it silently degrades a session's numbers, and finding
that out means being able to read it alongside that session's events.

**`calibration.updated`** — producer: the calibration UI (an operator):
`{ "camera_id", "kind", "revision", "by", "note" }`

`kind` is `"homography" | "zone_map" | "reader_map" | "privacy_mask"` — the last
being the opt-out polygon of `privacy.md`, whose pixels are masked before any
model runs, which makes this event the audit trail for when masking changed.

The second cache-invalidation event after `session.zones_updated`, and it takes
the same exception as that one: its `event_id` is **random, not derived**. Two
recalibrations of the same camera are two distinct facts, and an id derived from
`camera_id` would collapse them, silently discarding the second — which here
would mean the log claiming a mask was applied at a time it was not.

**`cost.metered`** — producer: any consumer that spends money:
`{ "kind", "amount", "unit", "detail" }`

`kind` is `"llm_tokens" | "enrichment_credit" | "storage" | "other"` — a closed
set, unlike the event taxonomy, because a kind nobody recognises cannot be
summed into unit economics and would sit in the log looking as though it had
been counted. `unit` is what `amount` counts: `"tokens"`, `"credits"`, or an ISO
currency code when the spend is already money. The reader only totals a currency
figure from events whose unit *is* a currency
(`dashboard/src/lib/roi/cost.ts`) — adding tokens to dollars produces a number
that reads like unit economics and is not.

Emitted through `backend/app/cost.py`, which derives the `event_id` from the
cause of the spend rather than generating one, per the rule below. A random id
here is worse than elsewhere: the double count survives every replay and lands
in the direction that overstates what a client's activation cost.

`occurred_at` is when the work happened, not when the row was written, so a
replayed cost does not attribute last week's spend to today's session.

**`crm.retract`** — producer: the re-anonymiser (`consent-and-identity.md` §5):
`{ "contact_id", "destination", "reason", "dedupe_key" }`

The outbound half of withdrawal. When consent is withdrawn the re-anonymiser
drops the `IDENTIFIED_AS` edge and redacts the `Contact` locally, but the record
already pushed to HubSpot or Salesforce is outside this system — this event is
what tells the CRM adapters to go and retract it. `reason` is
`"consent_withdrawn" | "erasure_request"`; `dedupe_key` mirrors `handoff.lead`
so a retried retraction cannot fire twice.

**This event carries PII** (`contact_id`), the only one of the Phase 3
additions that does. §6 applies to it in full: it must never ride the anonymised
cloud-sync path, and `dashboard/src/lib/contracts/events.ts` lists it in
`PII_EVENT_TYPES` so `isPiiEventType` gates it without every caller remembering.

### Event ids on derived events

Consumers that produce events — the tracker is the first — must **derive** the
`event_id` from the identity of whatever caused it, never generate a random one.
A random id means a replayed input produces a *new* output the log cannot dedupe,
so every dwell is counted twice and the ROI numbers in `roi-framework.md` §2 are
silently wrong. See `backend/app/consumers/ids.py`.

---

## 4. Consumers (the follow-up features are just these)

Each consumer: reads from its cursor, processes idempotently, advances cursor,
dead-letters on repeated failure.

| Consumer | Runs where | Job | Latency need |
|---|---|---|---|
| **Tracker** | edge | detections → tracked anon persons + spatial events | real-time |
| **Graph writer** | edge | events → nodes/edges (`data-model.md`) | near-real-time |
| **Rules engine** | edge | IF/THEN → `rule.fired` (staff prompts, alerts) | **< 3s** |
| **Identity** | edge | on `consent.captured` link anon → contact | seconds |
| **Attribution** | edge/cloud | build LeadHandoff, apply model+window | post-event ok |
| **CRM adapters** | cloud | LeadHandoff → HubSpot/SF/… (retry/replay) | eventually |
| **ROI/report** | edge/cloud | 4-layer metrics, scorecard, PDF | post-event |
| **Cloud sync** | cloud | append-only anonymised replay upload | post-event |
| **Cost meter** | any | sum tokens/credits per session | async |

Adding attribution or a new CRM = **adding a consumer/adapter. The producers,
the prototype UI, and the edge loop are untouched.** This is the whole point.

---

## 5. Offline & replay semantics

1. Edge produces into the local log whether or not the internet exists.
2. Real-time consumers (tracker, graph, rules) run locally → live UI + prompts
   work with no network.
3. Cloud consumers (CRM, cloud sync) simply lag their cursor while offline.
4. On reconnect they replay from `last_seq` in order — **nothing is lost, nothing
   is double-applied** (idempotent upserts + unique `event_id`).
5. A failed CRM push increments `dead_letter.attempts`; after N tries it surfaces
   in the **HITL review screen** for a human to retry/fix.

---

## 6. Privacy invariants on the bus

- Frames/video **never** enter the log — only structured events.
- PII (`contact`) only appears in events **after** a `consent.captured` of the
  required tier (enforced by the identity/CRM consumers — see
  `consent-and-identity.md`).
- Cloud sync uploads are **anonymised**: `Person` nodes carry no PII unless a
  consent basis permits, and even then per-tenant policy can strip it.

---

## 7. Migration from today

The current dashboard has an **in-memory** event bus (`lib/event-bus.ts`) and
in-memory agent rules. The plan:

1. Keep the in-memory bus as the **browser-side** fan-out for live UI.
2. Introduce the **Postgres log** as the durable backend bus (FastAPI).
3. Bridge: perception + backend consumers use the durable log; the browser
   subscribes via WebSocket. Same event shapes → the UI barely changes.

See `roadmap.md` Phase "The Spine" for exact steps and acceptance criteria.
