/**
 * Stack: TrackedPerson[] → grid[width][height] intensity 0..1
 */
import type { SkillModule, SkillRunInput } from "./types";
import type { TrackSkillOutput } from "./track";

export interface HeatmapOutput {
  width: number;
  height: number;
  grid: number[][];
  peak: { x: number; y: number; value: number };
}

export const heatmapSkill: SkillModule<HeatmapOutput> = {
  id: "heatmap",
  stack: "chain.track.persons → grid[][], peak",
  async run(input: SkillRunInput, config) {
    const w = (config.width as number) ?? 24;
    const h = (config.height as number) ?? 16;
    const trackOut = input.chain.track as TrackSkillOutput | undefined;
    const persons = trackOut?.persons ?? [];
    const frameW = (input.trigger.payload.frameWidth as number) ?? 1280;
    const frameH = (input.trigger.payload.frameHeight as number) ?? 720;

    const grid = Array.from({ length: h }, () => Array(w).fill(0));
    let peak = { x: 0, y: 0, value: 0 };

    for (const p of persons) {
      const gx = Math.min(w - 1, Math.max(0, Math.floor((p.cx / frameW) * w)));
      const gy = Math.min(h - 1, Math.max(0, Math.floor((p.cy / frameH) * h)));
      grid[gy][gx] = Math.min(1, grid[gy][gx] + 0.35);
      if (grid[gy][gx] > peak.value) {
        peak = { x: gx, y: gy, value: grid[gy][gx] };
      }
    }

    return { width: w, height: h, grid, peak };
  },
};
