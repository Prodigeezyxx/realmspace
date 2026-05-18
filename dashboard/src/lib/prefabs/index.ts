import { exhibitionGrid } from "./exhibition-grid";
import { openPlan } from "./open-plan";
import { standardHall } from "./standard-hall";
import { theatre } from "./theatre";
import type { Prefab } from "./types";

export const prefabs: Prefab[] = [
  standardHall,
  exhibitionGrid,
  openPlan,
  theatre,
];

export function getPrefab(id: string): Prefab | undefined {
  return prefabs.find((p) => p.id === id);
}

export type { Prefab, PrefabZone, PrefabWall } from "./types";
