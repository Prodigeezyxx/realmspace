import type { AgentDefinition } from "../types";

export const heatmapAgent: AgentDefinition = {
  id: "heatmap",
  name: "Heatmap Builder",
  description: "Builds occupancy heatmap grid from live tracks",
  enabled: true,
  trigger: { event: "frame_tracks" },
  skills: [
    { id: "track" },
    { id: "heatmap", config: { width: 24, height: 16 } },
  ],
  outputs: [{ type: "stream" }],
};
