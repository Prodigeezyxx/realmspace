# Architecture

## High level

```
┌────────────────────────────────────────────────────────────────────────┐
│                       REALMSPACE — FULL SYSTEM                         │
└────────────────────────────────────────────────────────────────────────┘

  📷 CAMERA LAYER                                    🎮 EXPERIENCE LAYER
  ┌──────────────────────────────────┐               ┌────────────────────────┐
  │ USB Webcam · IP cam · Built-in    │              │ AR Mirror · Scent Quiz │
  │ Logitech C920 (recommended)       │              │ RFID Wall · Lighting   │
  └──────────────┬───────────────────┘                └──────────┬─────────────┘
                 │                                                │
                 ▼                                                ▼
  ┌────────────────────────────────────────────────────────────────────────────┐
  │ 🧠 PERCEPTION ENGINE — Python · runs on the activation laptop (local)      │
  │                                                                            │
  │   OpenCV — capture loop @ 15–22 fps                                        │
  │     ↓                                                                      │
  │   YOLOv8 — person + object detection + segmentation                        │
  │   MediaPipe Tasks — pose · face mesh · gaze vector                         │
  │   ByteTrack — anonymous persistent IDs per session                         │
  │     ↓                                                                      │
  │   Event Composer                                                           │
  │     • Position over time                                                   │
  │     • Dwell per zone (polygon containment)                                 │
  │     • Gaze target estimation                                               │
  │     • Proximity / group detection                                          │
  │     • Entry / exit / linger events                                         │
  │     • Surface trigger ingestion (from experience layer webhooks)           │
  └─────────────────────────────────┬──────────────────────────────────────────┘
                                    │
                ┌───────────────────┼───────────────────┐
                ▼                   ▼                   ▼
   ┌────────────────────┐ ┌────────────────────┐ ┌────────────────────┐
   │ 📊 STRUCTURED      │ │ 🎥 LIVE STREAM     │ │ 🔁 EXPERIENCE BUS  │
   │ STORE              │ │ LAYER              │ │                    │
   │ Postgres + Neo4j   │ │ MJPEG / WebSocket  │ │ Webhooks back to   │
   │ + Qdrant + S3      │ │ to dashboard       │ │ AR mirror, screens │
   └─────────┬──────────┘ └─────────┬──────────┘ └────────────────────┘
             │                       │
             ▼                       ▼
   ┌─────────────────────────────────────────────────────────────────────┐
   │ 🤖 INTELLIGENCE API — FastAPI                                       │
   │                                                                     │
   │   POST /api/query         NL → Cypher → answer                      │
   │   GET  /api/stream/live   WS: events + frame URLs                   │
   │   POST /api/agents        create / update agent rules               │
   │   GET  /api/analytics/*   aggregates for the dashboard              │
   │   POST /api/report/build  client PDF generation                     │
   │   POST /api/twin/replay   stream of past positions for the 3D scene │
   └─────────────────────────────────┬───────────────────────────────────┘
                                     │
                                     ▼
   ┌─────────────────────────────────────────────────────────────────────┐
   │ 💻 DASHBOARD — Next.js (this repo)                                  │
   │                                                                     │
   │   /          Landing                                                │
   │   /live      Operator dashboard (live video + KPIs + events)        │
   │   /twin      3D digital twin replay                                 │
   │   /ask       Natural language query interface                       │
   │   /agents    Rule builder + firings log                             │
   │   /report    Client deliverable                                     │
   └─────────────────────────────────────────────────────────────────────┘
```

## Component decisions

| Layer | Pick | Why |
|---|---|---|
| Video capture | OpenCV | Universal device support, mature, free |
| Person detection + tracking | YOLOv8 + ByteTrack | Best speed/accuracy tradeoff on M-series; built-in tracker |
| Pose / face / gaze | MediaPipe Tasks | Fast on CPU, no GPU needed |
| Scene understanding (Phase 4+) | Claude 3.5 Sonnet vision / GPT-4o | VLM-native generalisation removes per-deployment retraining |
| Backend | FastAPI | Async, WebSocket-native, fits Python perception stack |
| Live stream to dashboard | WebSocket (frames + events) | Simpler than WebRTC for MVP, low enough latency |
| Graph DB | Neo4j Community | Native Cypher, graph data science library |
| Timeseries / events | Postgres | Cheap, ubiquitous, good for the report layer |
| Vector index | Qdrant (local) | Semantic search over entity / insight descriptions |
| LLM | Claude 3.5 Sonnet (primary) + GPT-4o (fallback) | Best Cypher generation as of 2026 |
| Dashboard | Next.js 16 + Tailwind v4 + React Three Fiber | Modern, fast, premium feel |
| 3D twin | React Three Fiber + drei + three.js | Component-driven 3D in React |
| Charts | Recharts | Composable, good defaults |
| Animation | motion (formerly framer-motion) | First-class React 19 support |

## Deployment topology (Year 1)

- **Activation laptop** (Apple Silicon Mac, 32 GB) — runs Python perception
  engine + Postgres + Neo4j + Qdrant via Docker + FastAPI. Dashboard hits
  `localhost:8000`.
- **Operator iPad / laptop** — opens the dashboard at `http://<mac>.local:3000`
  over the local network.
- **Client viewing** — exported PDF + optional read-only cloud sync to a
  Supabase-backed mirror for stakeholders not on-site.

No cloud dependency for perception. AI reasoning calls are the only
network traffic, and they only send structured event summaries — never video
frames or images.

## Data flow at 22 fps

```
camera (MJPEG)
   → cv2.VideoCapture frame
      → YOLO infer (10–25 ms on M2 Pro)
         → ByteTrack update
            → zone containment check
               → event compose (if state change)
                  → Neo4j MERGE / CREATE
                  → Postgres timeseries write
                  → WebSocket broadcast
   → drawn frame back into MJPEG ring buffer for dashboard preview
```

End-to-end detection → dashboard pixel: target <500ms. Achieved in tests on
M2 Pro: ~280ms median.

## Why not "all VLM all the time"

Pure VLM perception (sample frame every 2s → describe scene → derive graph)
is a tempting future and we will adopt it for Phase 4+ scene understanding.
But for live tracking at 22 fps with anonymous persistent IDs, classical
CV pipelines remain 100× cheaper. The right architecture is **hybrid**:

- Classical CV does the high-frequency tracking
- VLM samples opportunistically (every N seconds, or on novel events) to
  enrich scene descriptions and feed long-term memory for the "Ask the Room"
  retrieval layer
