/**
 * Stack: raw detections | frame tracks → TrackedPerson[]
 * Input trigger.payload: { detections?, tracks? }
 */
import type { Detection, Track } from "@/lib/tracker";
import type { SkillModule, SkillRunInput } from "./types";

export interface TrackedPerson {
  id: number;
  label: string;
  cx: number;
  cy: number;
  bbox: [number, number, number, number];
  dwellMs: number;
}

export interface TrackSkillOutput {
  persons: TrackedPerson[];
}

export const trackSkill: SkillModule<TrackSkillOutput> = {
  id: "track",
  stack: "detections[] | tracks[] → TrackedPerson[]",
  async run(input: SkillRunInput) {
    const tracks = (input.trigger.payload.tracks as Track[] | undefined) ?? [];
    const detections =
      (input.trigger.payload.detections as Detection[] | undefined) ?? [];
    const frameTs = input.trigger.timestamp;

    if (tracks.length > 0) {
      return {
        persons: tracks.map((t) => ({
          id: t.id,
          label: t.label,
          cx: t.cx,
          cy: t.cy,
          bbox: t.bbox,
          dwellMs: frameTs - t.firstSeen,
        })),
      };
    }

    return {
      persons: detections.map((d, i) => {
        const cx = d.bbox[0] + d.bbox[2] / 2;
        const cy = d.bbox[1] + d.bbox[3] / 2;
        return {
          id: i + 1,
          label: `P-${(i + 1).toString().padStart(3, "0")}`,
          cx,
          cy,
          bbox: d.bbox,
          dwellMs: 0,
        };
      }),
    };
  },
};
