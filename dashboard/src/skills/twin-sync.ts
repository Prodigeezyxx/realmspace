/**
 * Stack: TrackedPerson[] | zone layout → avatar deltas for Three.js twin
 */
import type { SkillModule, SkillRunInput } from "./types";
import type { TrackSkillOutput } from "./track";

export interface TwinAvatarDelta {
  personId: number;
  label: string;
  x: number;
  z: number;
}

export interface TwinSyncOutput {
  avatars: TwinAvatarDelta[];
  zones?: { id: string; label: string; polygon: [number, number][] }[];
}

export const twinSyncSkill: SkillModule<TwinSyncOutput> = {
  id: "twin-sync",
  stack: "chain.track.persons | layout JSON → avatar positions",
  async run(input: SkillRunInput) {
    const layout = input.trigger.payload.layout as
      | { zones?: { id: string; label: string; polygon: [number, number][] }[] }
      | undefined;
    if (layout?.zones) {
      return { avatars: [], zones: layout.zones };
    }

    const trackOut = input.chain.track as TrackSkillOutput | undefined;
    const persons = trackOut?.persons ?? [];
    const { width, depth } = input.session.boothSize;
    const frameW = (input.trigger.payload.frameWidth as number) ?? 1280;
    const frameH = (input.trigger.payload.frameHeight as number) ?? 720;

    const avatars: TwinAvatarDelta[] = persons.map((p) => ({
      personId: p.id,
      label: p.label,
      x: (p.cx / frameW - 0.5) * width,
      z: (p.cy / frameH - 0.5) * depth,
    }));

    return { avatars };
  },
};
