"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { detectorRuntime } from "@/lib/live-session/detector-runtime";

interface LiveSessionContextValue {
  stream: MediaStream | null;
  start: () => void;
  stop: () => void;
}

const LiveSessionContext = createContext<LiveSessionContextValue | null>(null);

export function useLiveSessionControl() {
  const ctx = useContext(LiveSessionContext);
  if (!ctx) throw new Error("useLiveSessionControl requires LiveSessionProvider");
  return ctx;
}

/**
 * Sensor + inference live in detectorRuntime (module singleton).
 * This provider only hosts the hidden video element and stream mirror for /live.
 */
export function LiveSessionProvider({ children }: { children: ReactNode }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [stream, setStream] = useState<MediaStream | null>(null);

  useEffect(() => {
    detectorRuntime.setStreamListener(setStream);
    return () => detectorRuntime.setStreamListener(() => {});
  }, []);

  useEffect(() => {
    detectorRuntime.attachVideo(videoRef.current);
  }, []);

  const start = useCallback(() => {
    void detectorRuntime.start();
  }, []);

  const stop = useCallback(() => {
    detectorRuntime.stop();
  }, []);

  return (
    <LiveSessionContext.Provider value={{ stream, start, stop }}>
      {children}
      <div
        className="fixed left-0 top-0 w-[640px] h-[360px] overflow-hidden opacity-0 pointer-events-none -z-50"
        aria-hidden
      >
        <video
          ref={videoRef}
          className="w-full h-full object-cover"
          playsInline
          muted
          style={{ transform: "scaleX(-1)" }}
        />
      </div>
    </LiveSessionContext.Provider>
  );
}
