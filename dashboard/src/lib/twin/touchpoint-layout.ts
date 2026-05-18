import { surfaces as demoSurfaces } from "@/lib/mock/session";
import type { Touchpoint, Zone } from "@/lib/session/types";

export interface TwinSurface {
  id: string;
  label: string;
  type: "screen" | "game" | "ar" | "rfid" | "scent" | "product";
  zoneId: string;
  position: [number, number];
  active: boolean;
  triggerCount: number;
}

function zoneCentroid(polygon?: [number, number][]): [number, number] {
  if (!polygon?.length) return [0.5, 0.5];
  const x = polygon.reduce((s, [px]) => s + px, 0) / polygon.length;
  const y = polygon.reduce((s, [, py]) => s + py, 0) / polygon.length;
  return [x, y];
}

function mapType(
  t: Touchpoint["type"]
): TwinSurface["type"] {
  switch (t) {
    case "ar_mirror":
      return "ar";
    case "quiz":
    case "game":
      return "game";
    case "scent_station":
      return "scent";
    case "product_display":
    case "configurator":
      return "product";
    case "lead_form":
    case "badge_scan":
      return "rfid";
    default:
      return "screen";
  }
}

/** Demo uses curated layout; custom sessions use only configured touchpoints */
export function resolveTwinSurfaces(
  isDemo: boolean,
  touchpoints: Touchpoint[],
  zones: Zone[]
): TwinSurface[] {
  if (isDemo) return demoSurfaces;

  return touchpoints.map((tp, i) => {
    const zone = zones.find((z) => z.id === tp.zoneId);
    const position =
      zone?.polygon?.length
        ? zoneCentroid(zone.polygon)
        : ([0.25 + (i % 4) * 0.15, 0.25 + Math.floor(i / 4) * 0.15] as [
            number,
            number,
          ]);

    return {
      id: tp.id,
      label: tp.name,
      type: mapType(tp.type),
      zoneId: tp.zoneId ?? zones[0]?.id ?? "floor",
      position,
      active: false,
      triggerCount: 0,
    };
  });
}
