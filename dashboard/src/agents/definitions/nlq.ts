import type { AgentDefinition } from "../types";

export const nlqAgent: AgentDefinition = {
  id: "nlq",
  name: "Ask the Room",
  description: "Natural language query over the spatial graph",
  enabled: true,
  trigger: { event: "nlq_query" },
  skills: [{ id: "nlq" }],
  outputs: [{ type: "stream" }],
};
