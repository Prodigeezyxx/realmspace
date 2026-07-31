# Perception Engine — stub

The Python perception engine that backs the dashboard. This folder ships
a single minimal script that proves the loop: open the webcam → detect
people with YOLO → draw bounding boxes → log JSON events.

The dashboard in `/dashboard` is wired against the *contract* this script
will eventually emit. Right now the dashboard reads from `lib/mock/` data.
Once this engine writes real events to the event bus, the dashboard swaps to
it with no UI changes.

**It writes into the bus now.** Add `--bus-url` and a device key and every
detection becomes a durable event; leave them off and it prints to stdout as
before.

```bash
python realmspace.py --headless \
  --bus-url http://127.0.0.1:8000 \
  --session-id s_demo \
  --api-key "$REALMSPACE_API_KEY"     # or just export REALMSPACE_API_KEY
```

Get a key from the backend: `python -m app.auth.seed`. Producers use a device
key rather than a login — a camera cannot sign in — and that key can only write.

**Offline is the normal case, not the error case.** When the bus is unreachable
events go to `.bus-buffer.jsonl` next to the script and replay oldest-first when
it returns. Because each event's id is assigned when it happens rather than when
it is sent, the log recognises a replayed event and stores it once. Kill the
backend mid-run and watch `bus_buffered` on stderr, then `bus_replay_done` when
you bring it back.

The client lives in [`bus_client.py`](./bus_client.py), separate from this
script so it imports without OpenCV — which makes the buffer logic testable
without a camera, and lets the RFID bridge and kiosk SDK reuse it instead of
writing their own.

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
| ~~Emit into the bus~~ ✅ | Done — `--bus-url`, throttled to one detection per 200ms so an append-only log isn't filled with near-identical rows |
| ~~Offline buffer + replay~~ ✅ | Done — `.bus-buffer.jsonl`, oldest-first, idempotent on reconnect |
| Zone polygons + dwell (Phase 1/2) | Emitted as `spatial.zone_enter` / `spatial.dwell` by the tracker consumer, not by this script |
| Multi-camera + sensor fusion (Phase 6) | Calibration UI + drift telemetry alongside |

The JSON printed to stdout is deliberately **not** the bus event shape — it is a
debugging stream and stays as it was, so anything piping it still works. The bus
shape (`eventId`, `tenantId`, namespaced `type`, fields under `payload`) is built
in `bus_client.py`.
