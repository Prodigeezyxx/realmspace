import type { Track } from "@/lib/tracker";

export interface DetectorStats {
  fps: number;
  modelMs: number;
  classCounts: Record<string, number>;
  activeTracks: Track[];
  totalSeen: number;
  sessionStartedAt: number | null;
  /** Intrinsic sensor frame size (matches track bbox / centroid space). */
  frameWidth: number;
  frameHeight: number;
}
