# realmspace

> **Watch the room think.**
>
> realmspace is the measurement, replay, and intelligence layer for physical
> brand experiences. One camera. One laptop. A queryable graph of attention,
> dwell, and behaviour — plus a 3D digital twin you can scrub through and ask
> questions of.

This repository ships:

- A polished **Next.js dashboard** with a Tesla-inspired in-car UI aesthetic,
  fully functional with mocked data (`dashboard/`)
- A **Phase-0 Python perception stub** that opens any webcam, runs YOLO
  person detection, and emits structured JSON events (`perception/`)
- The **product spec and supporting documents** (`docs/`)

. The live `/live` view performs **real, on-device
person detection** from your webcam using TensorFlow.js + COCO-SSD with a
custom centroid tracker — no backend, no cloud, no frames stored. Walk in
front of your laptop and watch yourself get tracked with a persistent
anonymous ID, live. Everything else (digital twin, reports, agents) is wired
against mocked data that maps 1:1 to the contracts the real backend will
produce. The boundary between "real" and "mocked" is documented explicitly in
`docs/PRD.md` so you can demo honestly.

---

## What's in the dashboard

| Surface | Path | What it shows |
|---|---|---|
| **Landing** | `/` | Agency-targeted product page — hero, the 4-step loop, pricing, privacy posture |
| **Live** | `/live` | **Real webcam + on-device object tracking.** Click "Start live session" — your browser asks for camera permission, loads COCO-SSD (~28 MB, once), then detects people in your frame at 10–22 fps with persistent anonymous IDs. KPIs and the event log update in real time from the detector. Heatmap, AI insights, and zone counters remain mocked. |
| **Twin** | `/twin` | 3D digital twin of the activation — anonymous avatars walking recorded paths, scrubbable timeline, playback speed |
| **Ask** | `/ask` | Plain-English query interface — natural language → Cypher → answer with chart |
| **Agents** | `/agents` | Rule builder — "when X, do Y" with Slack / webhook / screen actions |
| **Report** | `/report` | Client-deliverable post-activation report — funnel, top moments, sponsor exposure, recommendations |

---

## Running the dashboard

```bash
cd dashboard
npm install --legacy-peer-deps     # only the first time
npm run dev
```

Open `http://localhost:3000`.

Stack:

- Next.js 16 (App Router) · React 19 · TypeScript
- Tailwind CSS v4 (CSS-first config in `globals.css`)
- React Three Fiber + drei + three.js (the digital twin)
- Recharts (charts) · motion (animations) · lucide-react (icons)
- Plus Jakarta Sans + JetBrains Mono via `next/font/google`

---

## Running the perception stub

```bash
cd perception
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python realmspace.py               # opens your default webcam
python realmspace.py --headless    # JSON events only, no preview window
```

Output is one JSON event per detection per frame on stdout. See
`perception/README.md` for the full schema.

---

## Documentation

| Doc | What it covers |
|---|---|
| [`docs/PRD.md`](docs/PRD.md) | Product spec v2 — positioning, ICP, MVP scope, real vs. mocked status |
| [`docs/architecture.md`](docs/architecture.md) | End-to-end system architecture |
| [`docs/data-model.md`](docs/data-model.md) | Graph schema + example Cypher queries |
| [`docs/gtm.md`](docs/gtm.md) | Go-to-market plan, pricing, cold email template, revenue math |
| [`docs/privacy.md`](docs/privacy.md) | Privacy posture sent to client compliance teams |

---

## Project layout

```
realmspace/
├── README.md                 ← you are here
├── docs/                     ← PRD, architecture, data model, GTM, privacy
├── perception/               ← Python · YOLO + ByteTrack + OpenCV
│   ├── realmspace.py
│   └── requirements.txt
└── dashboard/                ← Next.js · the demo artifact
    ├── src/
    │   ├── app/              ← routes (landing + the 5 app surfaces)
    │   ├── components/       ← UI, chrome, viz, twin, ask, report
    │   └── lib/
    │       ├── mock/         ← fabricated session data
    │       └── utils.ts
    └── package.json
```

---

## What's real vs. mocked (today)

| | Real | Mocked |
|---|---|---|
| Dashboard UI / interactions | ✅ | |
| **Live webcam capture** | ✅ on-device via `getUserMedia` | |
| **Real-time person detection** | ✅ COCO-SSD via TensorFlow.js (WebGL backend) | |
| **Persistent anonymous IDs** | ✅ in-browser centroid tracker | |
| **Live KPIs + event log** | ✅ driven by real detections | |
| 3D digital twin scene | ✅ | data only |
| Heatmap rendering | ✅ | aggregated from mock waypoints |
| Twin avatar paths | | 🟡 hand-authored seed paths |
| Ask the Room — answers | | 🟡 pre-canned, regex-matched |
| Agent rule engine | ✅ UI | 🟡 in-memory state |
| Report numbers | | 🟡 static for the demo |
| Server-side perception (Phase 0) | 🟡 Python stub in `perception/` | |

### Live tab — how the camera actually works

1. **Click "Start live session"** in the camera viewport
2. **Browser prompts for camera permission** — must be granted
3. **COCO-SSD downloads on first run** (~28 MB, cached forever after)
4. **Detection loop runs in `requestAnimationFrame`**: each frame goes to TF.js with WebGL backend, COCO-SSD returns bounding boxes + class labels + confidence
5. **Centroid tracker** (`src/lib/tracker.ts`) maintains persistent IDs across frames using greedy nearest-centroid matching with a configurable distance threshold and miss tolerance
6. **Bounding boxes + corner brackets + ID tags** are drawn on a canvas overlay that's mirrored to match the user's view
7. **Live KPIs** ("People now", session length, fps) and the **event log** ("P-003 entered the frame", "P-005 left the frame") are derived directly from detector state

No data leaves the tab. The video element, the model, the canvas, the tracker — all live in your browser. Close the tab and everything is gone.

 The mocked pieces are wired against the exact
contract the real backend will produce. When the Phase-1 perception engine
lands, swapping is a wiring change, not a redesign.

---

## License

Internal · Floats XR · 2026.
