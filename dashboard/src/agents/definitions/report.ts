import type { AgentDefinition } from "../types";

export const reportAgent: AgentDefinition = {
  id: "report",
  name: "Session Report",
  description: "Generates post-event markdown report when session ends",
  enabled: true,
  trigger: { event: "session_ended" },
  skills: [{ id: "report-gen" }],
  outputs: [{ type: "stream" }, { type: "log" }],
};
