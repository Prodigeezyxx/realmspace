/**
 * Spatial-event deriver — the P2 linchpin producer.
 *
 * Zone polygons (normalized 0..1 booth coords, from the event context) +
 * live tracked persons → spatial.zone_enter / zone_exit / dwell / passby
 * onto the durable bus (docs/event-bus-spec.md §3). Runs in the browser next
 * to the tracker because that is where zones and tracks share one coordinate
 * space; the edge-side consumer version needs a booth→camera mapping first.
 *
 * Session-hygiene heuristics (docs/research CHI '26 — 71% of raw sessions
 * invalid without these):
 * - Confirm window: a membership change must persist ZONE_CONFIRM_MS before
 *   an event fires (kills boundary flicker; enter/exit timestamps still use
 *   the real crossing time, so dwell stays accurate).
 * - Duration sanity: dwell < MIN_DWELL_SEC is dropped as noise.
 * - Dropout flags: when a track disappears inside a zone, its exit is
 *   emitted with reason "dropout" so the scorecard can discount it.
 * - Fragmented-track merge (one person → multiple IDs) is NOT solved here;
 *   it is a tracker-level concern. Dropout flags mark the seams so later
 *   consumers can handle it.
 */

import { emit as busEmit } from "@/lib/bus";
import { getEventContext } from "@/lib/event-context";
import type { Zone } from "@/lib/session/types";
import type { Track } from "@/lib/tracker";
import { pointInPolygon } from "@/skills/zone-detect";

const ZONE_CONFIRM_MS = 600;
const MIN_DWELL_SEC = 1.0;
/** Normalized distance to a zone edge that counts as "passed by". */
const PASSBY_RADIUS = 0.08;

export type ZoneExitReason = "move" | "leave" | "dropout" | "session_end";

export interface SpatialTransition {
  kind: "enter" | "exit" | "dwell" | "passby";
  anonId: string;
  zoneId: string;
  zoneName: string;
  durationSec?: number;
}

interface TrackZoneState {
  /** Confirmed zone membership, if any. */
  confirmed: string | null;
  /** When the confirmed membership actually began (first polygon crossing). */
  enteredAt: number;
  /** Pending membership change awaiting the confirm window. */
  candidate: { zoneId: string | null; since: number } | null;
  lastSeen: number;
  /** Closest normalized distance seen per zone (passby detection). */
  minDistByZone: Record<string, number>;
  /** Zones ever confirmed-entered (excluded from passby). */
  enteredZones: Set<string>;
}

function distToSegment(
  px: number,
  py: number,
  x1: number,
  y1: number,
  x2: number,
  y2: number
): number {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const lenSq = dx * dx + dy * dy;
  const t =
    lenSq === 0
      ? 0
      : Math.max(0, Math.min(1, ((px - x1) * dx + (py - y1) * dy) / lenSq));
  const ex = x1 + t * dx - px;
  const ey = y1 + t * dy - py;
  return Math.sqrt(ex * ex + ey * ey);
}

function distToPolygon(
  x: number,
  y: number,
  poly: [number, number][]
): number {
  if (pointInPolygon(x, y, poly)) return 0;
  let min = Infinity;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    min = Math.min(
      min,
      distToSegment(x, y, poly[j][0], poly[j][1], poly[i][0], poly[i][1])
    );
  }
  return min;
}

function safeEmit(
  type: "spatial.zone_enter" | "spatial.zone_exit" | "spatial.dwell" | "spatial.passby",
  payload: Record<string, unknown>
) {
  try {
    busEmit(type, payload);
  } catch {
    /* bus must never break the detector loop */
  }
}

/** Mirror of the tracker's publish threshold — unconfirmed tracks are ignored
 * so single-frame false positives never create zone state. */
const MIN_HITS = 2;

export class SpatialDeriver {
  private states = new Map<number, TrackZoneState>();

  /**
   * Feed one frame of retained tracks (including ones missing frames but not
   * yet dropped — a one-frame hiccup is not an exit). Emits any due spatial
   * events and returns the transitions for the UI event feed.
   */
  update(
    tracks: Track[],
    frameWidth: number,
    frameHeight: number,
    now: number
  ): SpatialTransition[] {
    const zones = getEventContext().zones.filter((z) => z.polygon?.length);
    const transitions: SpatialTransition[] = [];
    const seenIds = new Set<number>();

    for (const t of tracks) {
      if (t.hits < MIN_HITS) continue;
      seenIds.add(t.id);
      const nx = t.cx / frameWidth;
      const ny = t.cy / frameHeight;

      let raw: string | null = null;
      for (const z of zones) {
        if (pointInPolygon(nx, ny, z.polygon!)) {
          raw = z.id;
          break;
        }
      }

      let st = this.states.get(t.id);
      if (!st) {
        st = {
          confirmed: null,
          enteredAt: 0,
          candidate: null,
          lastSeen: t.lastSeen,
          minDistByZone: {},
          enteredZones: new Set(),
        };
        this.states.set(t.id, st);
      }
      st.lastSeen = t.lastSeen;

      for (const z of zones) {
        const d = distToPolygon(nx, ny, z.polygon!);
        if (d < (st.minDistByZone[z.id] ?? Infinity)) st.minDistByZone[z.id] = d;
      }

      this.advance(t, st, raw, zones, now, transitions);
    }

    // Dropouts: tracks that vanished since the last frame.
    for (const [id, st] of this.states) {
      if (!seenIds.has(id)) {
        this.finalize(id, st, "dropout", st.lastSeen, zones, transitions);
        this.states.delete(id);
      }
    }

    return transitions;
  }

  /** Zone under the candidate window state machine for one track. */
  private advance(
    t: Track,
    st: TrackZoneState,
    raw: string | null,
    zones: Zone[],
    now: number,
    out: SpatialTransition[]
  ) {
    if (st.confirmed === null) {
      if (raw === null) {
        st.candidate = null;
        return;
      }
      if (st.candidate?.zoneId === raw) {
        if (now - st.candidate.since >= ZONE_CONFIRM_MS) {
          st.confirmed = raw;
          st.enteredAt = st.candidate.since;
          st.enteredZones.add(raw);
          st.candidate = null;
          safeEmit("spatial.zone_enter", { anonId: t.label, zoneId: raw });
          out.push(this.describe("enter", t.label, raw, zones));
        }
      } else {
        st.candidate = { zoneId: raw, since: now };
      }
      return;
    }

    if (raw === st.confirmed) {
      st.candidate = null;
      return;
    }

    // Person has left the confirmed zone (for another zone or open floor).
    if (st.candidate && st.candidate.zoneId === raw) {
      if (now - st.candidate.since >= ZONE_CONFIRM_MS) {
        const exitAt = st.candidate.since;
        this.emitExit(
          t.label,
          st,
          raw !== null ? "move" : "leave",
          exitAt,
          zones,
          out
        );
        st.confirmed = null;
        // Immediate promotion: the confirm window already covers the new zone.
        if (raw !== null) {
          st.confirmed = raw;
          st.enteredAt = exitAt;
          st.enteredZones.add(raw);
          safeEmit("spatial.zone_enter", { anonId: t.label, zoneId: raw });
          out.push(this.describe("enter", t.label, raw, zones));
        }
        st.candidate = null;
      }
    } else {
      st.candidate = { zoneId: raw, since: now };
    }
  }

  /** Emit zone_exit + (if sane) dwell for the confirmed zone. */
  private emitExit(
    anonId: string,
    st: TrackZoneState,
    reason: ZoneExitReason,
    exitAt: number,
    zones: Zone[],
    out: SpatialTransition[]
  ) {
    if (st.confirmed === null) return;
    const zoneId = st.confirmed;
    const durationSec = (exitAt - st.enteredAt) / 1000;
    safeEmit("spatial.zone_exit", { anonId, zoneId, reason });
    out.push(this.describe("exit", anonId, zoneId, zones));
    if (durationSec >= MIN_DWELL_SEC) {
      safeEmit("spatial.dwell", {
        anonId,
        zoneId,
        durationSec: Number(durationSec.toFixed(1)),
      });
      out.push({
        ...this.describe("dwell", anonId, zoneId, zones),
        durationSec: Number(durationSec.toFixed(1)),
      });
    }
  }

  /**
   * Close out a track: exit any confirmed zone, then evaluate passbys
   * (came within PASSBY_RADIUS of a zone but never entered it).
   */
  private finalize(
    trackId: number,
    st: TrackZoneState,
    reason: ZoneExitReason,
    at: number,
    zones: Zone[],
    out: SpatialTransition[]
  ) {
    const anonId = `P-${trackId.toString().padStart(3, "0")}`;
    if (st.confirmed !== null) {
      this.emitExit(anonId, st, reason, at, zones, out);
      st.confirmed = null;
    }
    for (const z of zones) {
      if (st.enteredZones.has(z.id)) continue;
      const d = st.minDistByZone[z.id];
      if (d !== undefined && d <= PASSBY_RADIUS) {
        safeEmit("spatial.passby", {
          anonId,
          adjacentZoneId: z.id,
          closestDist: Number(d.toFixed(3)),
        });
        out.push(this.describe("passby", anonId, z.id, zones));
      }
    }
  }

  /** End-of-session: close every open track with the given reason. */
  flushAll(reason: ZoneExitReason = "session_end", now: number = Date.now()) {
    const zones = getEventContext().zones.filter((z) => z.polygon?.length);
    const out: SpatialTransition[] = [];
    for (const [id, st] of this.states) {
      this.finalize(id, st, reason, now, zones, out);
    }
    this.states.clear();
    return out;
  }

  /** Drop all state without emitting (e.g. fresh session start). */
  reset() {
    this.states.clear();
  }

  private describe(
    kind: SpatialTransition["kind"],
    anonId: string,
    zoneId: string,
    zones: Zone[]
  ): SpatialTransition {
    return {
      kind,
      anonId,
      zoneId,
      zoneName: zones.find((z) => z.id === zoneId)?.name ?? zoneId,
    };
  }
}

export const spatialDeriver = new SpatialDeriver();
