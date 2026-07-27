# Perception Engine — stub

The Python perception engine that backs the dashboard. This folder ships
a single minimal script that proves the loop: open the webcam → detect
people with YOLO → draw bounding boxes → log JSON events.

The dashboard in `/dashboard` is wired against the *contract* this script
will eventually emit. Right now the dashboard reads from `lib/mock/` data.
Once this engine writes real events to Postgres + Neo4j, the dashboard
swaps to it with no UI changes.

## Phase 0 — Hello, RealmSpace

```bash
cd perception
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python realmspace.py
```

This will:
- Open your webcam (or `--source path/to/video.mp4`)
- Run YOLOv8 person detection at 15–22 fps
- Draw bounding boxes with persistent IDs (ByteTrack)
- Print structured event JSON to stdout
- Optionally save annotated frames to `out/`

## Roadmap (post-stub)

| Phase | Adds |
|---|---|
| 1 | Emit RealmEvents into edge bus (`--bus-url`); zone polygons + dwell + graph writer (in `backend/`) |
| 2 | Dashboard `/live` fully driven by bus WebSocket (mock KPIs removed) |
| 3 | LLM `/query` endpoint (Cypher/SQL allow-list) |
| 4 | Agent rule engine + insight generation |
| 5 | Multi-camera + sensor fusion + RFID ingestion |
| later | Offline buffer on disk when bus unreachable |

### Emit into the edge bus

```bash
# terminal A
cd backend && uvicorn app.main:app --reload --port 8000

# terminal B
cd perception
python realmspace.py --bus-url http://127.0.0.1:8000 --tenant-id t_floats
```

Events land as `session.started` / `perception.detection` / `session.ended` and
are projected into `graph_nodes` by the graph writer consumer.
