/**
 * realmspace — twin replay reducer.
 *
 * Rebuilds anonymous person tracks from a recorded session on the durable bus:
 * `perception.detection` bboxes → per-person waypoints in booth coords,
 * `spatial.zone_enter` → zonesVisited, `surface.interaction` → surfaces,
 * `spatial.dwell` → dwell totals. Same PersonTrack shape the twin already
 * renders — data swap, not a rebuild.
 *
 * Coordinate space matches the live path (`twin-emit.ts`): sensor bbox
 * centroid is mirrored on X and normalized to the booth's 0..1 square.
 * Recorded events may lack frame dims (pre-contract sessions) → 640×480.
 */

import type { RealmEvent } from "@/lib/contracts";
import { palette, type PersonTrack } from "@/lib/mock/people";

const DEFAULT_FRAME_W = 640;
const DEFAULT_FRAME_H = 480;
/** Normalized-space move threshold below which a detection is not a new waypoint. */
const MOVE_EPS = 0.02;
/** Max seconds a person can linger without emitting a fresh waypoint. */
const STALE_SEC = 3;

export interface ReplaySessionData {
  tracks: PersonTrack[];
  /** Wall-clock seconds spanned by the session (first → last event). */
  durationSec: number;
  /** Session start (ms epoch) — the virtual clock's t=0. */
  startAt: number;
  eventCount: number;
  detectionCount: number;
}

interface Waypoint {
  x: number;
  y: number;
  t: number;
}

export function reduceReplaySession(events: RealmEvent[]): ReplaySessionData {
  const sorted = [...events].sort((a, b) => a.occurredAt - b.occurredAt);
  const firstAt = sorted[0]?.occurredAt ?? 0;
  const lastAt = sorted[sorted.length - 1]?.occurredAt ?? firstAt;
  const durationSec = Math.max(1, (lastAt - firstAt) / 1000);

  const waypointsByPerson = new Map<string, Waypoint[]>();
  const zonesByPerson = new Map<string, string[]>();
  const surfacesByPerson = new Map<string, string[]>();
  const dwellByPerson = new Map<string, number>();
  const order: string[] = [];
  let detectionCount = 0;

  for (const e of sorted) {
    const t = (e.occurredAt - firstAt) / 1000;
    if (e.type === "perception.detection") {
      const p = e.payload as {
        anonId?: string;
        bbox?: unknown;
        frameWidth?: number;
        frameHeight?: number;
      };
      const id = p?.anonId;
      const bbox = p?.bbox;
      if (!id || !Array.isArray(bbox) || bbox.length < 4) continue;
      detectionCount += 1;
      const fw = p.frameWidth ?? DEFAULT_FRAME_W;
      const fh = p.frameHeight ?? DEFAULT_FRAME_H;
      const cx = (bbox[0] as number) + (bbox[2] as number) / 2;
      const cy = (bbox[1] as number) + (bbox[3] as number) / 2;
      const x = 1 - cx / fw;
      const y = cy / fh;
      let pts = waypointsByPerson.get(id);
      if (!pts) {
        pts = [];
        waypointsByPerson.set(id, pts);
        order.push(id);
      }
      const prev = pts[pts.length - 1];
      if (
        !prev ||
        Math.abs(prev.x - x) + Math.abs(prev.y - y) > MOVE_EPS ||
        t - prev.t >= STALE_SEC
      ) {
        pts.push({ x, y, t });
      }
    } else if (e.type === "spatial.zone_enter") {
      const p = e.payload as { anonId?: string; zoneId?: string };
      if (p?.anonId && p.zoneId) {
        const zones = zonesByPerson.get(p.anonId) ?? [];
        if (!zones.includes(p.zoneId)) zones.push(p.zoneId);
        zonesByPerson.set(p.anonId, zones);
      }
    } else if (e.type === "surface.interaction") {
      const p = e.payload as { anonId?: string; surfaceId?: string };
      if (p?.anonId && p.surfaceId) {
        const surfaces = surfacesByPerson.get(p.anonId) ?? [];
        if (!surfaces.includes(p.surfaceId)) surfaces.push(p.surfaceId);
        surfacesByPerson.set(p.anonId, surfaces);
      }
    } else if (e.type === "spatial.dwell") {
      const p = e.payload as { anonId?: string; durationSec?: number };
      if (p?.anonId && typeof p.durationSec === "number") {
        dwellByPerson.set(
          p.anonId,
          (dwellByPerson.get(p.anonId) ?? 0) + p.durationSec
        );
      }
    }
  }

  const maxDwell = Math.max(1, ...dwellByPerson.values());
  const tracks: PersonTrack[] = order
    .map((id, i) => {
      const pts = waypointsByPerson.get(id)!;
      const dwellSec = dwellByPerson.get(id) ?? pts[pts.length - 1]?.t ?? 0;
      const attentionScore =
        dwellByPerson.size > 0
          ? clamp01(dwellSec / maxDwell)
          : clamp01(pts.length > 1 ? pts[pts.length - 1].t / durationSec : 0.5);
      return {
        id,
        color: palette[i % palette.length],
        waypoints: pts.map((w) => [w.x, w.y, w.t] as [number, number, number]),
        zonesVisited: zonesByPerson.get(id) ?? [],
        surfacesTriggered: surfacesByPerson.get(id) ?? [],
        totalDwellSeconds: Math.round(dwellSec),
        attentionScore,
      };
    })
    .sort((a, b) => b.totalDwellSeconds - a.totalDwellSeconds);

  return {
    tracks,
    durationSec,
    startAt: firstAt,
    eventCount: sorted.length,
    detectionCount,
  };
}

function clamp01(v: number): number {
  return Math.min(0.95, Math.max(0.1, v));
}

/** Interpolated positions at time `t` (seconds since session start). */
export function positionsAtFrom(tracks: PersonTrack[], t: number) {
  return tracks
    .map((p) => {
      const wp = p.waypoints;
      if (t < wp[0][2]) return null;
      if (t > wp[wp.length - 1][2]) return null;
      for (let i = 0; i < wp.length - 1; i++) {
        const [x1, y1, t1] = wp[i];
        const [x2, y2, t2] = wp[i + 1];
        if (t >= t1 && t <= t2) {
          const u = (t - t1) / Math.max(1, t2 - t1);
          return {
            id: p.id,
            color: p.color,
            x: x1 + (x2 - x1) * u,
            y: y1 + (y2 - y1) * u,
            moving: Math.abs(x2 - x1) + Math.abs(y2 - y1) > 0.001,
            attentionScore: p.attentionScore,
          };
        }
      }
      return null;
    })
    .filter(Boolean) as {
    id: string;
    color: string;
    x: number;
    y: number;
    moving: boolean;
    attentionScore: number;
  }[];
}
