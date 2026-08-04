/**
 * realmspace — a session's event log turned into a replayable set of paths.
 *
 * The twin already knew how to play something back: it has a scrubber, a
 * play/pause and 0.5–4× speeds, and `TwinScene` resolves everybody through one
 * function. What it played was five hardcoded mock paths over a 620-second
 * constant, and a real recorded session could not be replayed at all.
 *
 * This is that function, built from the log instead.
 *
 * ## Two kinds of path, and why they must not look alike
 *
 * **Measured.** `perception.detection` carries a pixel bbox and the frame size,
 * so the centroid normalises to a real position at a real moment. That is where
 * the camera actually saw somebody.
 *
 * **Inferred.** With only `spatial.zone_enter` / `zone_exit`, all the graph knows
 * is *which zone* somebody was in — not where in it they stood. Placing them at
 * the zone's centre is the honest reading of that, but a smooth line drawn
 * between two zone centres is a route nobody walked.
 *
 * Every track therefore carries `inferred`, and the scene renders the two
 * differently. Showing a centroid path as though a camera had traced it would be
 * the twin's version of the invented ROI ratio the report just had removed:
 * plausible, unfalsifiable by the person reading it, and wrong.
 */

import type {
  DetectionPayload,
  RealmEvent,
  ZoneMovePayload,
} from "@/lib/contracts";

export interface ReplayZone {
  id: string;
  name?: string;
  /** Normalized 0..1 booth coordinates. */
  polygon?: [number, number][];
}

export interface Waypoint {
  /** ms epoch. */
  t: number;
  x: number;
  y: number;
}

export interface ReplayTrack {
  id: string;
  color: string;
  waypoints: Waypoint[];
  /** True when the path came from zone membership rather than from detections. */
  inferred: boolean;
}

export interface ReplayPosition {
  id: string;
  color: string;
  x: number;
  y: number;
  moving: boolean;
  attentionScore: number;
  inferred: boolean;
}

export interface Replay {
  tracks: ReplayTrack[];
  /** ms epoch of the first and last position in the session. */
  startAt: number;
  endAt: number;
  /** Session length in seconds — replaces the old 620-second constant. */
  durationSec: number;
  /** How many tracks came from zone membership rather than detections. */
  inferredCount: number;
  /** Positions at `t` seconds after `startAt`. */
  positionsAt: (t: number) => ReplayPosition[];
}

/** Stable per-person colour, so the same visitor keeps it across a scrub. */
const PALETTE = [
  "#3e83f7",
  "#bf5af2",
  "#00d4ff",
  "#30d158",
  "#ffd60a",
  "#ff9f0a",
  "#ff6482",
];

function colorFor(id: string): string {
  let hash = 0;
  for (let i = 0; i < id.length; i++) hash = (hash * 31 + id.charCodeAt(i)) | 0;
  return PALETTE[Math.abs(hash) % PALETTE.length];
}

/**
 * The centre of a polygon, by vertex average.
 *
 * Not the true centroid of the area — for a concave zone the average of the
 * corners can sit outside the shape. Good enough to say "they were in here",
 * which is the only claim an inferred track makes, and it avoids pretending to
 * a precision the underlying data does not have anyway.
 */
export function polygonCentre(polygon: [number, number][]): [number, number] {
  if (!polygon.length) return [0.5, 0.5];
  let sx = 0;
  let sy = 0;
  for (const [x, y] of polygon) {
    sx += x;
    sy += y;
  }
  return [sx / polygon.length, sy / polygon.length];
}

/** Detections → waypoints, in event order. Empty when the person has none. */
function measuredWaypoints(events: RealmEvent[]): Map<string, Waypoint[]> {
  const byPerson = new Map<string, Waypoint[]>();

  for (const e of events) {
    if (e.type !== "perception.detection") continue;
    const p = e.payload as DetectionPayload;
    if (!p.anonId || !p.bbox || !p.frameWidth || !p.frameHeight) continue;

    const [x1, y1, x2, y2] = p.bbox;
    const list = byPerson.get(p.anonId) ?? [];
    list.push({
      t: e.occurredAt,
      // Same centroid-then-normalise as the backend tracker and the browser's
      // zone-detect: a bbox is pixels, the booth is 0..1.
      x: (x1 + x2) / 2 / p.frameWidth,
      y: (y1 + y2) / 2 / p.frameHeight,
    });
    byPerson.set(p.anonId, list);
  }

  return byPerson;
}

/** Zone membership → waypoints at zone centres. The fallback. */
function inferredWaypoints(
  events: RealmEvent[],
  zones: ReplayZone[]
): Map<string, Waypoint[]> {
  const centres = new Map<string, [number, number]>();
  for (const z of zones) {
    if (z.polygon?.length) centres.set(z.id, polygonCentre(z.polygon));
  }

  const byPerson = new Map<string, Waypoint[]>();
  const push = (anonId: string, wp: Waypoint) => {
    const list = byPerson.get(anonId) ?? [];
    list.push(wp);
    byPerson.set(anonId, list);
  };

  for (const e of events) {
    if (e.type !== "spatial.zone_enter" && e.type !== "spatial.zone_exit") continue;
    const p = e.payload as ZoneMovePayload;
    if (!p.anonId || !p.zoneId) continue;
    const centre = centres.get(p.zoneId);
    if (!centre) continue; // a zone nobody drew has no centre to stand in

    // Both the entry and the exit sit at the same point: the person was in that
    // zone for the whole stretch between them. Two waypoints rather than one is
    // what stops the interpolation sliding them across the room the moment they
    // arrive.
    push(p.anonId, { t: e.occurredAt, x: centre[0], y: centre[1] });
  }

  return byPerson;
}

/**
 * Build a replay from a session's events.
 *
 * Detections win wherever they exist. A person with a single detection is still
 * measured — one confirmed position beats a guessed one — but needs a second
 * waypoint to be visible over any span, so their track is held at that point.
 */
export function buildReplay(events: RealmEvent[], zones: ReplayZone[] = []): Replay {
  const measured = measuredWaypoints(events);
  const inferred = inferredWaypoints(events, zones);

  const tracks: ReplayTrack[] = [];
  const ids = new Set([...measured.keys(), ...inferred.keys()]);

  for (const id of ids) {
    const fromDetections = measured.get(id) ?? [];
    const waypoints = fromDetections.length ? fromDetections : inferred.get(id) ?? [];
    if (!waypoints.length) continue;

    waypoints.sort((a, b) => a.t - b.t);
    tracks.push({
      id,
      color: colorFor(id),
      waypoints,
      inferred: fromDetections.length === 0,
    });
  }

  let startAt = Infinity;
  let endAt = -Infinity;
  for (const track of tracks) {
    startAt = Math.min(startAt, track.waypoints[0].t);
    endAt = Math.max(endAt, track.waypoints[track.waypoints.length - 1].t);
  }
  if (!tracks.length) {
    startAt = 0;
    endAt = 0;
  }

  const durationSec = Math.max(0, (endAt - startAt) / 1000);

  const positionsAt = (t: number): ReplayPosition[] => {
    const at = startAt + t * 1000;
    const out: ReplayPosition[] = [];

    for (const track of tracks) {
      const wp = track.waypoints;
      const first = wp[0];
      const last = wp[wp.length - 1];
      // Outside their own span they are simply not in the room. The twin shows
      // who was present at that moment, not everyone who ever attended.
      if (at < first.t || at > last.t) continue;

      if (wp.length === 1) {
        out.push({
          id: track.id,
          color: track.color,
          x: first.x,
          y: first.y,
          moving: false,
          attentionScore: 0,
          inferred: track.inferred,
        });
        continue;
      }

      for (let i = 0; i < wp.length - 1; i++) {
        const a = wp[i];
        const b = wp[i + 1];
        if (at < a.t || at > b.t) continue;

        const span = b.t - a.t;
        const u = span > 0 ? (at - a.t) / span : 0;
        out.push({
          id: track.id,
          color: track.color,
          x: a.x + (b.x - a.x) * u,
          y: a.y + (b.y - a.y) * u,
          moving: Math.abs(b.x - a.x) + Math.abs(b.y - a.y) > 0.001,
          attentionScore: 0,
          inferred: track.inferred,
        });
        break;
      }
    }

    return out;
  };

  return {
    tracks,
    startAt,
    endAt,
    durationSec,
    inferredCount: tracks.filter((t) => t.inferred).length,
    positionsAt,
  };
}
