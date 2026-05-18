import { heatmapSkill } from "./heatmap";
import { nlqSkill } from "./nlq";
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
  [nlqSkill.id]: nlqSkill,
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
  nlqSkill,
  notifySkill,
  reportGenSkill,
  twinSyncSkill,
};
