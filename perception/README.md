# Perception Engine — stub

The Python perception engine that backs the dashboard. This folder ships
a single minimal script that proves the loop: open the webcam → detect
people with YOLO → draw bounding boxes → log JSON events.

The dashboard in `/dashboard` is wired against the *contract* this script
will eventually emit. Right now the dashboard reads from `lib/mock/` data.
Once this engine writes real events to the event bus, the dashboard swaps to
it with no UI changes.

**The bus it will write into now exists** — `backend/` implements
[`docs/event-bus-spec.md`](../docs/event-bus-spec.md). This script does not use
it yet; wiring that up is the next task for this folder.

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

## What's next for this folder

Canonical sequencing lives in [`docs/roadmap.md`](../docs/roadmap.md) — this is
just the perception slice of it.

| Item | Adds |
|---|---|
| **Emit into the bus** (Phase 1) | Replace stdout with `POST /events` — one `perception.detection` per detection, carrying a producer-assigned `event_id` |
| **Offline buffer + replay** (Phase 1) | Queue locally when the backend is unreachable, replay in order on reconnect. Conference WiFi is assumed unreliable (`event-bus-spec.md` §5) |
| Zone polygons + dwell (Phase 1/2) | Emitted as `spatial.zone_enter` / `spatial.dwell` by the tracker consumer, not by this script |
| Multi-camera + sensor fusion (Phase 6) | Calibration UI + drift telemetry alongside |

Note that the JSON this script prints today (`type: "detection"`) is **not yet**
the bus event shape — the bus expects `event_id`, `tenant_id`, `session_id`, a
namespaced `type` like `perception.detection`, and the detection fields nested
under `payload`. Reconciling the two is part of the first item above.
