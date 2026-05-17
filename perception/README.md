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
| 1 | Zone polygons + dwell calculation + Neo4j writer |
| 2 | FastAPI WebSocket server + dashboard wiring |
| 3 | LLM `/query` endpoint (Cypher generation) |
| 4 | Agent rule engine + insight generation |
| 5 | Multi-camera + sensor fusion + RFID ingestion |
