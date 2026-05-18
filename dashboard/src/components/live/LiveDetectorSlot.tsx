"use client";

import {
  AlertTriangle,
  Camera,
  CameraOff,
  Loader2,
  Lock,
} from "lucide-react";
import { useEffect, useRef } from "react";

import { drawTrackOverlay } from "@/lib/live-session/overlay";
import {
  SENSOR_H,
  SENSOR_W,
} from "@/lib/live-session/twin-emit";
import {
  useLiveSession,
  useLiveSessionStore,
  type LiveDetectorStatus,
} from "@/lib/live-session/store";
import { cn } from "@/lib/utils";

import { useLiveSessionControl } from "./LiveSessionProvider";

/** Mirrors the persistent sensor stream + tracking overlay on /live */
export function LiveDetectorSlot({ className }: { className?: string }) {
  const { stream, start, stop } = useLiveSessionControl();
  const { status, errorMsg } = useLiveSessionStore();
  const stats = useLiveSession((s) => s.stats);

  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    if (stream) {
      video.srcObject = stream;
      void video.play().catch(() => {});
    } else {
      video.srcObject = null;
    }
  }, [stream]);

  useEffect(() => {
    if (status !== "running" || !stats) return;
    let raf = 0;
    const tick = () => {
      const canvas = canvasRef.current;
      if (canvas) {
        drawTrackOverlay(canvas, stats.activeTracks, SENSOR_W, SENSOR_H);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [status, stats]);

  const peopleNow = stats?.activeTracks.length ?? 0;
  const fps = stats?.fps ?? 0;
  const modelMs = stats?.modelMs ?? 0;
  const totalSeen = stats?.totalSeen ?? 0;

  return (
    <div
      className={cn(
        "relative w-full h-full min-h-[280px] overflow-hidden rounded-xl bg-bg-inverse",
        className
      )}
    >
      <video
        ref={videoRef}
        className="w-full h-full object-cover"
        playsInline
        muted
        style={{ transform: "scaleX(-1)" }}
      />
      <canvas
        ref={canvasRef}
        className="absolute inset-0 w-full h-full pointer-events-none"
        style={{ transform: "scaleX(-1)" }}
      />

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
          </div>
          <div className="absolute bottom-3 left-3 text-[10px] tabular text-white/85 bg-black/30 backdrop-blur px-2 py-1 rounded">
            tracking {peopleNow} subject{peopleNow === 1 ? "" : "s"} · {totalSeen}{" "}
            seen this session
          </div>
          <div className="absolute bottom-3 right-3 text-[10px] tabular text-white/85 bg-black/30 backdrop-blur px-2 py-1 rounded inline-flex items-center gap-1.5">
            <Lock size={10} />
            on-device · no faces stored
          </div>
          <button
            type="button"
            onClick={stop}
            className="absolute top-3 left-1/2 -translate-x-1/2 inline-flex items-center gap-1.5 text-[11px] tabular bg-black/40 hover:bg-black/55 backdrop-blur text-white px-3 py-1.5 rounded-full border border-white/15"
            title="Stop session"
          >
            <CameraOff size={12} />
            Stop session
          </button>
        </>
      )}

      {status !== "running" && (
        <StartOverlay status={status} errorMsg={errorMsg} onStart={start} />
      )}
    </div>
  );
}

function StartOverlay({
  status,
  errorMsg,
  onStart,
}: {
  status: LiveDetectorStatus;
  errorMsg: string | null;
  onStart: () => void;
}) {
  const message = (() => {
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
  })();

  const canStart =
    status === "idle" || status === "denied" || status === "error";

  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center text-white p-8 text-center bg-black/40 backdrop-blur-sm">
      <div className="w-14 h-14 rounded-full bg-white/10 border border-white/15 flex items-center justify-center mb-4">
        {message.icon}
      </div>
      <h3 className="text-lg font-semibold tracking-tight">{message.title}</h3>
      <p className="text-sm text-white/70 max-w-sm mt-1.5">{message.sub}</p>
      {canStart && (
        <button
          type="button"
          onClick={onStart}
          className="mt-6 inline-flex items-center gap-2 bg-accent text-text-inverse h-11 px-5 rounded-full font-semibold text-sm hover:bg-accent-bright transition-colors shadow-[var(--glow-green)]"
        >
          <Camera size={16} />
          Start live session
        </button>
      )}
    </div>
  );
}
