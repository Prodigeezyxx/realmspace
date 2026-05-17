/**
 * Single source of truth for the demo session.
 * Used across Live, Twin, Ask, Agents and Report pages.
 *
 * NOTE: All data here is fabricated for the prototype.
 * In production these objects are computed in the Perception Engine
 * and persisted to Neo4j + Postgres + Qdrant.
 */

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
  agencyName: "Yourself Creative",
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
export const peopleSeries = generateSeries(60, 8, 28, 0.4);
export const dwellSeries = generateSeries(60, 90, 360, 0.6);
export const triggerSeries = generateSeries(60, 4, 22, 0.5);
export const attentionSeries = generateSeries(60, 0.35, 0.92, 0.3);

function generateSeries(
  n: number,
  min: number,
  max: number,
  smoothness: number
): number[] {
  const out: number[] = [];
  let v = (min + max) / 2;
  for (let i = 0; i < n; i++) {
    const drift = (Math.random() - 0.5) * (max - min) * (1 - smoothness);
    v = Math.max(min, Math.min(max, v + drift));
    out.push(Number(v.toFixed(2)));
  }
  return out;
}
