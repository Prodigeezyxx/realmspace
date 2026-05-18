"use client";

/**
 * Real-time object detection on the user's webcam, in the browser.
 *
 *   • TensorFlow.js with the WebGL backend
 *   • COCO-SSD (mobilenet_v2) — 80 classes including 'person'
 *   • Centroid tracker (`@/lib/tracker`) for persistent IDs
 *
 * Everything runs on-device. No frame ever leaves the browser. This is the
 * real, live proof of the RealmSpace pitch — you walk in front of your
 * laptop, you get tracked, you get an anonymous ID, you watch the room think.
 */

import {
  AlertTriangle,
  Camera,
  CameraOff,
  Loader2,
  Lock,
  Maximize2,
} from "lucide-react";
import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";

import { getDetectionModel, getModelLoadStage } from "@/lib/live-session/model-cache";
import { liveSessionActions } from "@/lib/live-session/store";
import type { LiveDetectorStatus } from "@/lib/live-session/store";
import type { DetectorStats } from "@/lib/live-session/types";
import { CentroidTracker, type Track } from "@/lib/tracker";

export type { DetectorStats };

export interface WebcamDetectorHandle {
  start: () => void;
  stop: () => void;
}

interface Props {
  /** Which COCO classes to track. Defaults to people only. */
  classFilter?: string[];
  /** Min confidence to keep a detection. */
  minConfidence?: number;
  /** Keep camera + loop running when this component unmounts (route changes). */
  persist?: boolean;
  /** Called every detection frame so the parent can render dashboard state. */
  onStats?: (s: DetectorStats) => void;
  onStatusChange?: (status: LiveDetectorStatus, errorMsg?: string | null) => void;
  /** Show start / loading overlay (only on /live viewport). */
  showControls?: boolean;
  onStreamChange?: (stream: MediaStream | null) => void;
}

export const WebcamDetector = forwardRef<WebcamDetectorHandle, Props>(
  function WebcamDetector(
    {
      classFilter = ["person"],
      minConfidence = 0.45,
      persist = false,
      onStats,
      onStatusChange,
      showControls = true,
      onStreamChange,
    },
    ref
  ) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const trackerRef = useRef(
    new CentroidTracker({ classFilter, idPrefix: "P" })
  );
  const modelRef = useRef<Awaited<ReturnType<typeof getDetectionModel>> | null>(
    null
  );
  const rafRef = useRef<number | null>(null);

  const [status, setStatus] = useState<
    "idle" | "requesting" | "loading-model" | "running" | "denied" | "error"
  >("idle");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [fps, setFps] = useState(0);
  const [modelMs, setModelMs] = useState(0);
  const [activeTracks, setActiveTracks] = useState<Track[]>([]);
  const [totalSeen, setTotalSeen] = useState(0);

  const sessionStartedAtRef = useRef<number | null>(null);
  const lastFpsTickRef = useRef<number>(0);
  const fpsFramesRef = useRef<number>(0);

  const setStatusSynced = useCallback(
    (next: typeof status, err: string | null = null) => {
      setStatus(next);
      setErrorMsg(err);
      onStatusChange?.(next, err);
    },
    [onStatusChange]
  );

  // ── Setup: load model + sensor (parallel) ─────────────────────────────
  const startSession = useCallback(async () => {
    if (status === "running" || status === "loading-model" || status === "requesting") {
      return;
    }
    setErrorMsg(null);
    setStatusSynced("loading-model");
    try {
      const video = videoRef.current;
      if (!video) return;

      const [, stream] = await Promise.all([
        getDetectionModel().then((m) => {
          modelRef.current = m;
        }),
        navigator.mediaDevices.getUserMedia({
          video: {
            facingMode: "user",
            width: { ideal: 640 },
            height: { ideal: 480 },
          },
          audio: false,
        }),
      ]);

      video.srcObject = stream;
      await video.play();
      onStreamChange?.(stream);

      sessionStartedAtRef.current = Date.now();
      trackerRef.current.reset();
      setStatusSynced("running");
    } catch (err) {
      const e = err as Error;
      if (e.name === "NotAllowedError" || /permission/i.test(e.message)) {
        setStatusSynced("denied", e.message);
      } else {
        setStatusSynced("error", e.message);
      }
      console.error("[WebcamDetector] start failed:", err);
    }
  }, [status, setStatusSynced, onStreamChange]);

  const stopSession = useCallback(() => {
    if (rafRef.current != null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    const video = videoRef.current;
    if (video?.srcObject) {
      (video.srcObject as MediaStream).getTracks().forEach((t) => t.stop());
      video.srcObject = null;
    }
    onStreamChange?.(null);
    sessionStartedAtRef.current = null;
    modelRef.current = null;
    trackerRef.current.reset();
    setActiveTracks([]);
    setTotalSeen(0);
    setFps(0);
    setStatusSynced("idle");
    liveSessionActions.resetSession();
  }, [setStatusSynced, onStreamChange]);

  useImperativeHandle(ref, () => ({
    start: () => void startSession(),
    stop: () => stopSession(),
  }));

  // ── Detection loop ────────────────────────────────────────────────────
  useEffect(() => {
    if (status !== "running") return;
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas) return;

    let cancelled = false;
    let lastInferMs = 0;
    const minFrameMs = 100; // ~10 fps — lighter on CPU/RAM

    const tick = async (frameTs: number) => {
      if (cancelled) return;
      if (video.readyState !== 4) {
        rafRef.current = requestAnimationFrame(tick);
        return;
      }

      if (frameTs - lastInferMs < minFrameMs) {
        rafRef.current = requestAnimationFrame(tick);
        return;
      }
      lastInferMs = frameTs;

      const model = modelRef.current;
      if (!model) {
        rafRef.current = requestAnimationFrame(tick);
        return;
      }
      const t0 = performance.now();
      const raw = await model.detect(video);
      const t1 = performance.now();
      setModelMs(Math.round(t1 - t0));

      const detections = raw
        .filter((d) => d.score >= minConfidence)
        .filter((d) => classFilter.includes(d.class))
        .map((d) => ({
          bbox: d.bbox,
          class: d.class,
          score: d.score,
        }));

      const tracks = trackerRef.current.update(detections, Date.now());
      const confirmed = trackerRef.current.active();
      setActiveTracks(confirmed);
      setTotalSeen(trackerRef.current.totalAssigned());

      // Resize canvas to match video intrinsic dims
      const vw = video.videoWidth;
      const vh = video.videoHeight;
      if (canvas.width !== vw) canvas.width = vw;
      if (canvas.height !== vh) canvas.height = vh;
      drawOverlay(canvas, tracks);

      // FPS counter
      fpsFramesRef.current += 1;
      const now = performance.now();
      if (now - lastFpsTickRef.current > 1000) {
        const dt = (now - lastFpsTickRef.current) / 1000;
        setFps(Number((fpsFramesRef.current / dt).toFixed(1)));
        lastFpsTickRef.current = now;
        fpsFramesRef.current = 0;
      }

      rafRef.current = requestAnimationFrame(tick);
    };

    lastFpsTickRef.current = performance.now();
    fpsFramesRef.current = 0;
    rafRef.current = requestAnimationFrame(tick);

    return () => {
      cancelled = true;
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    };
  }, [status, minConfidence, classFilter]);

  // ── Emit stats up to parent (live KPIs, event log) ────────────────────
  useEffect(() => {
    if (!onStats) return;
    const counts: Record<string, number> = {};
    activeTracks.forEach((t) => {
      counts[t.class] = (counts[t.class] ?? 0) + 1;
    });
    onStats({
      fps,
      modelMs,
      classCounts: counts,
      activeTracks,
      totalSeen,
      sessionStartedAt: sessionStartedAtRef.current,
    });
  }, [activeTracks, fps, modelMs, totalSeen, onStats]);

  // ── Cleanup on unmount (skip when persisting across routes)
  useEffect(() => {
    if (!persist) return () => stopSession();
    return undefined;
  }, [persist, stopSession]);

  const peopleNow = activeTracks.length;

  return (
    <div className="relative w-full h-full overflow-hidden rounded-xl bg-bg-inverse">
      <video
        ref={videoRef}
        className="w-full h-full object-cover"
        playsInline
        muted
        style={{
          transform: "scaleX(-1)", // mirror so on-screen movement matches the user
        }}
      />
      <canvas
        ref={canvasRef}
        className="absolute inset-0 w-full h-full pointer-events-none"
        style={{ transform: "scaleX(-1)" }}
      />

      {/* HUD overlay */}
      {status === "running" && (
        <>
          <div className="absolute top-3 left-3 flex items-center gap-2">
            <span className="live-dot" />
            <span className="text-[10px] tabular tracking-[0.18em] uppercase text-white/90 bg-black/30 backdrop-blur px-2 py-1 rounded">
              REC · SENSOR_01
            </span>
          </div>
          <div className="absolute top-3 right-3 flex items-center gap-2 text-[10px] tabular text-white/85">
            <span className="bg-black/30 backdrop-blur px-2 py-1 rounded">
              {fps.toFixed(1)} fps · {modelMs}ms
            </span>
            <span className="bg-black/30 backdrop-blur px-2 py-1 rounded">
              COCO-SSD · webgl
            </span>
          </div>
          <div className="absolute bottom-3 left-3 text-[10px] tabular text-white/85 bg-black/30 backdrop-blur px-2 py-1 rounded">
            tracking {peopleNow} subject{peopleNow === 1 ? "" : "s"} · {totalSeen} seen this session
          </div>
          <div className="absolute bottom-3 right-3 text-[10px] tabular text-white/85 bg-black/30 backdrop-blur px-2 py-1 rounded inline-flex items-center gap-1.5">
            <Lock size={10} />
            on-device · no faces stored
          </div>
        </>
      )}

      {/* Idle / setup state */}
      {showControls && status !== "running" && (
        <StartOverlay
          status={status}
          errorMsg={errorMsg}
          onStart={startSession}
        />
      )}

      {/* Stop button (only when running) */}
      {status === "running" && (
        <button
          onClick={stopSession}
          className="absolute top-3 left-1/2 -translate-x-1/2 inline-flex items-center gap-1.5 text-[11px] tabular bg-black/40 hover:bg-black/55 backdrop-blur text-white px-3 py-1.5 rounded-full border border-white/15"
          title="Stop session"
        >
          <CameraOff size={12} />
          Stop session
        </button>
      )}
    </div>
  );
  }
);

// ── Drawing ─────────────────────────────────────────────────────────────

function drawOverlay(canvas: HTMLCanvasElement, tracks: Track[]) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  for (const t of tracks) {
    if (t.missCount > 0) continue;
    const [x, y, w, h] = t.bbox;
    const color = t.color;

    // Bounding box
    ctx.lineWidth = 2;
    ctx.strokeStyle = color;
    ctx.shadowColor = color;
    ctx.shadowBlur = 8;
    ctx.strokeRect(x, y, w, h);
    ctx.shadowBlur = 0;

    // Corner brackets — Tesla touchscreen feel
    const bracket = Math.min(18, w * 0.18, h * 0.18);
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(x, y + bracket);
    ctx.lineTo(x, y);
    ctx.lineTo(x + bracket, y);
    ctx.moveTo(x + w - bracket, y);
    ctx.lineTo(x + w, y);
    ctx.lineTo(x + w, y + bracket);
    ctx.moveTo(x + w, y + h - bracket);
    ctx.lineTo(x + w, y + h);
    ctx.lineTo(x + w - bracket, y + h);
    ctx.moveTo(x + bracket, y + h);
    ctx.lineTo(x, y + h);
    ctx.lineTo(x, y + h - bracket);
    ctx.stroke();

    // ID tag — mirrored back to readable, anchored top-left of the box
    const label = `${t.label} · ${Math.round(t.score * 100)}%`;
    ctx.font = "600 13px ui-monospace, monospace";
    const metrics = ctx.measureText(label);
    const padX = 6;
    const padY = 4;
    const tagW = metrics.width + padX * 2;
    const tagH = 18;
    const tagX = x;
    const tagY = Math.max(0, y - tagH - 2);

    ctx.fillStyle = color;
    roundRect(ctx, tagX, tagY, tagW, tagH, 3);
    ctx.fill();

    ctx.save();
    ctx.translate(tagX + tagW / 2, tagY + tagH / 2);
    ctx.scale(-1, 1); // un-mirror so the text reads normally on screen
    ctx.fillStyle = "#ffffff";
    ctx.textBaseline = "middle";
    ctx.textAlign = "center";
    ctx.fillText(label, 0, 1);
    ctx.restore();

    void padY;
  }
}

function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number
) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

// ── Idle / error overlay ────────────────────────────────────────────────

function StartOverlay({
  status,
  errorMsg,
  onStart,
}: {
  status: string;
  errorMsg: string | null;
  onStart: () => void;
}) {
  const message = useMemo(() => {
    switch (status) {
      case "requesting":
        return {
          icon: <Loader2 className="animate-spin" size={24} />,
          title: "Connecting sensor…",
          sub: "Approve sensor access to start the live session.",
        };
      case "loading-model":
        return {
          icon: <Loader2 className="animate-spin" size={24} />,
          title: "Loading object detection model…",
          sub: "First run downloads ~20 MB. You can open other tabs while it loads.",
        };
      case "denied":
        return {
          icon: <CameraOff size={24} />,
          title: "Sensor access denied",
          sub: "Allow sensor access in your browser site settings, then refresh.",
        };
      case "error":
        return {
          icon: <AlertTriangle size={24} />,
          title: "Couldn't start the detector",
          sub: errorMsg ?? "Unknown error",
        };
      default:
        return {
          icon: <Camera size={24} />,
          title: "Watch the room think",
          sub: "On-device person detection. No frames leave the browser.",
        };
    }
  }, [status, errorMsg]);

  const canStart = status === "idle" || status === "denied" || status === "error";

  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center text-white p-8 text-center bg-black/40 backdrop-blur-sm">
      <div className="w-14 h-14 rounded-full bg-white/10 border border-white/15 flex items-center justify-center mb-4">
        {message.icon}
      </div>
      <h3 className="text-lg font-semibold tracking-tight">{message.title}</h3>
      <p className="text-sm text-white/70 max-w-sm mt-1.5">{message.sub}</p>
      {canStart && (
        <button
          onClick={onStart}
          className="mt-6 inline-flex items-center gap-2 bg-accent text-text-inverse h-11 px-5 rounded-full font-semibold text-sm hover:bg-accent-bright transition-colors shadow-[var(--glow-green)]"
        >
          <Camera size={16} />
          Start live session
        </button>
      )}
      <div className="mt-6 inline-flex items-center gap-2 text-[10px] tabular tracking-[0.18em] uppercase text-white/55">
        <Maximize2 size={10} />
        Press fullscreen for a real demo
      </div>
    </div>
  );
}
