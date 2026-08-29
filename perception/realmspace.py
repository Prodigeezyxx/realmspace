"""
RealmSpace — Phase 0 perception stub.

Open the webcam, detect people with YOLOv8, draw bounding boxes with
persistent IDs (ByteTrack), and emit structured event JSON to stdout.

Usage:
    python realmspace.py                       # uses default camera, stdout only
    python realmspace.py --bus-url http://127.0.0.1:8000 --api-key "$KEY"
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
import os
import sys
import time
from dataclasses import dataclass, asdict
from typing import Iterable

import cv2  # opencv-python

from bus_client import BusClient
from heading import heading_from_keypoints
from mask import MaskFetcher, MaskUnavailable


@dataclass
class Detection:
    type: str
    ts: float
    frame_id: int
    person_id: str
    bbox: list[float]
    confidence: float
    #: Which way they are facing, radians in the image plane, or None when the
    #: keypoints did not support an answer. See `heading_from_keypoints` — this
    #: is the *only* thing derived from the skeleton that leaves this process.
    heading: float | None = None
    #: How much to trust it, 0..1. Ships with the heading rather than being
    #: folded into it, so a reader can judge the measurement instead of a
    #: verdict — the argument `drift.detected` makes for its own numbers.
    heading_confidence: float | None = None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="RealmSpace Phase 0 perception stub")
    p.add_argument(
        "--source",
        default="0",
        help="camera index or path to video file (default: 0 = built-in webcam)",
    )
    p.add_argument(
        "--model",
        default="yolov8n-pose.pt",
        help="ultralytics model checkpoint. The pose default is what gaze needs — a\n             detect model still tracks and dwells, it just carries no heading",
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
        help="edge API base URL, e.g. http://127.0.0.1:8000. Omit for stdout only.",
    )
    p.add_argument(
        "--tenant-id",
        default="t_floats",
        help="tenant scope for bus events (default: t_floats)",
    )
    p.add_argument(
        "--session-id",
        default="s_demo",
        help="activation this run belongs to (default: s_demo)",
    )
    p.add_argument(
        "--camera-id",
        default="cam-1",
        help="which camera this process is. Names the privacy mask it fetches "
             "and the camera drift telemetry is attributed to (default: cam-1).",
    )
    p.add_argument(
        "--api-key",
        default=os.environ.get("REALMSPACE_API_KEY", ""),
        help="device key. Prefer REALMSPACE_API_KEY so it stays out of shell history.",
    )
    p.add_argument(
        "--bus-interval",
        type=float,
        default=0.2,
        help="min seconds between posted detections (default 0.2). Throttles the "
             "bus without throttling stdout.",
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
        # Present only when a pose model is loaded. A plain detect model leaves
        # this None and every detection simply carries no heading, so an
        # existing deployment keeps working and gaze is absent rather than wrong.
        kp = getattr(res, "keypoints", None)
        for idx, box in enumerate(res.boxes):
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

            heading = heading_conf = None
            if kp is not None and kp.xy is not None and idx < len(kp.xy):
                points = kp.xy[idx].tolist()
                # `conf` is None on a pose model run without confidences; treat
                # that as "no evidence" rather than as certainty.
                raw = kp.conf[idx].tolist() if kp.conf is not None else []
                found = heading_from_keypoints(points, raw)
                if found is not None:
                    heading, heading_conf = found

            yield Detection(
                type="detection",
                ts=ts,
                frame_id=frame_id,
                person_id=person_id,
                bbox=[round(v, 2) for v in xyxy],
                confidence=round(conf, 3),
                heading=None if heading is None else round(heading, 4),
                heading_confidence=heading_conf,
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

    bus = BusClient(
        base_url=args.bus_url,
        tenant_id=args.tenant_id,
        session_id=args.session_id,
        api_key=args.api_key,
    )
    if bus.enabled:
        pending = bus.pending()
        print(
            json.dumps({"type": "bus_enabled", "url": args.bus_url,
                        "tenant": args.tenant_id, "session": args.session_id,
                        "buffered_from_last_run": pending}),
            file=sys.stderr, flush=True,
        )

    # Before the first frame is read, and therefore before any inference. A
    # mask established after the loop has started is a mask that missed frames,
    # and privacy.md's promise is about every frame. Failing here also fails
    # before the preview window opens, so a refused start is not a booth showing
    # unmasked video to the room while somebody reads the error.
    masker = MaskFetcher(bus, session_id=args.session_id, camera_id=args.camera_id)
    try:
        mask = masker.start()
    except MaskUnavailable as exc:
        print(json.dumps({"type": "mask_unavailable", "error": str(exc)}),
              file=sys.stderr, flush=True)
        cap.release()
        return 2
    print(
        json.dumps({"type": "mask", "camera": args.camera_id,
                    "masking": mask.masks_anything, "revision": mask.revision,
                    "source": mask.source}),
        file=sys.stderr, flush=True,
    )

    print(
        json.dumps(
            {
                "type": "session_start",
                "ts": time.time(),
                "source": str(args.source),
                "model": args.model,
                "conf_min": args.conf,
            }
        ),
        flush=True,
    )
    # session.started on the bus is a separate thing from the stdout line above:
    # one is for a human watching the terminal, the other is an event in the log
    # that the report and the twin will read back later (spec §3).
    bus.post(
        "session.started",
        {"source": str(args.source), "model": args.model,
         "camera_id": args.camera_id},
    )

    frame_id = 0
    fps_t0 = time.time()
    fps_frames = 0
    fps = 0.0
    last_bus_post = 0.0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frame_id += 1
            ts = time.time()

            # Before the model, before the overlay, before --save. The frame is
            # masked in place and nothing downstream holds the original, which
            # is the only version of this that cannot leak by omission.
            if masker.poll():
                print(
                    json.dumps({"type": "mask_updated", "camera": args.camera_id,
                                "masking": masker.mask.masks_anything,
                                "revision": masker.mask.revision}),
                    file=sys.stderr, flush=True,
                )
            frame = masker.apply(frame, cv2)

            results = yolo.track(frame, persist=True, conf=args.conf, classes=[0], verbose=False)

            # frame.shape is (height, width, channels). The bus needs both:
            # bboxes are in pixels and zone polygons are normalised 0..1, so a
            # detection without them cannot be placed in a zone and the tracker
            # dead-letters it (event-bus-spec.md §3).
            frame_h, frame_w = frame.shape[0], frame.shape[1]

            for d in detections_for_frame(results, frame_id, ts, args.conf):
                print(json.dumps(asdict(d)), flush=True)
                draw_overlay(frame, d)

                # Throttled. stdout keeps every detection because that is a
                # debugging stream; the bus is an append-only log, and one row
                # per person per frame at 20fps would be thousands a minute of
                # near-identical events nobody will ever read.
                if bus.enabled and (ts - last_bus_post) >= args.bus_interval:
                    bus.post(
                        "perception.detection",
                        {
                            "anon_id": d.person_id,
                            "bbox": d.bbox,
                            "confidence": d.confidence,
                            "frame_id": d.frame_id,
                            "frame_width": frame_w,
                            "frame_height": frame_h,
                            # Which camera saw this. Additive to the payload
                            # event-bus-spec.md §3 pins for perception.detection,
                            # and the field the drift consumer groups on — a
                            # confidence mean averaged across two cameras
                            # describes neither.
                            "camera_id": args.camera_id,
                            # Two scalars, and only when the skeleton supported
                            # them. The keypoints they came from are already
                            # out of scope by the time this runs: privacy.md
                            # keeps pose "briefly" for a gaze vector, and the
                            # bus is append-only and exported, so a skeleton
                            # posted here would be permanent. The consumer
                            # applies the confidence threshold, so the decision
                            # lives in one place rather than at every camera.
                            **(
                                {}
                                if d.heading is None
                                else {
                                    "heading": d.heading,
                                    "heading_confidence": d.heading_confidence,
                                }
                            ),
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
        # In `finally`, so Ctrl-C still closes the session properly rather than
        # leaving it open forever in the log.
        bus.post("session.ended", {"frames": frame_id})
        if bus.enabled and bus.pending():
            print(
                json.dumps({"type": "bus_pending_at_exit", "events": bus.pending(),
                            "note": "will replay on next run"}),
                file=sys.stderr, flush=True,
            )
        print(
            json.dumps({"type": "session_end", "ts": time.time(), "frames": frame_id}),
            flush=True,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
