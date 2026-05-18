/**
 * Stack: TrackedPerson[] + zone polygons → crossings[], zone occupancy
 * Reads chain.track.persons; config: zones from session or payload
 */
import type { SkillModule, SkillRunInput } from "./types";
import type { TrackSkillOutput } from "./track";

export interface ZoneCrossing {
  personId: number;
  personLabel: string;
  zoneId: string;
  zoneName: string;
  kind: "enter" | "exit";
}

export interface ZoneDetectOutput {
  crossings: ZoneCrossing[];
  occupancy: Record<string, number>;
}

function pointInPolygon(
  x: number,
  y: number,
  poly: [number, number][]
): boolean {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const xi = poly[i][0];
    const yi = poly[i][1];
    const xj = poly[j][0];
    const yj = poly[j][1];
    const intersect =
      (yi > y) !== (yj > y) &&
      x < ((xj - xi) * (y - yi)) / (yj - yi) + xi;
    if (intersect) inside = !inside;
  }
  return inside;
}

/** Map pixel coords to normalized 0..1 using frame size from payload */
function norm(cx: number, cy: number, w: number, h: number): [number, number] {
  return [cx / w, cy / h];
}

export const zoneDetectSkill: SkillModule<ZoneDetectOutput> = {
  id: "zone-detect",
  stack: "chain.track.persons + zones[] → crossings[], occupancy{}",
  async run(input: SkillRunInput, config) {
    const trackOut = input.chain.track as TrackSkillOutput | undefined;
    const persons = trackOut?.persons ?? [];
    const frameW = (input.trigger.payload.frameWidth as number) ?? 1280;
    const frameH = (input.trigger.payload.frameHeight as number) ?? 720;
    const prevZones =
      (input.trigger.payload.previousZoneByPerson as Record<number, string>) ??
      {};
    const zones =
      (config.zones as { id: string; name: string; polygon?: [number, number][] }[]) ??
      input.session.zones.filter((z) => z.polygon?.length);

    const crossings: ZoneCrossing[] = [];
    const occupancy: Record<string, number> = {};

    for (const p of persons) {
      const [nx, ny] = norm(p.cx, p.cy, frameW, frameH);
      let current: string | null = null;
      for (const z of zones) {
        if (!z.polygon?.length) continue;
        if (pointInPolygon(nx, ny, z.polygon)) {
          current = z.id;
          occupancy[z.id] = (occupancy[z.id] ?? 0) + 1;
          break;
        }
      }
      const prev = prevZones[p.id];
      if (current && current !== prev) {
        const z = zones.find((x) => x.id === current);
        crossings.push({
          personId: p.id,
          personLabel: p.label,
          zoneId: current,
          zoneName: z?.name ?? current,
          kind: "enter",
        });
      }
      if (prev && prev !== current) {
        const z = zones.find((x) => x.id === prev);
        crossings.push({
          personId: p.id,
          personLabel: p.label,
          zoneId: prev,
          zoneName: z?.name ?? prev,
          kind: "exit",
        });
      }
    }

    return { crossings, occupancy };
  },
};
