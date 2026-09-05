import { heatmapSkill } from "./heatmap";
import { notifySkill } from "./notify";
import { reportGenSkill } from "./report-gen";
import { trackSkill } from "./track";
import { twinSyncSkill } from "./twin-sync";
import { zoneDetectSkill } from "./zone-detect";
import type { SkillModule } from "./types";

export const skillRegistry: Record<string, SkillModule> = {
  [trackSkill.id]: trackSkill,
  [zoneDetectSkill.id]: zoneDetectSkill,
  [heatmapSkill.id]: heatmapSkill,
  [notifySkill.id]: notifySkill,
  [reportGenSkill.id]: reportGenSkill,
  [twinSyncSkill.id]: twinSyncSkill,
};

export function getSkill(id: string): SkillModule | undefined {
  return skillRegistry[id];
}

export {
  trackSkill,
  zoneDetectSkill,
  heatmapSkill,
  notifySkill,
  reportGenSkill,
  twinSyncSkill,
};
