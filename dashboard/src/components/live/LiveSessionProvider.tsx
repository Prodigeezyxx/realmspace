"use client";

import {
  createContext,
  useCallback,
  useContext,
  useRef,
  useState,
  type ReactNode,
} from "react";

import {
  WebcamDetector,
  type WebcamDetectorHandle,
} from "@/components/viz/WebcamDetector";
import { liveSessionActions } from "@/lib/live-session/store";

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
 * Detector always mounts here — never moved via portal (avoids remount / session end).
 * /live mirrors stream + overlay from the global live store.
 */
export function LiveSessionProvider({ children }: { children: ReactNode }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const detectorRef = useRef<WebcamDetectorHandle>(null);
  const [stream, setStream] = useState<MediaStream | null>(null);

  const start = useCallback(() => {
    detectorRef.current?.start();
  }, []);

  const stop = useCallback(() => {
    detectorRef.current?.stop();
  }, []);

  return (
    <LiveSessionContext.Provider value={{ stream, start, stop }}>
      {children}
      <div
        ref={hostRef}
        className="fixed left-0 top-0 w-[640px] h-[360px] overflow-hidden opacity-0 pointer-events-none -z-50"
        aria-hidden
      >
        <WebcamDetector
          ref={detectorRef}
          persist
          showControls={false}
          renderOverlay={false}
          classFilter={["person"]}
          minConfidence={0.5}
          onStats={liveSessionActions.onStats}
          onStatusChange={liveSessionActions.setStatus}
          onStreamChange={setStream}
        />
      </div>
    </LiveSessionContext.Provider>
  );
}
