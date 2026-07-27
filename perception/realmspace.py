"""
RealmSpace — Phase 0 perception stub.

Open the webcam, detect people with YOLOv8, draw bounding boxes with
persistent IDs (ByteTrack), and emit structured event JSON to stdout.

Usage:
    python realmspace.py                       # uses default camera
    python realmspace.py --source 1            # use camera index 1
    python realmspace.py --source clip.mp4     # use a video file
    python realmspace.py --headless            # no preview window

Output JSON shape (matches what the dashboard expects):
    {
        "type": "detection",
        "ts": 1716066240.123,
        "frame_id": 8421,
        "person_id": "P-12",
        "bbox": [x1, y1, x2, y2],
        "confidence": 0.89
    }
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, asdict
from typing import Iterable

import cv2  # opencv-python


@dataclass
class Detection:
    type: str
    ts: float
    frame_id: int
    person_id: str
    bbox: list[float]
    confidence: float


class BusClient:
    """Optional HTTP bridge to the realmspace edge API event bus."""

    def __init__(self, base_url: str, tenant_id: str, session_id: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.tenant_id = tenant_id
        self.session_id = session_id
        self.enabled = bool(base_url)

    def post(self, event_type: str, payload: dict, event_id: str | None = None) -> None:
        if not self.enabled:
            return
        body = {
            "tenantId": self.tenant_id,
            "sessionId": self.session_id,
            "type": event_type,
            "payload": payload,
            "eventId": event_id or str(uuid.uuid4()),
            "occurredAt": int(time.time() * 1000),
        }
        req = urllib.request.Request(
            f"{self.base_url}/v1/events",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                resp.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            # Offline buffer is a later P1 hardening step; for now log and continue.
            print(json.dumps({"type": "bus_error", "error": str(exc)}), file=sys.stderr, flush=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="RealmSpace Phase 0 perception stub")
    p.add_argument(
        "--source",
        default="0",
        help="camera index or path to video file (default: 0 = built-in webcam)",
    )
    p.add_argument(
        "--model",
        default="yolov8n.pt",
        help="ultralytics model checkpoint (default: yolov8n.pt, downloads on first run)",
    )
    p.add_argument(
        "--conf",
        type=float,
        default=0.4,
        help="confidence threshold (default 0.4)",
    )
    p.add_argument(
        "--headless",
        action="store_true",
        help="do not show preview window",
    )
    p.add_argument(
        "--save",
        default=None,
        help="optional folder to save annotated frames",
    )
    p.add_argument(
        "--bus-url",
        default="",
        help="optional edge API base URL, e.g. http://127.0.0.1:8000 — emits RealmEvents",
    )
    p.add_argument(
        "--tenant-id",
        default="t_floats",
        help="tenant scope for bus events (default: t_floats)",
    )
    p.add_argument(
        "--session-id",
        default="",
        help="session id for bus events (default: auto)",
    )
    return p.parse_args()


def load_yolo(model_name: str):
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        print(
            "ultralytics is not installed. run: pip install -r requirements.txt",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    return YOLO(model_name)


def detections_for_frame(
    yolo_results, frame_id: int, ts: float, conf_min: float
) -> Iterable[Detection]:
    for res in yolo_results:
        if res.boxes is None:
            continue
        for box in res.boxes:
            cls = int(box.cls.item()) if hasattr(box.cls, "item") else int(box.cls[0])
            # COCO class 0 = person
            if cls != 0:
                continue
            conf = float(box.conf.item()) if hasattr(box.conf, "item") else float(box.conf[0])
            if conf < conf_min:
                continue
            xyxy = box.xyxy[0].tolist()
            tid = int(box.id.item()) if box.id is not None else -1
            person_id = f"P-{tid:03d}" if tid >= 0 else "P-???"
            yield Detection(
                type="detection",
                ts=ts,
                frame_id=frame_id,
                person_id=person_id,
                bbox=[round(v, 2) for v in xyxy],
                confidence=round(conf, 3),
            )


def draw_overlay(frame, detection: Detection) -> None:
    x1, y1, x2, y2 = (int(v) for v in detection.bbox)
    color = (255, 200, 60)  # BGR for the live-cam dashboard cyan
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
    label = f"{detection.person_id}  {int(detection.confidence * 100)}%"
    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
    cv2.rectangle(frame, (x1, y1 - th - 6), (x1 + tw + 6, y1), color, -1)
    cv2.putText(
        frame,
        label,
        (x1 + 3, y1 - 4),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (0, 0, 0),
        1,
        cv2.LINE_AA,
    )


def main() -> int:
    args = parse_args()

    source: str | int = args.source
    if source.isdigit():
        source = int(source)

    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        print(f"could not open source: {args.source}", file=sys.stderr)
        return 1

    yolo = load_yolo(args.model)
    session_id = args.session_id or f"s_perception_{uuid.uuid4().hex[:8]}"
    bus = BusClient(args.bus_url, args.tenant_id, session_id)

    print(
        json.dumps(
            {
                "type": "session_start",
                "ts": time.time(),
                "source": str(args.source),
                "model": args.model,
                "conf_min": args.conf,
                "tenant_id": args.tenant_id,
                "session_id": session_id,
                "bus_url": args.bus_url or None,
            }
        ),
        flush=True,
    )
    bus.post(
        "session.started",
        {"name": "Perception stub", "venue": f"source:{args.source}"},
    )

    frame_id = 0
    fps_t0 = time.time()
    fps_frames = 0
    fps = 0.0
    # Throttle bus posts: at most ~5 detection events/sec to avoid flooding.
    last_bus_post = 0.0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frame_id += 1
            ts = time.time()

            results = yolo.track(frame, persist=True, conf=args.conf, classes=[0], verbose=False)

            for d in detections_for_frame(results, frame_id, ts, args.conf):
                print(json.dumps(asdict(d)), flush=True)
                draw_overlay(frame, d)
                if bus.enabled and (ts - last_bus_post) >= 0.2:
                    bus.post(
                        "perception.detection",
                        {
                            "anonId": d.person_id,
                            "bbox": d.bbox,
                            "confidence": d.confidence,
                            "frameId": d.frame_id,
                        },
                    )
                    last_bus_post = ts

            fps_frames += 1
            if time.time() - fps_t0 > 1.0:
                fps = fps_frames / (time.time() - fps_t0)
                fps_t0 = time.time()
                fps_frames = 0

            if not args.headless:
                cv2.putText(
                    frame,
                    f"REALMSPACE  {fps:.1f} fps  ANON",
                    (10, 22),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    (0, 212, 255),
                    1,
                    cv2.LINE_AA,
                )
                cv2.imshow("RealmSpace · perception stub", frame)
                if cv2.waitKey(1) & 0xFF in (27, ord("q")):
                    break

            if args.save:
                cv2.imwrite(f"{args.save}/f_{frame_id:06d}.jpg", frame)
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        if not args.headless:
            cv2.destroyAllWindows()
        bus.post("session.ended", {"name": "Perception stub"})
        print(
            json.dumps({"type": "session_end", "ts": time.time(), "frames": frame_id}),
            flush=True,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
