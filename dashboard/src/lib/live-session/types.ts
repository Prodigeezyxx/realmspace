import type { Track } from "@/lib/tracker";

export interface DetectorStats {
  fps: number;
  modelMs: number;
  classCounts: Record<string, number>;
  activeTracks: Track[];
  totalSeen: number;
  sessionStartedAt: number | null;
}
