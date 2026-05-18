"use client";

import { usePathname } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

import {
  WebcamDetector,
  type WebcamDetectorHandle,
} from "@/components/viz/WebcamDetector";
import { liveSessionActions } from "@/lib/live-session/store";
import { cn } from "@/lib/utils";

interface LiveSessionContextValue {
  registerDetectorSlot: (el: HTMLElement | null) => void;
  start: () => void;
  stop: () => void;
}

const LiveSessionContext = createContext<LiveSessionContextValue | null>(null);

export function useLiveSessionControl() {
  const ctx = useContext(LiveSessionContext);
  if (!ctx) throw new Error("useLiveSessionControl requires LiveSessionProvider");
  return ctx;
}

export function LiveSessionProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const offscreenRef = useRef<HTMLDivElement>(null);
  const detectorRef = useRef<WebcamDetectorHandle>(null);
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  const onLivePage = pathname.startsWith("/live");

  const registerDetectorSlot = useCallback((el: HTMLElement | null) => {
    setSlot(el);
  }, []);

  const start = useCallback(() => {
    detectorRef.current?.start();
  }, []);

  const stop = useCallback(() => {
    detectorRef.current?.stop();
  }, []);

  const portalTarget =
    onLivePage && slot ? slot : offscreenRef.current;

  return (
    <LiveSessionContext.Provider
      value={{ registerDetectorSlot, start, stop }}
    >
      {children}
      <div
        ref={offscreenRef}
        className={cn(
          "overflow-hidden",
          onLivePage && slot
            ? "hidden"
            : "fixed left-0 top-0 w-px h-px opacity-0 pointer-events-none -z-50"
        )}
        aria-hidden={!onLivePage}
      />
      {portalTarget &&
        createPortal(
          <WebcamDetector
            ref={detectorRef}
            persist
            showControls={onLivePage}
            classFilter={["person"]}
            minConfidence={0.5}
            onStats={liveSessionActions.onStats}
            onStatusChange={liveSessionActions.setStatus}
          />,
          portalTarget
        )}
    </LiveSessionContext.Provider>
  );
}
