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
| `consent.captured` | capture surface | tier, basis, copy_version | identity, CRM gate |
| `consent.withdrawn` | anywhere | contact_id | re-anonymiser, CRM retract |
| `identity.resolved` | identity consumer | anon_id ↔ contact | CRM, follow-up |
| `rule.fired` | rules engine | rule_id, action | action consumers |
| `handoff.lead` | attribution | normalized LeadHandoff | CRM adapters |
| `insight.generated` | LLM/agents | text, refs | graph, dashboard |
| `cost.metered` | consumers | tokens/credits/$ | cost telemetry |
| `session.started` / `session.ended` | operator | session meta | report, sync |

### Payloads pinned so far

The gist column above is not enough to write a producer against. Types are
pinned here as they get implemented.

**`perception.detection`** — producer: edge perception:

```json
{
  "person_id":    "P-012",        // from ByteTrack; session-scoped, never reused
  "bbox":         [x1, y1, x2, y2],   // PIXELS, xyxy (what YOLO returns)
  "confidence":   0.91,
  "frame_width":  1280,           // required — see below
  "frame_height": 720
}
```

`frame_width` / `frame_height` are **required**. Bounding boxes arrive in pixels
but `Zone.polygon` is normalized 0–1, so without the frame size there is no way
to tell which zone a detection is in. The tracker dead-letters detections that
omit them rather than guessing. (Same normalisation the browser does in
`dashboard/src/skills/zone-detect.ts`.)

**`rfid.read`** — producer: RFID reader bridge. Added for Week 1 task 1.11; the
reader speaks MQTT or serial and a small bridge turns each read into an event.
`tag_id` is the badge, not a person — linking it to a `Person` is the identity
consumer's job and is consent-gated (`consent-and-identity.md`).

> Mirror needed in `dashboard/src/lib/contracts/events.ts`: `rfid.read` is not
> in the `RealmEventType` union yet, so the browser cannot type an event it may
> now receive over the WebSocket.

**`spatial.zone_enter`** — producer: tracker:
`{ "anon_id", "zone_id", "at" }`

**`spatial.zone_exit`** — producer: tracker:
`{ "anon_id", "zone_id", "at", "entered_at" }`

**`spatial.dwell`** — producer: tracker:
`{ "anon_id", "zone_id", "duration", "started_at", "ended_at", "exceeded_threshold" }`
— `duration` in seconds; `exceeded_threshold` compares it to the per-session
dwell threshold (30s today, matching `agents/definitions/dwell.ts`).

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
