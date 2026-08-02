# RealmSpace — Product Requirements Document v2

> **Watch the room think.**
>
> RealmSpace is the measurement, replay and intelligence layer for physical
> brand experiences — designed and shipped alongside the booths and digital
> experiences we already build. One camera. One laptop. A queryable graph of
> attention, dwell and behaviour, plus a 3D digital twin you can scrub through
> and ask questions of.

---

## 0. North star

**RealmSpace is the intelligence layer that closes the loop on physical brand experiences.**

The MVP proves one thing: that a single laptop with a webcam can capture
real-world spatial behaviour inside an activation we already designed, extract
entities and events, structure them as a graph, and let the agency and client
**query, replay and ask questions** of what happened — in plain English.

**The promise in one sentence:**

*"Plug in any camera. Watch the room think."*

---

## 1. Strategic positioning

### 1.1 Where we actually win

We are not building "Verkada for events". We are not building a generic CV
platform. We are building the *measurement and replay layer* of a vertically
integrated experiential agency offering.

That vertical integration is the moat. We own:

1. **The booth.** We design and fabricate the physical activation. That means
   cameras live where they need to live, sightlines work, zones map cleanly to
   sponsor real estate. No retrofit, no awkward installation.
2. **The digital experience.** We build the AR mirrors, scent quizzes, RFID
   memory walls, lighting cues, and audio reactives that give visitors a reason
   to dwell. Every interactive surface emits its own structured events into
   the same graph — so behavioural data is correlated with experience triggers
   automatically.
3. **The measurement.** RealmSpace.
4. **The deliverable.** A scrubbable 3D digital twin and a beautiful
   client-ready report.

No camera analytics vendor can match this because they don't ship the booth or
the experience. No experiential agency can match this because they don't ship
the measurement.

### 1.2 Ideal customer profile (ICP)

- **Primary buyer (Year 1)** — Heads of brand experience / experiential at:
  - Premium lifestyle and beauty brands deploying activations (Maison Vivienne,
    Glossier, Aesop, Diptyque, Spotify, Nike House of Innovation tier)
  - Experiential agencies with retainer-style brand clients (Jack Morton, MKG,
    Imagination, MOMENT FACTORY-tier)
- **Secondary** — Luxury hotel operators, museums, art fairs, sponsor managers
  at tier-1 sporting and music events.
- **Decision criteria**: ROI proof for the next budget cycle, social/PR
  capture, sponsor reporting, day-2 optimisation.
- **Budget held by**: Marketing, not IT. This is why local-first works as a
  *feature*: IT review is bypassed when the kit shows up in a flight case with
  the booth.

### 1.3 The wedge

Replace the post-event "report" — a PDF of door counts, Instagram screenshots
and gut-feel quotes — with **a queryable 3D twin and a structured ROI brief
within 24 hours of doors closing**. Then charge a measurement line item on
every activation we deliver, plus license the kit to other agencies later.

### 1.4 Three-horizon product strategy

| Horizon | Time | Mode | Goal |
|---|---|---|---|
| **H1 — Service** | Months 0–6 | RealmSpace ships inside every activation Floats XR delivers | Prove the demo loop, refine the platform, build case studies, generate measurement revenue |
| **H2 — Productised service** | Months 6–18 | Sell the kit + dashboard to other agencies as a quarterly subscription | Recurring revenue, multi-agency benchmark dataset |
| **H3 — Platform** | Year 2+ | License the perception engine + graph + twin to camera OEMs, venues, retail chains | Defensible category — "Mixpanel for physical space" |

---

## 2. The problem (re-framed)

Physical brand experiences are the single largest unmeasured marketing spend.
A 3-day pavilion can cost $500K–$3M end-to-end. The deliverables back to the
client are almost always:

- A door count (often hand-tallied)
- Instagram impression estimates
- Anecdotal quotes from staff
- Photos and a sizzle reel

There is no funnel, no per-zone ROI, no proof that the AR mirror was worth
the $80K it cost to install, no answer to "why didn't the bottle wall convert".
Clients are losing patience.

At the same time, the technology to *do this properly* finally exists in 2026:
- Vision-language models (Claude, GPT-4o, Gemini) understand scenes
  zero-shot, eliminating the historical CV pipeline cost.
- Local inference on M-series Macs is sufficient for live tracking.
- Modern graph DBs + LLMs make natural-language queries against complex
  spatial data trivial.

The window is now.

---

## 3. The MVP (Phase 0–3)

A single-laptop, single-camera, local-first prototype that delivers, in this
order:

1. **Capture** — live video → person + object detection + zone events
2. **Structure** — every detection becomes a node in a graph
3. **Visualise** — live dashboard with counter, heatmap, zones, event timeline
4. **Replay** — 3D digital twin of the activation, scrubbable, with anonymous
   avatars walking the recorded paths
5. **Ask** — natural-language input box translates to Cypher, runs against the
   graph, returns a structured answer (number, chart, or subgraph)
6. **Act** — agent rules that fire actions (Slack, webhook, screen change,
   logged insight) when conditions are met
7. **Deliver** — a one-click client-ready PDF report

The MVP runs entirely on the perception laptop. AI reasoning (Cypher
generation, insight summarisation) is the only thing that calls a remote API.

### 3.1 Demo loop (the 5-minute pitch)

1. Open the dashboard. "This is a webcam pointed at this room."
2. Walk around. "Watch me get tracked anonymously."
3. Open the Twin tab. "Here's a 3D replay of every visitor in the last 10
   minutes."
4. Click a visitor. "Follow their path through the booth."
5. Open Ask the Room. *"How long was P-216 in the lounge?"* → answer in <1s.
6. Open Agents. *"When 5 people dwell at the entrance for 30 seconds, ping
   Slack."* Save.
7. Open Report. "This is the client deliverable. 24 hours after the doors
   close, this lands in their inbox."

That's the whole pitch. The MVP **is** the pitch.

---

## 4. Architecture (in this repo)

See [docs/architecture.md](./architecture.md) for the full diagram and stack
decisions. The short version:

```
Camera → Perception (Python / OpenCV + YOLO + ByteTrack + MediaPipe)
       → Event Composer
       → Postgres (events + timeseries) + Neo4j (graph) + Qdrant (embeddings)
       → FastAPI (REST + WebSocket)
       → Next.js dashboard (this app)
```

For the prototype in this repo, the dashboard runs end-to-end on **mocked
data**. The architecture wiring is faithful and every piece of mocked data
maps 1:1 to what the real pipeline will produce.

---

## 5. Data model

See [docs/data-model.md](./data-model.md) for the full schema. The core
nodes:

```
(:Session) (:Person) (:Object) (:Zone) (:Surface)
(:Event) (:Frame) (:Insight)
```

The core relationships:

```
(Person)-[:ENTERED]->(Zone)
(Person)-[:DWELLED_IN {duration}]->(Zone)
(Person)-[:LOOKED_AT {duration, confidence}]->(Object)
(Person)-[:INTERACTED_WITH]->(Surface)
(Person)-[:NEAR {distance, duration}]->(Person)
(Surface)-[:LIVE_IN]->(Zone)
```

This graph is what the "Ask the Room" feature queries. The 3D digital twin
is the visual projection of this graph onto a scale model of the booth.

---

## 6. The 5 product surfaces

### 6.1 Landing page (`/`)

Public-facing site that positions the product for agency/brand buyers. Hero,
the 4-step loop (Booth → Experience → Measure → Twin), three product pillars,
privacy moat, per-activation pricing.

### 6.2 Live dashboard (`/live`)

The operator view. KPIs strip, live annotated camera feed (with bounding
boxes, ID tags, zone polygons), attention heatmap, traffic over the last
hour, live event log, zone counters, LLM-generated insights every 10 minutes.

### 6.3 Digital twin (`/twin`)

The client wow moment. A scale 3D model of the activation. Anonymous avatars
walk the recorded paths. Zones glow. Surfaces emit light when active.
Scrubbable timeline. Playback speed 0.5×–4×. Heatmap overlay toggle. Click a
visitor to follow their path.

### 6.4 Ask the Room (`/ask`)

Plain English input. The query is sent to Claude / GPT-4o along with the
graph schema, translated to Cypher, executed against Neo4j, and the answer
is summarised with an appropriate chart type. Suggested questions sidebar,
schema reference, latency breakdown.

### 6.5 Agents (`/agents`)

Rule-builder. "When [condition] then [action]". Conditions: dwell threshold,
people count, gaze duration, group formation, funnel breach. Actions: Slack,
webhook, screen content swap, log insight to graph. Includes a plain-English
composer powered by the LLM that translates intent → rule.

### 6.6 Report (`/report`)

The client-deliverable. Hero numbers, headline insight, 5-step funnel, top
moments, zone-by-zone breakdown, sponsor exposure ranking, recommendations
for next session. PDF-exportable.

---

## 7. Privacy (the moat)

This is sold as the **first** feature, not the last:

- ❌ No face recognition
- ❌ No biometric storage
- ❌ No cross-session re-identification
- ✅ Anonymous, session-scoped track IDs (P-211, P-212, etc.)
- ✅ Local-first perception — frames never leave the device
- ✅ Auto-purge raw video within minutes (only structured graph survives)
- ✅ Avatar-only replay in the digital twin — clients never see faces
- ✅ Per-pixel masking for sensitive surfaces
- ✅ GDPR / CCPA / UK DPA-aware by construction

See [docs/privacy.md](./privacy.md) for the full deployment-ready
posture document we send to compliance teams.

---

## 8. Go-to-market (Year 1)

See [docs/gtm.md](./gtm.md). Three motions:

1. **Embedded** — RealmSpace ships in every activation Floats XR
   delivers, billed as a measurement line item.
2. **Partner agencies** — 5–10 white-label kit deployments to peer agencies
   on a per-activation revenue share.
3. **Direct brand pilots** — 2–3 hand-picked premium brands run a Pavilion
   tier pilot at a flagship store or activation.

---

## 9. What's real vs. mocked (today)

| Surface | Status |
|---|---|
| Landing page | ✅ Real |
| Live dashboard (UI + chrome) | ✅ Real |
| **Live webcam capture** | ✅ Real — `navigator.mediaDevices.getUserMedia` |
| **Person detection** | ✅ Real — TensorFlow.js + COCO-SSD (mobilenet_v2) running in the browser via WebGL backend |
| **Persistent anonymous IDs** | ✅ Real — custom centroid tracker, in-browser (`src/lib/tracker.ts`) |
| **Live KPI strip + event log** | ✅ Real — derived directly from detector state |
| Heatmap | 🟡 Generated from mock waypoints. Same code accepts live waypoints. |
| Digital twin (3D scene) | ✅ Real React Three Fiber scene; data is mocked but the render is genuine. |
| Twin avatar paths | ✅ Recorded sessions replay from the bus (`perception.detection` → per-person waypoints; picker over `GET /v1/sessions/{tenant_id}`). Seed paths remain as demo fallback only. |
| Ask the Room | 🟡 Pre-canned answers matched by regex. Production calls Claude/GPT-4o → Cypher → Neo4j. |
| Agents | 🟡 UI is real, rule storage is in-memory. Production persists to Postgres + subscribes to graph changes. |
| Report | 🟡 Static numbers. Production templates from session data. |
| Server-side perception (Phase 0) | 🟡 Python stub in `perception/realmspace.py` — the Python equivalent of what the browser detector does today. |

**The headline change vs. v0 prototype:** the live tab is no longer a stylised
mock. Walk in front of the laptop, watch yourself get a persistent anonymous
ID with a bounding box, see the KPI strip and event log update in real time
from your actual camera feed. Detection, tracking and event emission all run
on-device — no backend, no cloud, no frames stored.

This document is honest. If you demo it, demo it honestly. The point of the
prototype is to validate desire, not to claim shipped product.

---

## 10. Roadmap

| Phase | Deliverable | Timeframe |
|---|---|---|
| **Phase 0 — Hello, RealmSpace** | Python script: webcam → YOLO → JSON event log | Days |
| **Phase 1 — Structured reality** | Neo4j + ByteTrack + zones → live graph | 2 weeks |
| **Phase 2 — Dashboard v1** | FastAPI + WebSocket streaming to the Next.js app already in this repo | 2 weeks |
| **Phase 3 — Ask the Room** | Claude/GPT-4o → Cypher pipeline, fully wired | 1 week |
| **Phase 4 — Agents + insights** | Rule engine + LLM-generated insights every 10 minutes | 1–2 weeks |
| **Phase 5 — Report + polish** | One-click PDF, onboarding, multi-camera, exports | 2 weeks |
| **First paid activation** | Pavilion-tier pilot with a Floats XR client | Week 8 |

---

## 11. Success criteria

**Build**

- `docker compose up` brings up Neo4j + Postgres + Qdrant in <60s
- `npm run dev` brings up the dashboard
- Perception script runs against built-in webcam in <30s of `python main.py`
- <500ms latency from detection → graph node → dashboard

**Pitch**

- A prospect can ask a question of the room and get an answer in <5s
- The demo runs on a laptop with no network (proves the privacy/local moat)
- One screenshot of the dashboard convinces a marketing director

**Commercial (Year 1)**

- ≥6 paid pilots embedded in agency activations
- ≥1 partner agency white-label deployment
- ≥3 client logos for the case study page
- ARR run-rate $200K+ from measurement line items

---

## 12. Risks & mitigations

| Risk | Mitigation |
|---|---|
| CV accuracy in real venues (lighting, occlusion) | Calibration UI; start in controlled booth lighting we already design |
| Client compliance kills enterprise sale | Local-first + clear deletion + signed DPIA pack |
| LLM hallucinates wrong Cypher | Constrain to allow-list of query patterns; validate against schema before exec |
| Demo fails live | Always carry a recorded backup of the twin replay |
| Scope creep | Phase 0–3 ships before anything else; Phases 4–5 are post-revenue |
| Verkada / Spot AI ship a similar feature | They can't ship our booth+experience+measurement loop. Stay vertical. |

---

## 13. Open questions

- Pricing: per activation vs. per visitor — agency calendars favour per-activation, but per-visitor scales better long-term. We'll start per-activation and revisit at H2.
- Hardware: ship MacBook + Logitech kits, or open standard so agencies BYO? Recommendation: ship our kit for first 12 months; standardise later.
- Multi-camera fusion: how soon? Recommend Phase 4 minimum.
- Cloud sync (Supabase) for cross-device dashboards: Phase 5+.

---

*Authored for Floats XR. v2 supersedes the original "intelligence layer
for the physical world" PRD, which mixed three products into one. v2 picks
the wedge: experiential intelligence, sold inside our existing service.*
