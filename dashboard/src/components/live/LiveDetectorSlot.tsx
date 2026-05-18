"use client";

import {
  AlertTriangle,
  Camera,
  CameraOff,
  Loader2,
  Lock,
} from "lucide-react";
import { useEffect, useRef } from "react";

import { useLiveSessionControl } from "./LiveSessionProvider";
import { useLiveSessionStore } from "@/lib/live-session/store";
import { getModelLoadStage } from "@/lib/live-session/model-cache";
import { cn } from "@/lib/utils";

/** Live page viewport — mirrors the persistent detector stream */
export function LiveDetectorSlot({ className }: { className?: string }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const { stream, start, stop } = useLiveSessionControl();
  const { status, errorMsg, modelLoadStage } = useLiveSessionStore();

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    v.srcObject = stream;
    if (stream) void v.play().catch(() => undefined);
  }, [stream]);

  const isRunning = status === "running";

  return (
    <div
      className={cn(
        "relative w-full h-full overflow-hidden rounded-xl bg-bg-inverse",
        className
      )}
    >
      <video
        ref={videoRef}
        className={cn(
          "w-full h-full object-cover",
          isRunning ? "opacity-100" : "opacity-30"
        )}
        playsInline
        muted
        style={{ transform: "scaleX(-1)" }}
      />

      {isRunning && (
        <>
          <div className="absolute top-3 left-3 flex items-center gap-2">
            <span className="live-dot" />
            <span className="text-[10px] tabular tracking-[0.18em] uppercase text-white/90 bg-black/30 backdrop-blur px-2 py-1 rounded">
              REC · CAM_01 · runs in background
            </span>
          </div>
          <button
            type="button"
            onClick={stop}
            className="absolute top-3 left-1/2 -translate-x-1/2 inline-flex items-center gap-1.5 text-[11px] tabular bg-black/40 hover:bg-black/55 backdrop-blur text-white px-3 py-1.5 rounded-full border border-white/15"
          >
            <CameraOff size={12} />
            Stop session
          </button>
          <div className="absolute bottom-3 right-3 text-[10px] tabular text-white/85 bg-black/30 backdrop-blur px-2 py-1 rounded inline-flex items-center gap-1.5">
            <Lock size={10} />
            on-device · switch tabs freely
          </div>
        </>
      )}

      {!isRunning && (
        <IdleOverlay
          status={status}
          errorMsg={errorMsg}
          modelLoadStage={modelLoadStage}
          onStart={start}
        />
      )}
    </div>
  );
}

function IdleOverlay({
  status,
  errorMsg,
  modelLoadStage,
  onStart,
}: {
  status: string;
  errorMsg: string | null;
  modelLoadStage: string;
  onStart: () => void;
}) {
  const canStart =
    status === "idle" || status === "denied" || status === "error";
  const loading = status === "requesting" || status === "loading-model";

  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center text-white p-8 text-center bg-black/50 backdrop-blur-sm">
      <div className="w-14 h-14 rounded-full bg-white/10 border border-white/15 flex items-center justify-center mb-4">
        {loading ? (
          <Loader2 className="animate-spin" size={24} />
        ) : status === "denied" ? (
          <CameraOff size={24} />
        ) : status === "error" ? (
          <AlertTriangle size={24} />
        ) : (
          <Camera size={24} />
        )}
      </div>
      <h3 className="text-lg font-semibold tracking-tight">
        {status === "requesting"
          ? "Asking for camera access…"
          : status === "loading-model"
            ? "Loading detection model…"
            : status === "denied"
              ? "Camera access denied"
              : status === "error"
                ? "Couldn't start the detector"
                : "Watch the room think"}
      </h3>
      <p className="text-sm text-white/70 max-w-sm mt-1.5">
        {status === "loading-model"
          ? `${modelLoadStage} — open Twin or Agents anytime; detection keeps loading.`
          : status === "denied"
            ? "Allow camera in browser settings, then try again."
            : status === "error"
              ? (errorMsg ?? "Unknown error")
              : getModelLoadStage() === "ready"
                ? "Model preloaded. Start live, then navigate anywhere — data stays in sync."
                : "First visit downloads ~28 MB (~15–30s). Preload runs in the status bar."}
      </p>
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
