import type { Track } from "@/lib/tracker";

export interface DetectorStats {
  fps: number;
  modelMs: number;
  classCounts: Record<string, number>;
  activeTracks: Track[];
  /**
   * All retained tracks, including those missing frames but not yet dropped
   * (missCount > 0). The spatial deriver needs these so a one-frame detection
   * hiccup is not mistaken for a person leaving.
   */
  tracks: Track[];
  totalSeen: number;
  sessionStartedAt: number | null;
  /** Intrinsic sensor frame size (matches track bbox / centroid space). */
  frameWidth: number;
  frameHeight: number;
}
