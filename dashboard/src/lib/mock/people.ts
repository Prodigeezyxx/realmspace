/**
 * Anonymous person tracks for the digital twin.
 * Each person has a path through the booth as a list of waypoints,
 * with the time at which they reached each waypoint.
 */

export interface PersonTrack {
  id: string;
  color: string;
  // [x, y, t] where x,y are normalized booth coords (0..1) and t is seconds since session start
  waypoints: [number, number, number][];
  zonesVisited: string[];
  surfacesTriggered: string[];
  totalDwellSeconds: number;
  attentionScore: number; // 0..1
}

export const palette = [
  "#3e83f7",
  "#00d4ff",
  "#bf5af2",
  "#30d158",
  "#ffd60a",
  "#ff7eb6",
  "#5ac8fa",
  "#ff9f0a",
  "#a78bfa",
  "#f97316",
];

export const peopleTracks: PersonTrack[] = [
  {
    id: "P-211",
    color: palette[0],
    waypoints: [
      [0.1, 0.15, 0],
      [0.22, 0.2, 8],
      [0.4, 0.3, 22],
      [0.5, 0.4, 45],
      [0.5, 0.4, 180],
      [0.7, 0.2, 200],
      [0.82, 0.18, 215],
      [0.82, 0.18, 270],
      [0.6, 0.7, 290],
      [0.55, 0.85, 320],
      [0.55, 0.85, 540],
      [0.9, 0.85, 560],
    ],
    zonesVisited: ["zone_entry", "zone_experience", "zone_product", "zone_lounge", "zone_exit"],
    surfacesTriggered: ["srf_mirror", "srf_game", "srf_rfid_wall"],
    totalDwellSeconds: 560,
    attentionScore: 0.88,
  },
  {
    id: "P-213",
    color: palette[1],
    waypoints: [
      [0.08, 0.1, 0],
      [0.2, 0.2, 12],
      [0.35, 0.4, 30],
      [0.45, 0.4, 75],
      [0.45, 0.4, 220],
      [0.55, 0.75, 240],
      [0.55, 0.75, 600],
    ],
    zonesVisited: ["zone_entry", "zone_experience", "zone_lounge"],
    surfacesTriggered: ["srf_mirror"],
    totalDwellSeconds: 600,
    attentionScore: 0.72,
  },
  {
    id: "P-214",
    color: palette[2],
    waypoints: [
      [0.05, 0.18, 0],
      [0.2, 0.18, 8],
      [0.7, 0.15, 35],
      [0.85, 0.18, 50],
      [0.85, 0.18, 95],
      [0.88, 0.78, 130],
      [0.88, 0.78, 165],
    ],
    zonesVisited: ["zone_entry", "zone_product", "zone_exit"],
    surfacesTriggered: ["srf_bottle_wall", "srf_rfid_wall"],
    totalDwellSeconds: 165,
    attentionScore: 0.81,
  },
  {
    id: "P-215",
    color: palette[3],
    waypoints: [
      [0.05, 0.22, 0],
      [0.2, 0.18, 10],
      [0.5, 0.22, 28],
      [0.78, 0.2, 50],
      [0.78, 0.2, 140],
      [0.95, 0.85, 180],
    ],
    zonesVisited: ["zone_entry", "zone_product", "zone_exit"],
    surfacesTriggered: ["srf_bottle_wall"],
    totalDwellSeconds: 180,
    attentionScore: 0.65,
  },
  {
    id: "P-216",
    color: palette[4],
    waypoints: [
      [0.05, 0.12, 0],
      [0.22, 0.18, 9],
      [0.4, 0.3, 26],
      [0.5, 0.4, 60],
      [0.5, 0.4, 250],
      [0.82, 0.2, 285],
      [0.82, 0.2, 330],
      [0.6, 0.75, 360],
      [0.92, 0.85, 410],
    ],
    zonesVisited: ["zone_entry", "zone_experience", "zone_product", "zone_lounge", "zone_exit"],
    surfacesTriggered: ["srf_mirror", "srf_bottle_wall", "srf_rfid_wall"],
    totalDwellSeconds: 410,
    attentionScore: 0.93,
  },
  {
    id: "P-217",
    color: palette[5],
    waypoints: [
      [0.05, 0.2, 0],
      [0.22, 0.2, 14],
      [0.36, 0.42, 38],
      [0.36, 0.42, 160],
      [0.55, 0.78, 195],
      [0.55, 0.78, 380],
    ],
    zonesVisited: ["zone_entry", "zone_experience", "zone_lounge"],
    surfacesTriggered: ["srf_game"],
    totalDwellSeconds: 380,
    attentionScore: 0.77,
  },
  {
    id: "P-218",
    color: palette[6],
    waypoints: [
      [0.05, 0.16, 0],
      [0.22, 0.2, 11],
      [0.4, 0.3, 28],
    ],
    zonesVisited: ["zone_entry", "zone_experience"],
    surfacesTriggered: [],
    totalDwellSeconds: 28,
    attentionScore: 0.41,
  },
  {
    id: "P-219",
    color: palette[7],
    waypoints: [
      [0.04, 0.18, 0],
      [0.2, 0.2, 12],
    ],
    zonesVisited: ["zone_entry"],
    surfacesTriggered: [],
    totalDwellSeconds: 12,
    attentionScore: 0.22,
  },
];

/** Compute live positions at a given time `t` (seconds since session start) by interpolating waypoints. */
export function positionsAt(t: number) {
  return peopleTracks
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
