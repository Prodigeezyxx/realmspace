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

import { WebcamDetector, type WebcamDetectorHandle } from "@/components/viz/WebcamDetector";
import { liveSessionActions } from "@/lib/live-session/store";

interface LiveSessionContextValue {
  stream: MediaStream | null;
  registerStream: (s: MediaStream | null) => void;
  start: () => void;
  stop: () => void;
}

const LiveSessionContext = createContext<LiveSessionContextValue | null>(null);

export function useLiveSessionControl() {
  const ctx = useContext(LiveSessionContext);
  if (!ctx) throw new Error("useLiveSessionControl requires LiveSessionProvider");
  return ctx;
}

/** @deprecated use useLiveSessionControl */
export function useLiveDetectorSlot() {
  return useLiveSessionControl();
}

export function LiveSessionProvider({ children }: { children: ReactNode }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const detectorRef = useRef<WebcamDetectorHandle>(null);
  const [stream, setStream] = useState<MediaStream | null>(null);

  const registerStream = useCallback((s: MediaStream | null) => {
    setStream(s);
  }, []);

  const start = useCallback(() => {
    detectorRef.current?.start();
  }, []);

  const stop = useCallback(() => {
    detectorRef.current?.stop();
  }, []);

  useEffect(() => {
    void liveSessionActions.ensureModelPreloaded();
  }, []);

  return (
    <LiveSessionContext.Provider
      value={{ stream, registerStream, start, stop }}
    >
      {children}
      {/* Single persistent host — never unmounts when changing routes */}
      <div
        ref={hostRef}
        className="fixed left-0 top-0 w-[640px] h-[360px] overflow-hidden pointer-events-none opacity-0 -z-50"
        aria-hidden
      >
        <WebcamDetector
          ref={detectorRef}
          persist
          showControls={false}
          classFilter={["person"]}
          minConfidence={0.5}
          onStats={liveSessionActions.onStats}
          onStatusChange={liveSessionActions.setStatus}
          onStreamChange={registerStream}
        />
      </div>
    </LiveSessionContext.Provider>
  );
}
