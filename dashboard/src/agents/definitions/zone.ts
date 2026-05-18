import type { AgentDefinition } from "../types";

export const zoneAgent: AgentDefinition = {
  id: "zone",
  name: "Zone Crossing",
  description: "Emits when tracked persons enter or exit configured zones",
  enabled: true,
  trigger: { event: "frame_tracks" },
  skills: [
    { id: "track" },
    { id: "zone-detect" },
    { id: "twin-sync" },
  ],
  outputs: [{ type: "stream" }, { type: "log" }],
};
