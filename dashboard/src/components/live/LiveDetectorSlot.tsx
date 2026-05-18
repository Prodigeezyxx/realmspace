"use client";

import { useEffect, useRef } from "react";

import { useLiveSessionControl } from "./LiveSessionProvider";
import { cn } from "@/lib/utils";

/** Mount point — WebcamDetector portals here on /live (video + tracking overlay) */
export function LiveDetectorSlot({ className }: { className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const { registerDetectorSlot } = useLiveSessionControl();

  useEffect(() => {
    registerDetectorSlot(ref.current);
    return () => registerDetectorSlot(null);
  }, [registerDetectorSlot]);

  return (
    <div
      ref={ref}
      className={cn(
        "relative w-full h-full min-h-[280px] overflow-hidden rounded-xl bg-bg-inverse",
        className
      )}
    />
  );
}
