import type { AgentDefinition } from "../types";

export const alertAgent: AgentDefinition = {
  id: "alert",
  name: "Threshold Alert",
  description: "Notifies when zone metric exceeds threshold",
  enabled: true,
  trigger: {
    event: "zone_threshold_exceeded",
    config: {
      zoneId: "zone_entry",
      metric: "count",
      threshold: 5,
      cooldownMs: 60_000,
    },
  },
  skills: [{ id: "notify", config: { channel: "in_app" } }],
  outputs: [{ type: "webhook" }, { type: "stream" }],
};
