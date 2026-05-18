import type { AgentDefinition } from "../types";

export const layoutAgent: AgentDefinition = {
  id: "layout",
  name: "Twin Layout Loader",
  description: "Hydrates twin from prefab or floor-plan layout JSON",
  enabled: true,
  trigger: { event: "twin_layout_loaded" },
  skills: [{ id: "twin-sync" }],
  outputs: [{ type: "stream" }],
};
