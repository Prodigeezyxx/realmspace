# RealmSpace

> **Watch the room think.**
>
> RealmSpace is the measurement, replay, and intelligence layer for physical
> brand experiences. One camera. One laptop. A queryable graph of attention,
> dwell, and behaviour — plus a 3D digital twin you can scrub through and ask
> questions of.

This repository ships:

- A polished **Next.js dashboard** with a Tesla-inspired in-car UI aesthetic,
  fully functional with mocked data (`dashboard/`)
- A **Phase-0 Python perception stub** that opens any webcam, runs YOLO
  person detection, and emits structured JSON events (`perception/`)
- The **product spec and supporting documents** (`docs/`)

It is **demo-ready tonight**. It is **not** production. The boundary between
"real" and "mocked" is documented explicitly in `docs/PRD.md` so you can demo
honestly.

---

## What's in the dashboard

| Surface | Path | What it shows |
|---|---|---|
| **Landing** | `/` | Agency-targeted product page — hero, the 4-step loop, pricing, privacy posture |
| **Live** | `/live` | Operator dashboard — KPIs, annotated camera view, heatmap, traffic, event log, zone counters, AI insights |
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
- Inter + JetBrains Mono via `next/font/google`

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
| 3D digital twin scene | ✅ | data only |
| Heatmap rendering | ✅ | aggregated from mock waypoints |
| Live camera feed | | 🟡 stylised top-down representation |
| Person tracks | | 🟡 hand-authored seed paths |
| Ask the Room — answers | | 🟡 pre-canned, regex-matched |
| Agent rule engine | ✅ UI | 🟡 in-memory state |
| Report numbers | | 🟡 static for the demo |
| Perception engine | 🟡 phase-0 stub | |

**Demo this honestly.** The mocked pieces are wired against the exact
contract the real backend will produce. When the Phase-1 perception engine
lands, swapping is a wiring change, not a redesign.

---

## License

Internal · Yourself Creative · 2026.
