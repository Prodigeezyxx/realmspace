export type EventType =
  | "enter"
  | "exit"
  | "dwell"
  | "gaze"
  | "trigger"
  | "group"
  | "insight";

export interface SessionEvent {
  id: string;
  type: EventType;
  timestamp: number; // ms epoch
  personId?: string;
  zoneId?: string;
  surfaceId?: string;
  meta?: Record<string, string | number>;
  text: string;
}

const NOW = Date.UTC(2026, 4, 18, 21, 14, 0); // fixed seed so it renders identically server-side

const sample: Omit<SessionEvent, "id" | "timestamp">[] = [
  {
    type: "enter",
    personId: "P-218",
    zoneId: "zone_entry",
    text: "P-218 entered Entry Arch",
  },
  {
    type: "trigger",
    personId: "P-216",
    surfaceId: "srf_mirror",
    zoneId: "zone_experience",
    text: "AR Mirror activated by P-216",
  },
  {
    type: "gaze",
    personId: "P-216",
    surfaceId: "srf_bottle_wall",
    text: "P-216 looked at Bottle Wall · 7s",
    meta: { duration: 7 },
  },
  {
    type: "dwell",
    personId: "P-213",
    zoneId: "zone_lounge",
    text: "P-213 reached 5m dwell in Lounge",
    meta: { duration: 300 },
  },
  {
    type: "group",
    zoneId: "zone_experience",
    text: "Group of 4 formed near Mirror Room",
    meta: { size: 4 },
  },
  {
    type: "trigger",
    personId: "P-217",
    surfaceId: "srf_game",
    zoneId: "zone_experience",
    text: "Scent Quiz started by P-217",
  },
  {
    type: "insight",
    text: "Visitors who try the Scent Quiz dwell 2.4× longer in the Lounge.",
  },
  {
    type: "enter",
    personId: "P-219",
    zoneId: "zone_entry",
    text: "P-219 entered Entry Arch",
  },
  {
    type: "gaze",
    personId: "P-215",
    surfaceId: "srf_bottle_wall",
    text: "P-215 looked at Bottle Wall · 12s",
    meta: { duration: 12 },
  },
  {
    type: "exit",
    personId: "P-210",
    zoneId: "zone_exit",
    text: "P-210 exited via RFID Wall · captured memory",
  },
  {
    type: "trigger",
    personId: "P-214",
    surfaceId: "srf_rfid_wall",
    zoneId: "zone_exit",
    text: "RFID Wall captured P-214 — bottle: Rose Nuit",
  },
  {
    type: "dwell",
    personId: "P-211",
    zoneId: "zone_experience",
    text: "P-211 dwelled 3m 12s in Mirror Room",
    meta: { duration: 192 },
  },
  {
    type: "group",
    zoneId: "zone_lounge",
    text: "Group of 3 lingering in Lounge — 4m+",
    meta: { size: 3 },
  },
  {
    type: "insight",
    text: "Bottle Wall has 86% gaze-capture for visitors who pass within 1m.",
  },
];

// Deterministic jitter so the rendered times match between SSR and CSR
function jitter(i: number) {
  const s = ((i + 1) * 48271) % 2147483647;
  return (s % 3000);
}

export const events: SessionEvent[] = sample.map((e, i) => ({
  ...e,
  id: `evt_${i.toString().padStart(4, "0")}`,
  timestamp: NOW - i * 11_000 - jitter(i),
}));
