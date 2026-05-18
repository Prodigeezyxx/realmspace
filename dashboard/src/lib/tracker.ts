/**
 * Simple centroid-distance tracker.
 *
 * Maintains persistent IDs across frames using a greedy nearest-centroid
 * assignment. A real production pipeline uses ByteTrack or BoT-SORT; this
 * is the in-browser equivalent that's good enough to demo "watch yourself
 * get a persistent ID as you move around the room".
 */

export interface Detection {
  /** Original bbox from the detector: [x, y, width, height] in pixels. */
  bbox: [number, number, number, number];
  /** Class label, e.g. 'person'. */
  class: string;
  /** Confidence 0..1. */
  score: number;
}

export interface Track {
  id: number;
  label: string;          // e.g. "P-003"
  class: string;
  color: string;
  bbox: [number, number, number, number];
  cx: number;
  cy: number;
  score: number;
  firstSeen: number;      // ms epoch
  lastSeen: number;       // ms epoch
  missCount: number;      // frames since last seen
  hits: number;           // total frames matched
}

const PALETTE = [
  "#0a6dd6", // blue
  "#0aa3d9", // cyan
  "#7a3ee0", // violet
  "#1e9f3a", // green
  "#c68a00", // amber
  "#d70015", // red
  "#0091ff", // bright blue
  "#5856d6", // indigo
  "#ff2d92", // pink
  "#34c759", // light green
];

const MAX_MATCH_DISTANCE_PX = 220;
const MAX_MISS_FRAMES = 18;          // ~0.6s at 30fps
const MIN_HITS_BEFORE_PUBLISH = 2;   // confirm before counting (reduces flicker)

export class CentroidTracker {
  private tracks: Track[] = [];
  private nextId = 1;

  constructor(
    private opts: {
      classFilter?: string[];
      maxMatchDistance?: number;
      maxMissFrames?: number;
      idPrefix?: string;
    } = {}
  ) {}

  /** Update with the detections from this frame; returns the live tracks. */
  update(detections: Detection[], frameTs: number): Track[] {
    const allowed = this.opts.classFilter;
    const filtered = allowed
      ? detections.filter((d) => allowed.includes(d.class))
      : detections;

    const matchDist = this.opts.maxMatchDistance ?? MAX_MATCH_DISTANCE_PX;
    const maxMiss = this.opts.maxMissFrames ?? MAX_MISS_FRAMES;
    const prefix = this.opts.idPrefix ?? "P";

    const detCentroids = filtered.map((d) => ({
      ...d,
      cx: d.bbox[0] + d.bbox[2] / 2,
      cy: d.bbox[1] + d.bbox[3] / 2,
    }));

    // Greedy assignment: for each track, find the closest unused detection.
    const usedDet = new Set<number>();
    const updated: Track[] = [];

    for (const track of this.tracks) {
      let bestI = -1;
      let bestD = matchDist;
      for (let i = 0; i < detCentroids.length; i++) {
        if (usedDet.has(i)) continue;
        if (detCentroids[i].class !== track.class) continue;
        const dx = detCentroids[i].cx - track.cx;
        const dy = detCentroids[i].cy - track.cy;
        const d = Math.sqrt(dx * dx + dy * dy);
        if (d < bestD) {
          bestD = d;
          bestI = i;
        }
      }

      if (bestI >= 0) {
        usedDet.add(bestI);
        const m = detCentroids[bestI];
        updated.push({
          ...track,
          bbox: m.bbox,
          cx: m.cx,
          cy: m.cy,
          score: m.score,
          lastSeen: frameTs,
          missCount: 0,
          hits: track.hits + 1,
        });
      } else {
        // Unmatched track — increment miss count, drop if too many
        if (track.missCount + 1 < maxMiss) {
          updated.push({ ...track, missCount: track.missCount + 1 });
        }
      }
    }

    // New tracks for unmatched detections
    for (let i = 0; i < detCentroids.length; i++) {
      if (usedDet.has(i)) continue;
      const m = detCentroids[i];
      const id = this.nextId++;
      updated.push({
        id,
        label: `${prefix}-${id.toString().padStart(3, "0")}`,
        class: m.class,
        color: PALETTE[(id - 1) % PALETTE.length],
        bbox: m.bbox,
        cx: m.cx,
        cy: m.cy,
        score: m.score,
        firstSeen: frameTs,
        lastSeen: frameTs,
        missCount: 0,
        hits: 1,
      });
    }

    this.tracks = updated;
    return updated;
  }

  /** Currently-tracked entities (confirmed only). */
  active(): Track[] {
    return this.tracks.filter(
      (t) => t.hits >= MIN_HITS_BEFORE_PUBLISH && t.missCount === 0
    );
  }

  /** Reset to a clean state — used at session start. */
  reset() {
    this.tracks = [];
    this.nextId = 1;
  }

  /** Total unique IDs ever assigned this session. */
  totalAssigned(): number {
    return this.nextId - 1;
  }
}
