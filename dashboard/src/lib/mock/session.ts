/**
 * Single source of truth for the seeded **demo session** (Lagos Showroom).
 *
 * This file does two things:
 *   1. Exports the demo session in the canonical `Session` shape used by the
 *      whole app (see `@/lib/session/types`), so the session store can hold
 *      both user-created sessions and the demo in one list.
 *   2. Keeps the legacy named exports (`session`, `zones`, `surfaces`,
 *      `liveCounts`, `peopleSeries`, etc.) that the existing visualisation
 *      components already consume — they continue to work unchanged.
 *
 * All data here is fabricated for the prototype. In production these objects
 * are computed in the Perception Engine and persisted to Neo4j + Postgres +
 * Qdrant.
 */

import type { Session } from "@/lib/session/types";

// Legacy demo-specific zone type (carries `polygon`, which user-defined
// zones don't currently have).
export type ZoneType = "entry" | "experience" | "product" | "lounge" | "exit";

export interface Zone {
  id: string;
  name: string;
  type: ZoneType;
  // Polygon in normalized booth coordinates (0..1 on a 10m x 6m floor plan)
  polygon: [number, number][];
  color: string;
}

export interface InteractiveSurface {
  id: string;
  label: string;
  type: "screen" | "game" | "ar" | "rfid" | "scent" | "product";
  zoneId: string;
  position: [number, number]; // booth coords
  // Live state surfaced from the digital experience
  active: boolean;
  triggerCount: number;
}

export interface SessionMeta {
  id: string;
  client: string;
  campaign: string;
  venue: string;
  city: string;
  startedAt: string;
  endsAt: string;
  cameraCount: number;
  agencyName: string;
  boothSize: { width: number; depth: number }; // meters
}

export const session: SessionMeta = {
  id: "ses_2026_05_lagos_lvmh_01",
  client: "Maison Vivienne",
  campaign: "Pavilion No. 7 — Lagos Activation",
  venue: "Lagos Showroom · Eko Atlantic",
  city: "Lagos, NG",
  startedAt: "2026-05-18T09:00:00Z",
  endsAt: "2026-05-20T22:00:00Z",
  cameraCount: 2,
  agencyName: "Floats XR",
  boothSize: { width: 10, depth: 6 },
};

export const zones: Zone[] = [
  {
    id: "zone_entry",
    name: "Entry Arch",
    type: "entry",
    polygon: [
      [0.0, 0.0],
      [0.25, 0.0],
      [0.25, 0.3],
      [0.0, 0.3],
    ],
    color: "#3e83f7",
  },
  {
    id: "zone_experience",
    name: "Mirror Room",
    type: "experience",
    polygon: [
      [0.25, 0.05],
      [0.65, 0.05],
      [0.65, 0.55],
      [0.25, 0.55],
    ],
    color: "#bf5af2",
  },
  {
    id: "zone_product",
    name: "Bottle Wall",
    type: "product",
    polygon: [
      [0.65, 0.0],
      [1.0, 0.0],
      [1.0, 0.4],
      [0.65, 0.4],
    ],
    color: "#00d4ff",
  },
  {
    id: "zone_lounge",
    name: "Lounge",
    type: "lounge",
    polygon: [
      [0.3, 0.6],
      [0.75, 0.6],
      [0.75, 1.0],
      [0.3, 1.0],
    ],
    color: "#30d158",
  },
  {
    id: "zone_exit",
    name: "Exit + RFID Wall",
    type: "exit",
    polygon: [
      [0.75, 0.55],
      [1.0, 0.55],
      [1.0, 1.0],
      [0.75, 1.0],
    ],
    color: "#ffd60a",
  },
];

export const surfaces: InteractiveSurface[] = [
  {
    id: "srf_mirror",
    label: "AR Mirror",
    type: "ar",
    zoneId: "zone_experience",
    position: [0.45, 0.18],
    active: true,
    triggerCount: 482,
  },
  {
    id: "srf_game",
    label: "Scent Quiz",
    type: "game",
    zoneId: "zone_experience",
    position: [0.36, 0.42],
    active: true,
    triggerCount: 317,
  },
  {
    id: "srf_bottle_wall",
    label: "Bottle Wall",
    type: "product",
    zoneId: "zone_product",
    position: [0.85, 0.18],
    active: true,
    triggerCount: 904,
  },
  {
    id: "srf_rfid_wall",
    label: "Memory RFID",
    type: "rfid",
    zoneId: "zone_exit",
    position: [0.88, 0.78],
    active: true,
    triggerCount: 211,
  },
  {
    id: "srf_scent",
    label: "Scent Diffuser",
    type: "scent",
    zoneId: "zone_lounge",
    position: [0.52, 0.78],
    active: false,
    triggerCount: 0,
  },
];

export const liveCounts = {
  peopleNow: 14,
  peopleLastHour: 142,
  peopleToday: 1287,
  peopleVsYesterday: 0.18, // +18%
  avgDwellSeconds: 263,
  peakConcurrent: 31,
  triggers: 2186,
  insights: 23,
};

export const zoneStats = [
  {
    zoneId: "zone_entry",
    name: "Entry Arch",
    nowCount: 3,
    avgDwell: 18,
    todayCount: 1287,
    capture: 1.0,
  },
  {
    zoneId: "zone_experience",
    name: "Mirror Room",
    nowCount: 5,
    avgDwell: 142,
    todayCount: 894,
    capture: 0.69,
  },
  {
    zoneId: "zone_product",
    name: "Bottle Wall",
    nowCount: 2,
    avgDwell: 71,
    todayCount: 712,
    capture: 0.55,
  },
  {
    zoneId: "zone_lounge",
    name: "Lounge",
    nowCount: 3,
    avgDwell: 410,
    todayCount: 488,
    capture: 0.38,
  },
  {
    zoneId: "zone_exit",
    name: "Exit + RFID Wall",
    nowCount: 1,
    avgDwell: 32,
    todayCount: 1102,
    capture: 0.86,
  },
];

/** Time-series for the last 60 minutes, sampled per minute. */
export const peopleSeries = generateSeries(60, 8, 28, 0.4, 1);
export const dwellSeries = generateSeries(60, 90, 360, 0.6, 2);
export const triggerSeries = generateSeries(60, 4, 22, 0.5, 3);
export const attentionSeries = generateSeries(60, 0.35, 0.92, 0.3, 4);

/**
 * Deterministic seeded series — same output on server and client so SSR
 * hydration matches exactly. Each series has its own seed so they look
 * uncorrelated.
 */
function generateSeries(
  n: number,
  min: number,
  max: number,
  smoothness: number,
  seed: number
): number[] {
  const out: number[] = [];
  let v = (min + max) / 2;
  let s = (seed * 1000003) % 2147483647;
  if (s <= 0) s += 2147483646;
  const rand = () => {
    s = (s * 16807) % 2147483647;
    return (s - 1) / 2147483646;
  };
  for (let i = 0; i < n; i++) {
    const drift = (rand() - 0.5) * (max - min) * (1 - smoothness);
    v = Math.max(min, Math.min(max, v + drift));
    out.push(Number(v.toFixed(2)));
  }
  return out;
}

// ── Canonical demo session in the new `Session` shape ────────────────────
//
// The session store picks this up as `isDemo: true` and always keeps it in
// the list. It's the default active session on first load.

export const DEMO_SESSION: Session = {
  id: session.id,
  isDemo: true,
  status: "live",
  name: session.campaign,
  type: "brand_activation",
  brand: session.client,
  client: session.client,
  agency: session.agencyName,
  venue: session.venue,
  address: "Pavilion No. 7, Eko Atlantic, Lagos",
  city: session.city,
  timezone: "Africa/Lagos",
  startAt: session.startedAt,
  endAt: session.endsAt,
  expectedDailyFootfall: 1500,
  cameras: Array.from({ length: session.cameraCount }).map((_, i) => ({
    id: `cam_${i + 1}`,
    name: `Camera ${i + 1}`,
    placement: i === 0 ? "Ceiling — central" : "Side — sponsor wall",
    device: "Logitech C920",
  })),
  zones: zones.map((z) => ({
    id: z.id,
    name: z.name,
    // Demo's narrow ZoneType maps to the wider Session ZoneType:
    type:
      z.type === "experience"
        ? "engagement"
        : z.type === "product"
          ? "retail"
          : z.type, // entry / lounge / exit map directly
    color: z.color,
    polygon: z.polygon,
    capacity: 25,
  })),
  touchpoints: surfaces.map((s) => ({
    id: s.id,
    name: s.label,
    type:
      s.type === "ar"
        ? "ar_mirror"
        : s.type === "game"
          ? "quiz"
          : s.type === "scent"
            ? "scent_station"
            : s.type === "product"
              ? "product_display"
              : s.type, // screen | rfid map directly
    zoneId: s.zoneId,
    triggers: ["viewed", "interacted"],
  })),
  goals: {
    primaryObjective: "brand_awareness",
    targetVisitors: 3500,
    targetDwellSec: 240,
    targetCaptures: 700,
    notes: "Demo seed — Maison Vivienne Pavilion No. 7 activation.",
  },
  privacy: {
    mode: "default",
    consentSignage: true,
    retentionDays: 30,
    recipients: ["maya@yourselfcreative.co", "client@maisonvivienne.com"],
  },
  createdAt: session.startedAt,
  startedAt: session.startedAt,
};
