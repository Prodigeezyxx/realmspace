import type { AgentDefinition } from "../types";

export const dwellAgent: AgentDefinition = {
  id: "dwell",
  name: "Dwell Monitor",
  description: "Fires when a person exceeds dwell threshold in a zone",
  enabled: true,
  trigger: {
    event: "person_dwell_exceeded",
    config: { thresholdSec: 30 },
  },
  skills: [
    { id: "track" },
    { id: "zone-detect" },
  ],
  outputs: [{ type: "stream" }],
};
