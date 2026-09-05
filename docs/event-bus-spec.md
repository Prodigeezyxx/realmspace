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
- **Reading by type:** `GET /events` takes `type` and it is **repeatable** —
  `?type=spatial.dwell&type=spatial.zone_enter` returns their union. A row has
  one type, so an intersection would always be empty, and an empty page is
  indistinguishable from "nothing happened". Added for the report's benchmark,
  which scores several past activations and reads seven event types, none of
  them `perception.detection` — almost every row in a day's log, and none of it
  looked at.
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
| `perception.detection` | edge perception | person bbox, conf, frame, camera | tracker, graph, drift |
| `spatial.zone_enter` / `zone_exit` | tracker | anon_id, zone, ts | graph, rules, ROI |
| `spatial.dwell` | tracker | anon_id, zone, duration | graph, rules, ROI |
| `spatial.gaze` | gaze consumer | anon_id, target_id, duration, confidence | graph, ROI |
| `spatial.group` | grouping consumer | group_id, members, size, cohesion, status | graph, rules |
| `spatial.passby` | tracker | anon_id, adjacent (negative signal) | ROI |
| `spatial.occupancy` | occupancy consumer | zone_id, occupancy, capacity, status | rules, `/live` |
| `surface.touched` | touchpoint tablet (`POST /v1/touch/{token}`) | surface_id, kind, touch_id — **no person** | touch consumer, ROI |
| `surface.interaction` | touch consumer (from `surface.touched`), or booth hardware | surface_id, anon_id, kind | graph, ROI |
| `rfid.read` | RFID reader (MQTT/serial) | reader_id, tag_id, ts | identity, graph, ROI |
| `spatial.tagged` | vision × RFID fusion | anon_id ↔ tag_id, confidence | identity, graph, ROI |
| `consent.given` | consent kiosk (`POST /v1/kiosk/{token}/consent`) | consent_id, copy_version, contact — **no person** | kiosk consumer |
| `consent.captured` | capture surface, or the kiosk consumer (from `consent.given`) | tier, basis, copy_version | identity, CRM gate |
| `consent.withdrawn` | anywhere | contact_id | re-anonymiser, CRM retract |
| `identity.resolved` | identity consumer | anon_id ↔ contact | CRM, follow-up |
| `rule.fired` | rules evaluator | rule_id, action, matched | dispatcher, report |
| `rule.staff_prompt` | dispatcher | message, zone_id, priority | `/live` Next-Step surface |
| `rule.screen_swap` | dispatcher | screen_id, content_id | in-room screens |
| `intent.scored` | intent consumer | anon_id, score, band | rules, report, handoff |
| `handoff.lead` | attribution | normalized LeadHandoff | CRM adapters |
| `outcome.recorded` | operator (`POST /v1/outcomes`), later CRM adapters | dedupe_key, stage, value, closed_at | attribution ledger |
| `crm.retract` | re-anonymiser | contact_id, destination, reason | CRM adapters |
| `followup.drafted` | contextual SDR | contact, subject, body, grounded_in | `/followups` review |
| `erasure.requested` | admin (`POST /v1/erasure`) | subject ids, requested_by | erasure consumer |
| `erasure.completed` | erasure consumer | contact_ids, counts | audit |
| `retention.purge_requested` | admin (`POST /v1/retention/purge`) | requested_by | retention consumer |
| `retention.purged` | retention consumer | purged_through, purged_before_seq, counts | audit |
| `insight.generated` | insight agent | text, refs (supporting event ids), window | `/live`, graph |
| `cost.metered` | consumers | tokens/credits/$ | cost telemetry |
| `drift.detected` | drift consumer | camera_id, metric, observed vs baseline | ops |
| `calibration.updated` | calibration UI (operator) | camera_id, kind, revision, masked | drift (baseline reset), perception (mask poll) |
| `session.started` / `session.ended` | operator | session meta | report, sync |
| `session.zones_updated` | `POST /v1/sessions` | zone_ids, zone_count, by | tracker (cache invalidation) |
| `session.config_updated` | `POST /v1/sessions` | changed[] (field, from, to), by | report (states it on its face) |

**Six of these have no producer yet** — `rfid.read`, `spatial.tagged`,
`intent.scored`, `drift.detected`, `calibration.updated`, `crm.retract`. They are
registered here, and their namespaces accepted by `backend/app/schemas.py`,
*before* the phases that emit them (P4 capture, P6 calibration). `rule.fired` was
on that list until the evaluator landed; `rule.staff_prompt` and
`rule.screen_swap` needed no registration at all, the `rule.` namespace having
been accepted since before anything used it — which is the pre-registration
argument working exactly as intended.
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
  "frame_height": 720,
  "camera_id":    "cam-1"         // optional; added in P6, see below
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

`camera_id` was **optional and additive** when the drift consumer arrived
(2026-08-19), which groups on it — a confidence mean averaged across two cameras
describes neither. It is the id `perception/realmspace.py --camera-id` was
started with, and it must match a camera declared on the session or the mask
fetch has nothing to answer.

**Since 2026-08-21 it also decides who a person is.** Consumers key a visitor on
`camera_id/anon_id`, not on `anon_id` alone (`backend/app/consumers/ids.py`,
`person_key`). ByteTrack numbers people per process and one process runs per
camera, so every camera calls its first visitor `P-001`; keyed on the bare id,
two cameras' first visitors were one person with merged dwells and nothing
raised. Every `spatial.*` payload that names a person therefore carries the namespaced
id in its `anon_id`, and `(:Person).anon_id` is the namespaced one too.
(`spatial.occupancy` names no person — see its payload below.)

Still optional, on two conditions:

- **Absent means the bare id.** Every detection logged before Phase 6 lacks the
  field and spatial event ids are derived from this value, so the fallback is
  what makes replaying that history reproduce its original ids rather than a
  parallel set of duplicates. Drift's `"unattributed"` bucket exists for the
  same reason and stays as it is.
- **Absent is refused when it could be ambiguous.** On a session declaring two
  or more cameras, a detection with no `camera_id` is dead-lettered by the
  tracker naming the camera set, because there is no way to tell which camera's
  `P-001` it is and guessing merges two people. One camera or none, it is
  accepted exactly as before.

A zone gained an owning `camera_id` in the same change, for the other half of
the same problem: a polygon is normalized 0..1 *within one frame*, so scoring a
`cam-2` centroid against `cam-1`'s polygons puts a visitor in a part of the
booth they were never in. A zone with no owner belongs to every camera, which is
what every zone drawn before this is.

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

**`spatial.occupancy`** — producer: `consumers/occupancy.py`:
`{ "zone_id", "zone_name", "occupancy", "capacity", "status", "at" }`

A zone reached the capacity its operator set, or dropped back below it.
`status` is `"over" | "cleared"`, `occupancy` is how many people were inside at
the transition that crossed the line, and `capacity` is the number that was
crossed — carried on the event rather than looked up by a reader, so a rule
firing or a report rendered later shows the threshold that was actually applied.

**One event per crossing, not per arrival.** The fourth person into a
three-person zone produces this; the fifth, sixth and seventh produce nothing.
That is the same refusal `spatial.gaze` and `spatial.group` make — a signal that
fires on the common case looks like coverage — and downstream it is what keeps a
staff prompt from being raised nine times in a minute and taught to be ignored.

**A zone with no capacity produces nothing, at any count.** `capacity` is
optional on `Zone` (`data-model.md`) and blank means the operator did not set a
threshold, which is not the same as setting a large one.

The only `spatial.*` payload with **no `anon_id`**, and deliberately: it is a
statement about a zone at a moment, not about a person, and naming whichever
visitor happened to trip the line would attach a crowding alert to them. It is
anonymous for the same reason `rule.staff_prompt` is.

Its `event_id` derives from `("occupancy", tenant, session, zone_id, status,
seq)` — the seq of the transition that crossed the line — so a replay
re-derives it and the bus dedupes. The occupant set behind it is **recomputed
from this log by event time** rather than accumulated in memory, which is what
makes that true after a restart: see the consumer's docstring, and §5.

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

**`session.config_updated`** — producer: `POST /v1/sessions` (an operator):

```json
{
  "changed": [
    { "field": "engaged_threshold_seconds", "from": 30, "to": 60 }
  ],
  "by": "u_op"
}
```

**Only the scoring rules, and only once the floor has been measured.**
`roi-framework.md` §5 asks for the engagement threshold, the attribution model
and its window to be agreed with the client *before doors open*, "so the ROI
number is pre-agreed and un-arguable afterwards". Everything else on a session
is a correction — the activation cost that is final only once the build is paid
for, the client's own influenced-revenue figure that arrives weeks later, a
mistyped venue — and none of those produces this event.

Changing a scoring rule after visitors have been counted is allowed, because an
operator who typed 30 seconds and meant 60 must be able to fix it, and the
alternative is `psql`. What is not allowed is doing it silently: this event is
what lets `/report` say a rule moved after N events were recorded, which is the
same choice the scorecard makes between an absence and a zero.

"Measured" is `perception.detection` or `spatial.zone_enter`, not "any event on
this session" — the endpoint appends a `session.zones_updated` on every save, so
any second save would otherwise look like an activation that had run.

Its `event_id` is **random, not derived**, for `session.zones_updated`'s reason:
two corrections to the same threshold are two decisions, and one id would
discard the second. `field` is the snake_case name of a `SessionConfigIn` field,
like every other producer payload here; the dashboard turns the three of them
into words a person reads.

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

**`spatial.group`** — producer: `consumers/grouping.py`:
`{ "group_id", "members", "size", "cohesion", "zone_id", "status" }`

Who came with whom. The type has been in this table since Phase 1 and had **no
producer until 2026-08-21**, alongside a `(:Group)` node constrained in the graph
since migration 001 and a worked example query in `data-model.md` that returned
nothing for the whole life of the project. Pinned properly now that something
emits it.

`status` is `"formed" | "changed" | "dissolved"` — one event type rather than
three, the same shape as the `reason` the tracker puts on a visit ending. A
group is a thing with a life, and a consumer that saw only formations would show
groups that broke up hours ago.

`group_id` derives from the founding membership and the moment it formed, and
**never changes afterwards**. A group that gains a fourth member is the same
group with one more person in it; deriving the id from current membership would
give a family two histories and make the first look as though it dissolved the
instant a friend caught up.

`cohesion` is 0..1: the share of shared observations in which the members were
actually together, averaged over the member pairs that were ever seen at the
same time. Pairs never observed together — the two ends of a straggling group —
are left out rather than counted as zero, which would punish exactly the large
groups that are hardest to detect.

`zone_id` is present **only when every member is in the same zone**. A group
straddling a boundary is genuinely not in a zone, and naming one would put four
people somewhere two of them are standing outside.

**What makes this hard, recorded here because the obvious implementation is
wrong.** Proximity is not company: three strangers queueing at a popular zone
are within a metre of each other for minutes, and a detector built on
distance-and-time reports every queue as a family and every busy zone as one
giant group. So a pair has to clear one of two tests — **co-movement** (their
shared midpoint travelled a real distance while they stayed together) or **joint
arrival and departure** (they entered a zone within seconds of each other and
left within seconds of each other). The first separates walking the floor
together from standing in one spot; the second catches the family who sit at a
table and never move, and is failed by a queue for the reason it is a queue: its
members join at different times and are served in order.

Groups are the connected components over confirmed pairs, not cliques — two
people together and one of them with a third is a group of three, which is how
people actually walk.

**`drift.detected`** — producer: perception telemetry:
`{ "camera_id", "metric", "observed", "baseline", "window_seconds", "severity" }`

CV drift is on the risk register (`roadmap.md`) with calibration UI + drift
telemetry as the mitigation. `metric` is what moved, and both `observed` and
`baseline` are carried so the event states the comparison it is making instead
of asserting a verdict someone later cannot check. `severity` is
`"warn" | "critical"`.

About the *camera*, not a visitor, but session-scoped like everything on the bus:
drift matters exactly when it silently degrades a session's numbers, and finding
that out means being able to read it alongside that session's events.

**Re-pinned when the producer landed (2026-08-19).** This paragraph listed three
metrics — `detection_rate`, `confidence_mean`, `track_length`. The producer
(`backend/app/consumers/drift.py`) emits the second and the third and
**deliberately not the first**, and the reason belongs in the contract rather
than only in the consumer.

`detection_rate` falls for two completely different reasons: the model got
worse, or the room emptied. Nothing in the system can tell them apart, and a
booth is empty most of the time — so a detector built on rate fires every
lunchtime, and a panel that cries wolf daily is worth less than no panel,
because it looks like coverage. The other two are **per-detection statistics**,
computed from samples, so a quiet window contributes no samples rather than a
low reading.

- `confidence_mean` — the model's own confidence in the people it did find.
- `track_length` — measured as the share of visits ending in a dropout rather
  than a move, which the tracker already stamps on every `spatial.zone_exit`.

The type still accepts `detection_rate`: a producer that can separate
degradation from occupancy — a second sensor, an occupancy feed — would be
emitting a different and better-founded measurement under the same name, and
the contract should not have to change for it.

Two fields the producer adds beyond this list, both additive: `window`, the
`{from, to}` of the fixed contiguous window the reading covers, which the
consumer reads back to know which windows it has already written; and the
`camera_id` grouping, which is why `perception.detection` now carries one.

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

**Built 2026-08-19, for `privacy_mask` only.** `backend/app/routers/calibration.py`
refuses the other three kinds **with the reason** rather than accepting an event
nothing reads:

- `zone_map` → 409, pointing at `POST /v1/sessions`, which already writes zone
  geometry and already appends `session.zones_updated`. Two writers would give
  the system two ideas of what the current zones are.
- `homography` → 501. Zones are normalized image coordinates end to end and
  nothing reads a floor-plan transform, so the event would record a calibration
  that was never applied.
- `reader_map` → 501. `rfid.read` is a registered namespace with no producer.

The payload carries one field beyond this list: `masked`, whether the polygon
was set or cleared, so a reader of the log can tell "masking started here" from
"masking stopped here" without holding the graph's current state. The **polygon
itself is deliberately absent** — the log is replayed and exported, and a
booth's sensitive geometry does not need to be in every copy of it for the event
to do its job.

It also has a second reader now: `consumers/drift.py` treats a recalibration as
the start of a new measurement epoch. Masking pixels changes what the model
sees, so a drift baseline from before the mask would report the operator's own
correct action as a fault.

**`cost.metered`** — producer: any consumer that spends money:
`{ "kind", "amount", "unit", "detail" }`

`kind` is
`"llm_tokens" | "action_unit" | "enrichment_credit" | "storage" | "other"` — a
closed set, unlike the event taxonomy, because a kind nobody recognises cannot
be summed into unit economics and would sit in the log looking as though it had
been counted. `action_unit` is one dispatched rule action, added when the Phase 3
dispatchers landed and named rather than folded into `other`: actions are one of
the two spends the roadmap's cost line calls out, and a tile labelling it
`other` tells an operator nothing about what their booth is spending on. `unit` is what `amount` counts: `"tokens"`, `"credits"`, or an ISO
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

**`rule.fired`** — producer: the rules evaluator (`backend/app/consumers/rules.py`):

```json
{
  "ruleId":      "r_entry_crowd",
  "ruleName":    "Entrance crowding → ping ops",
  "triggerType": "spatial.dwell",
  "triggerSeq":  418,              // the event that caused it
  "condition":   { "type": "threshold", "count": 5, "windowSec": 30 },
  "action":      { "type": "slack", "channel": "#ops", "message": "5 at entrance" },
  "matched":     { "observed": 5, "countedBy": "people", "windowSec": 30 }
}
```

The rule document, per `adr/002-rule-spec.md`, plus what it matched on. Three
fields are load-bearing beyond the obvious:

- **`action` is copied in, not looked up.** The dispatcher acts on the document
  *as it was when the rule matched*, so an operator editing a rule between the
  firing and the dispatch has not retroactively changed what their booth decided
  to do. ADR-002's second reason for rules-as-data — "a rule must be inspectable
  after it fires … reading the document as it was" — is only true of the log if
  the log carries it.
- **`triggerSeq`** answers "why this moment?" in one lookup, and is what the
  cooldown check orders on. It has to be: a firing is appended *after* its cause,
  so ordering firings by their own `seq` puts every one of them ahead of the
  event that would be asking about it, and cooldown silently never applies.
- **`matched.countedBy`** is `"people"` or `"events"`. A threshold counts
  distinct `anonId`s where the payload carries them, because one visitor leaving
  and re-entering five times is five events and one person. Which was used
  changes what `observed` means, so it is stated rather than assumed.

`event_id` is derived from `(consumer, tenant, session, ruleId, cause)`, where
the cause is `triggerSeq` — or, for a `none` condition, the silence that closed.
Per the rule below, and doubly so here: the dispatcher keys its idempotency on
this id, so a random one posts to Slack twice.

**`rule.staff_prompt`** / **`rule.screen_swap`** — producer: the dispatcher
(`backend/app/consumers/dispatch.py`):

```json
{ "message": "Greet the group at the entrance", "zoneId": "z_entry",
  "priority": "normal", "ruleId": "r_entry_crowd", "ruleName": "…" }

{ "screenId": "scr_main", "contentId": "reel_b",
  "ruleId": "r_mirror", "ruleName": "…" }
```

The two rule actions that act on the room rather than on an outside service. They
go on the bus rather than straight to the WebSocket hub for the reason §5 gives
about every other event: a tablet or screen that reconnects mid-session catches
up from its cursor, and it can only do that if the prompt was in the log. A
missed prompt is a missed message; a missed screen swap leaves a display stuck on
the wrong content for the rest of the activation.

They are separate types rather than something the client derives from
`rule.fired` by inspecting its action. A browser that had to know which actions
are staff prompts would be a second implementation of the rule spec, which is the
split-brain ADR-002 exists to end — a client subscribes to the one type it
renders and knows nothing about rules.

Both are **anonymous**: a prompt is about a zone and a moment, never a visitor.

**`crm.retract`** — producer: the re-anonymiser (`consent-and-identity.md` §5):
`{ "contact_id", "destination", "reason", "dedupe_key" }`

The outbound half of withdrawal. When consent is withdrawn the re-anonymiser
drops the `IDENTIFIED_AS` edge and redacts the `Contact` locally, but the record
already pushed to HubSpot or Salesforce is outside this system — this event is
what tells the CRM adapters to go and retract it. `reason` is
`"consent_withdrawn" | "erasure_request"`; `dedupe_key` mirrors `handoff.lead`
so a retried retraction cannot fire twice.

*Added 2026-08-14:* the consumer is `consumers/crm_retract.py`, and
`destination: "all"` now resolves to something — `crm_link` (migration 0008)
records where each contact was actually pushed, so "all" means the destinations
that received this one rather than every CRM in the world. A contact with no
link produces no work and no dispatch: nothing was pushed, or a replay is
finding it already undone.

**This event carries PII** (`contact_id`), the only one of the Phase 3
additions that does. §6 applies to it in full: it must never ride the anonymised
cloud-sync path, and `dashboard/src/lib/contracts/events.ts` lists it in
`PII_EVENT_TYPES` so `isPiiEventType` gates it without every caller remembering.

**`consent.given`** — producer: a consent kiosk, via
`POST /v1/kiosk/{token}/consent`:

```json
{
  "consent_id":   "c_01J...",         // minted by the browser before the copy is shown
  "tier":         "T2",               // read off the activation, never from the request
  "basis":        "explicit_optin",   // likewise
  "copy_version": "consent-en-2026-09",  // likewise — see below
  "captured_by":  "the plinth by the door",  // the kiosk's own label
  "source":       "kiosk",
  "surface_id":   "sf_kiosk_entry",   // which kiosk, off the token's row
  "at":           "2026-09-03T10:04:02Z",
  "contact": {                        // the PII, as consent.captured carries it
    "email":   "sam@example.com",
    "name":    "Sam Rivera"
  }
}
```

**No `anon_id`, for `surface.touched`'s reason.** A plinth has no camera. Which
visitor gave this consent is `consumers/kiosk_consent.py`'s question, answered
from zone occupancy at the moment it was given, and it emits the
`consent.captured` below with the answer. The two events are one conversation:
the raw fact the surface can attest to, and the derived one everything
downstream reads.

**The tier and the copy version are read off the activation, not received.** A
surface that could name its own tier could record a T3 for somebody who was
shown the T1 wording, and the consent record is the evidence a disputed
withdrawal is settled by. `GET /v1/kiosk/{token}` returns both, so the wording
displayed and the version recorded cannot drift apart.

**Unlike a tap, a consent is never refused for being unattributable.** A tablet's
tap is a claim about somebody the device cannot observe, so
`consumers/touch.py` declines to guess. A consent is a claim the visitor makes
about themselves; the spatial path is the second half. What an unattributable
consent loses is the path, not the permission.

**This event carries PII** (`contact`) and is listed in `PII_TYPES` and
`LINKING_TYPES` in `backend/app/erasure.py`, and in `PII_EVENT_TYPES` in
`dashboard/src/lib/contracts/events.ts`. It is where a kiosk consent's email
first appears — and, for the consents no zone could attribute, the only place it
appears — so an erasure that walked only from `consent.captured` would leave the
name behind.

**`consent.captured`** — producer: a capture surface (badge, QR, kiosk, form),
via `POST /v1/consent`:

```json
{
  "consent_id":   "c_01J...",         // stable; the id of the ConsentEvent node
  "anon_id":      "P-012",            // the track this consent is about
  "tier":         "T1",               // 'T1'|'T2'|'T3' — consent-and-identity.md §2
  "basis":        "explicit_optin",   // 'explicit_optin'|'contract'|'legitimate_interest'
  "copy_version": "consent-en-2026-08",  // the exact wording shown, versioned
  "captured_by":  "kiosk-entrance",   // the surface or operator that captured it
  "source":       "qr",               // 'badge'|'qr'|'kiosk'|'form'|'manual'
  "captured_at":  "2026-08-13T10:04:02Z",
  "expires_at":   null,               // optional; null = end of the activation
  "contact": {                        // the PII itself — see below
    "email":   "sam@example.com",
    "name":    "Sam Rivera",
    "company": "Example Ltd",
    "title":   "Head of Ops"
  }
}
```

**`copy_version` is the load-bearing field, not the tier.** A tier says what
somebody is being asked for; the copy version says what they actually read
before agreeing, and it is the only thing that settles a withdrawal argued after
the fact. It is required for that reason — a capture surface that cannot say
which wording it displayed has not really captured consent.

**`contact` is optional and is the only PII in the payload.** A T1 capture that
is a badge scan may carry nothing but an `anon_id` and a tag, with the details
arriving from the badge registry later. Everything outside `contact` is
anonymous and is what the audit trail is made of, which means a deployment can
keep the consent record after erasing the person.

**`anon_id`, not `contact_id`.** At capture time there is no Contact yet —
creating one is the identity consumer's job, and it happens *after* the gate is
checked, never as part of asking for permission.

**`consent.withdrawn`** — producer: anywhere (a kiosk, an operator, an inbound
erasure request):
`{ "consent_id", "contact_id", "anon_id", "reason", "withdrawn_at" }`

`reason` is `"visitor_request" | "erasure_request" | "operator"`. Carrying all
three identifiers is deliberate redundancy: the withdrawal has to work when the
person can only be identified by one of them — a visitor at a kiosk knows their
email, an erasure request names a Contact, and a mid-session withdrawal at the
capture surface knows only the track it is standing in front of.

**Both events carry PII** and are listed in `PII_EVENT_TYPES` alongside
`crm.retract`. `consent.captured` carries it in `contact`; `consent.withdrawn`
carries `contact_id`, which is PII by §6's own definition — an identifier that
resolves to a person is not made anonymous by being opaque.

**`handoff.lead`** — producer: the attribution consumer
(`backend/app/consumers/attribution.py`). The normalized `LeadHandoff/v1` from
`integrations.md` §2, which specifies the shape; what is pinned here is the
sourcing of the three fields that spec asks for and does not say where to get.

```jsonc
{
  "schema": "realmspace.lead_handoff/v1",
  "stage":  "identified",              // 'identified' | 'final' | 'anonymous'
  "tenant_id": "t_...",
  "activation":     { "id", "name", "venue", "city", "started_at", "ends_at" },
  "contact":        { "id", "email", "name", "company", "title", "source" },
                     // omitted entirely on an anonymous handoff — see below
  "spatial_intent": {
    "zones_visited":        ["Entry", "Pod"],   // order first entered, deduped
    "top_dwell_zone":       "Pod",              // totalled per zone, not longest stay
    "dwell_seconds_total":  160.0,
    "surfaces_engaged":     ["AR Mirror"],
    "attention_score":      250.0,
    "attention_basis":      "weighted_dwell_seconds",
    "funnel_depth_reached": 2,
    "path_summary":         "Visited Entry 40s, then Pod 2m. Engaged AR Mirror.",
    "lead_score":           74,                 // or null — never 0 when unknown
    "lead_score_basis":     "spatial/v1",
    "lead_score_components": ["dwell", "funnel", "surfaces"],
    "surfaces_available":   2,
    "max_funnel_order":     2
  },
  "consent":     { "tier", "basis", "copy_version", "captured_at" },
  "roi_context": { "attribution_model", "attribution_window_days",
                   "activation_cost_share" },
  "dedupe_key":  "t_acme:sam@example.com",      // 'tenant:email|anon_id'
  "anon_id": "P-012", "emitted_at": "...", "event_seq": 84213
}
```

**Two stages, one lead.** A handoff is emitted on `identity.resolved` with the
path so far — a trade-show lead is worth most while the visitor is still on the
floor — and again on `session.ended` with the complete one. They carry **the
same `dedupe_key`**, which is every adapter's upsert key, so the second updates
the lead rather than creating one; and **different `event_id`s**, derived with
the stage in the key, because the bus dedupes on `event_id` and an id derived
from the contact alone would silently discard the complete path.

**A third stage: `anonymous`** *(added 2026-08-17)*. `integrations.md` §2 always
allowed a handoff with no `contact`, "carrying spatial_intent for aggregate ROI".
It is emitted at `session.ended` only, one per person with no live
`IDENTIFIED_AS` edge — which covers both somebody who never consented and
somebody who consented and then withdrew, since a withdrawal returns a person to
the anonymous path. `dedupe_key` is the `anon_id` half of `tenant:email|anon_id`,
and the event id is derived from the track rather than from a contact that does
not exist.

`contact` and `consent` are **omitted**, not set to objects of nulls. "Nobody was
named here" and "these details are blank" are different statements, and a
destination reading the second would create an empty contact.
`activation_cost_share` is null: the denominator is the leads that divided the
cost, and giving a slice to somebody who never consented would count the same
money twice.

**Off unless the operator turned it on** (`anonymous_handoffs` on the session
config), which is the opposite of every other setting there. A busy day is several
hundred of them and they reach the same destinations an identified lead does.
They reach the log, the deployment webhook, the pull API and any bring-your-own
hook the tenant has connected — not the CRMs. `consumers/crm_delivery.py` offers
one only to destinations whose adapter declares `capabilities()["anonymous"]`,
which a hook does and a CRM does not: there is no record to create for somebody
who was never named, and a claim per visitor per CRM recording that nothing was
sent is noise `/ops` does not need.

**`attention_score` is in seconds.** `integrations.md` §2 illustrated it as
`0.82`, which reads as a ratio. `roi-framework.md` §2 defines dwell-weighted
attention as `Σ(dwell × weight)` — a quantity — and no denominator anywhere in
the framework turns one into the other. Normalising would mean inventing that
denominator, so the value is the definition and `attention_basis` names the
unit. The doc's example was corrected rather than the number bent to fit it.

**`lead_score` is a stated formula, not a model.** Three ratios of what the
visitor did to what the activation offered — dwell against the session's
engagement threshold, funnel depth against the deepest configured zone,
surfaces used against surfaces present — weighted 0.5/0.3/0.2. A component the
operator never configured is **dropped and the rest renormalised**, not scored
zero, and `lead_score_components` says which contributed. With nothing
computable the score is `null`, never `0`: a CRM sorting by score would
otherwise rank "we could not tell" alongside "not interested".
`lead_score_basis` travels with it for the same reason `spatial.tagged` carries
`method` — a stored score whose derivation is unknown cannot be re-judged. The
version is never redefined in place; a new formula is a new version.

**`activation_cost_share` is null at the `identified` stage.** Its denominator
is how many leads the activation produced, which is not known while the doors
are open — a share against a partial count changes every time somebody scans a
badge. Computed only at `final`.

**This event carries PII** (`contact`) and is in `PII_EVENT_TYPES`. A handoff is
built only behind a live, non-withdrawn consent: the attribution consumer reads
the `IDENTIFIED_AS` edge, which the re-anonymiser deletes on withdrawal, so a
replayed `identity.resolved` for somebody who has since withdrawn builds
nothing.

**`outcome.recorded`** — producer: an operator via `POST /v1/outcomes`, and
later the CRM adapters:

```jsonc
{
  "outcome_id":  "o_0001",              // caller-supplied; the idempotency key
  "dedupe_key":  "t_acme:sam@example.com",  // the join back to handoff.lead
  "stage":       "won",                 // 'won' | 'lost' | 'open'
  "value":       5000.0,                // null when not told — never 0
  "currency":    "USD",
  "closed_at":   "2026-09-01T10:00:00Z",   // required for won/lost
  "source":      "operator",            // 'operator' | 'crm'
  "external_ref": "hubspot:deal:4471",
  "recorded_by": "u_ops"                // who said so
}
```

**Nothing in this system had ever defined an outcome.** `data-model.md`'s node
list stopped at `Frame`, and every attribution claim in `roi-framework.md` — the
whole of Layer 4 — rested on it. Defined here so the ledger has something to
reconcile against and the CRM adapters have somewhere to write.

`dedupe_key` is the join, deliberately rather than a contact id: it is the key
`handoff.lead` carried out to the destination and the one every adapter upserts
on, so an outcome names its lead in the vocabulary the CRM already speaks.

**`closed_at` is required for `won` and `lost`** because the attribution window
is measured against it, and an undated close cannot be judged inside or outside
one. `open` needs none — an opportunity still in play is most of a B2B pipeline
at any moment, and forcing it to won/lost would make the ledger claim
resolutions that have not happened.

**These are the client's figures, not measurements**, the same as
`revenue_influenced` and `qualified_leads` on the session config. `recorded_by`
and `source` are on the row because who said so is part of what an auditor reads
the ledger to find out.

**Carries PII**: `dedupe_key` embeds an email. It is in `PII_EVENT_TYPES`.

**`insight.generated`** — producer: the insight agent
(`backend/app/consumers/insights.py`) *(added 2026-08-18)*:

```jsonc
{
  "insight_id":   "…",
  "text":         "2 people moved through 4 zone entries in the last 5 minutes. \
Product Pod held attention longest, at 300s of dwell.",
  "refs": [                        // the events this claim rests on
    { "seq": 5,  "event_id": "…" },
    { "seq": 6,  "event_id": "…" }
  ],
  "measurements": { "people": 2, "zone_entries": 4, "top_zone": "Product Pod",
                    "top_zone_seconds": 300.0, "zones": [...], "surfaces": [...] },
  "window":       { "from": "…", "to": "…", "minutes": 5 },
  "generated_by": "rule",          // 'llm' | 'rule' — data-model.md's (:Insight)
  "basis":        "deterministic",  // or the provider that wrote the sentence
  "truncated":    false
}
```

**`refs` is the reason this event has a payload at all.** An insight without them
is an assertion a reader has to take on trust, which is the failure mode the
report's invented `1,287 visitors` had — it reads as authority and cannot be
checked. They are the *supporting* events, not the window's traffic: an insight
about Product Pod cites the Product Pod dwells that sum to the number it quotes.
`GET /v1/insights/{seq}/sources` resolves them, and a ref that no longer resolves
is reported as `missing` rather than dropped.

This is why the digest is built from the **log** rather than the graph. The graph
holds current state and can tell you a zone's average dwell; it cannot tell you
which events say so, because there is no id to carry.

**Windows are fixed and contiguous** from the session's first event —
`[first, first+N)`, `[first+N, first+2N)` — and the trigger is **event time**,
never the clock. A wall-clock trigger would give a replayed log different windows
from identical events. The consequence is worth stating: insights advance with
the floor, not with the wall, and a session with no traffic for an hour produces
no insights about that hour.

`occurred_at` is the window's **end**, not the time of whichever event crossed
the boundary — an insight is about a period, and dating it by its trigger would
make its position in the log depend on traffic.

**Anonymous**, and by construction rather than by policy: the digest reads only
`spatial.*` and `surface.interaction`, so no consent, handoff or contact can
reach it. It is in `ANONYMOUS_EVENT_TYPES` and may travel the anonymised
cloud-sync path (§6).

**`surface.touched`** — producer: a tablet at a touchpoint
(`backend/app/routers/touch.py`) *(added 2026-08-31)*:

```jsonc
{
  "surface_id":    "sf_ar_mirror",  // from the token, never from the request
  "surface_label": "AR Mirror",     // the operator's name for it
  "kind":          "tap",
  "at":            "2026-08-31T14:02:11.400Z",
  "touch_id":      "9f1c…"          // minted by the tablet when the finger lands
}
```

**No `anon_id`, and that absence is the design.** A tablet has no camera and
cannot know who pressed it. Naming the visitor is `consumers/touch.py`'s job,
from zone occupancy at `at`, and it **refuses more often than it succeeds** — a
tap with two people in the zone is counted and not attributed, because choosing
between them would put an observation on an append-only log that nothing made.

The three ids that matter — tenant, session, surface — come off the token row
(migration 0015) and never from the request body, so one tablet cannot post as
another touchpoint. `touch_id` is minted when the finger lands rather than when
the request is sent, which is what makes a retry over venue wifi one tap instead
of two; the `event_id` derives from it.

`surface_label` travels with the tap the way `zone_name` travels with every
spatial event, and for the same reason: the readers of a payload do not all have
the graph. Without it `llm/digest.py` writes *"sf_ar_mirror was used 3 times"* at
a client.

**Anonymous**, by construction: there is no field here that could name anybody.
Its derived `surface.interaction` is not — it carries an `anon_id` like every
other spatial event.

**Usage is counted from `surface.touched`, not from `surface.interaction`.** An
attributed tap is on the log as *both*, so a reader counting both doubles a
touchpoint's tally; a reader counting only the second reports how identifiable
the crowd happened to be. The rule, in `computeScorecard` and `llm/digest.py`
alike: **count every `surface.touched`, and count a `surface.interaction` only
when it carries no `touch_id`** — an interaction with none had no tap behind it
and came from booth hardware posting directly, which this taxonomy has always
allowed.

**`followup.drafted`** — producer: the contextual SDR
(`backend/app/consumers/sdr.py`) *(added 2026-08-18)*:

```jsonc
{
  "contact":     { "id", "email", "name" },   // PII
  "dedupe_key":  "t_acme:sam@example.com",
  "anon_id":     "P-012",
  "subject":     "Following up from Pavilion No.7",
  "body":        "Hi Sam,\n\nThank you for visiting…",
  "basis":       "deterministic",   // or the provider that wrote it
  "consent":     { "tier": "T2" },
  "grounded_in": {                  // what the draft was allowed to reference
    "zones_visited": ["Entry", "Pod"],
    "top_dwell_zone": "Pod",
    "dwell_seconds_total": 160.0,
    "surfaces_engaged": ["AR Mirror"]
  },
  "sent":        false,             // stated, never inferred from an absence
  "drafted_at":  "2026-08-18T18:00:00Z"
}
```

**Emitted for the `final` handoff only**, and only at consent **T2 or above**
(`consent-and-identity.md` §2 puts "personalised follow-up (SDR)" in T2's own
column). A draft built from the `identified` stage would name the entry zone and
nothing else, because the visitor has not finished walking.

**`grounded_in` is the point of the payload.** It is the exact set the draft was
permitted to reference, so a reviewer checks a sentence against it rather than
trusting it — the only way to catch a model that invented a conversation. We
measured where somebody walked; we did not hear a word they said.

**Nothing sends it.** There is no email provider and no send endpoint;
`GET /v1/followups` reports `sendingSupported: false` on every response. `sent`
is on the payload so a surface never infers "not sent" from a missing field.

**Carries PII**, and unusually it carries PII twice: the `contact` object names
them and the `body` quotes them. An erasure redacts both — redacting the envelope
and leaving the letter is not redaction. It is in `PII_EVENT_TYPES`.

**`erasure.requested`** — producer: an admin via `POST /v1/erasure`
*(added 2026-08-17)*:

```jsonc
{
  "contact_id":   "ct_...",   // whichever the person asking can be named by;
  "consent_id":   "c_0001",   // at least one is required, all three allowed
  "anon_id":      "P-012",
  "requested_by": "dpo@acme.example",  // a person, and accountable for it
  "note":         "ticket GDPR-41",    // never the subject's own details
  "requested_at": "2026-08-17T09:00:00Z"
}
```

`POST /v1/erasure` appends a `consent.withdrawn` with
`reason: "erasure_request"` beside it, so the whole existing withdrawal path runs
first — the link is dropped, the graph Contact redacted, and the record retracted
from every CRM that received it. This event asks for the part that path cannot
reach: **the log itself**.

The consumer (`consumers/erasure.py`) refuses to act until the retraction has
actually landed, and both refusals are ordering conditions rather than
politeness. Erasing the Contact first would delete the record the re-anonymiser
reads to build `crm.retract`, so no retraction would ever be emitted and the copy
in the client's CRM would stay — an erasure that reports success and leaves the
data where it matters most.

**Carries PII**: it names a contact and it names the person who asked. It is in
`PII_EVENT_TYPES`.

**`erasure.completed`** — producer: the erasure consumer:
`{ "request_event_id", "contact_ids", "events_redacted", "contacts_erased", "completed_at" }`

The receipt, and deliberately ids and counts only. It is appended to the same log
the erasure has just rewritten, so a receipt quoting what it removed would put it
straight back. **Not PII.**

#### What an erasure does to the log

This is the one thing in the system that updates an `event_log` row, and §2's
append-only guarantee has to be restated around it rather than quietly bent.

Only `payload` is rewritten, on the events that actually carry a name —
`consent.captured`'s `contact` object, and `handoff.lead`'s `contact` and
`dedupe_key`. `seq`, `event_id`, `type`, `occurred_at` and `recorded_at` are
untouched; no row is deleted and no sequence number is reused. **A replay after an
erasure reproduces the same events in the same order, with a name missing from a
few of them.** That is weaker than "the log never changes", and it is the
guarantee Article 17 leaves us.

`redacted_at` (migration 0009) is a column rather than a payload key, because the
payload is the thing being rewritten and "what did this erasure touch" should not
be a JSON scan.

What survives is the evidence: the consent with its tier, basis and copy version,
the withdrawal, the identification and the retraction. None of it names anybody,
and together they are what a disputed erasure would be settled by.

**`identity.resolved`** — producer: the identity consumer:
`{ "anon_id", "contact_id", "consent_id", "tier", "via", "at" }`

The record that the gate was passed and the link drawn. `via` mirrors the
`IDENTIFIED_AS` edge's own property (`'badge'|'qr'|'kiosk'|'form'|'manual'`), and
`consent_id` is carried so a downstream consumer can re-check the justification
without re-deriving it — a CRM adapter refusing anything below T2
(`consent-and-identity.md` §3) should not have to go looking for the reason it is
allowed to act.

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
| **Kiosk consent** | edge | `consent.given` → `consent.captured`, naming the visitor from zone occupancy | seconds |
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
