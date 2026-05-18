import type { Prefab } from "./types";
import type { Touchpoint, Zone, ZoneType } from "@/lib/session/types";

import { getPrefab } from "./index";

function zoneTypeFromKey(key: string): ZoneType {
  const k = key.toLowerCase();
  if (k.includes("entry") || k.includes("ingress")) return "entry";
  if (k.includes("exit") || k.includes("egress")) return "exit";
  if (k.includes("lounge") || k.includes("seating")) return "lounge";
  if (k.includes("retail") || k.includes("shop")) return "retail";
  if (k.includes("demo")) return "demo";
  if (k.includes("press")) return "press";
  if (k.includes("sponsor")) return "sponsor";
  if (k.includes("reveal")) return "reveal";
  if (k.includes("privacy")) return "privacy_masked";
  return "engagement";
}

export function prefabToZones(prefab: Prefab, newId: (prefix: string) => string): Zone[] {
  return prefab.zones.map((z) => ({
    id: newId(`zone_${z.id}`),
    name: z.label,
    type: zoneTypeFromKey(z.id + z.label),
    color: z.color,
    polygon: z.polygon,
  }));
}

export function remapTouchpointZones(
  touchpoints: Touchpoint[],
  prevZones: Zone[],
  nextZones: Zone[]
): Touchpoint[] {
  const prevById = new Map(prevZones.map((z) => [z.id, z]));
  const nextByName = new Map(nextZones.map((z) => [z.name.toLowerCase(), z.id]));

  return touchpoints.map((tp) => {
    if (!tp.zoneId) return tp;
    const prev = prevById.get(tp.zoneId);
    if (!prev) return { ...tp, zoneId: undefined };
    const nextId = nextByName.get(prev.name.toLowerCase());
    return nextId ? { ...tp, zoneId: nextId } : { ...tp, zoneId: undefined };
  });
}

export interface ApplyPrefabResult {
  prefabId: string;
  boothSize: { width: number; depth: number };
  zones: Zone[];
  touchpoints: Touchpoint[];
}

export function applyPrefabToDraft(
  prefabId: string,
  opts: {
    newId: (prefix: string) => string;
    prevZones: Zone[];
    touchpoints: Touchpoint[];
  }
): ApplyPrefabResult | null {
  const prefab = getPrefab(prefabId);
  if (!prefab) return null;

  const zones = prefabToZones(prefab, opts.newId);
  const touchpoints = remapTouchpointZones(
    opts.touchpoints,
    opts.prevZones,
    zones
  );

  return {
    prefabId,
    boothSize: prefab.boothSize,
    zones,
    touchpoints,
  };
}
