/** Agent runtime types — YAML-shaped configs live as TS modules in ./definitions */

export type AgentOutputType = "stream" | "webhook" | "log";

export interface AgentOutput {
  type: AgentOutputType;
  config?: Record<string, unknown>;
}

export interface AgentSkillRef {
  id: string;
  config?: Record<string, unknown>;
}

export interface AgentTrigger {
  /** Event bus type string, e.g. person_dwell_exceeded */
  event: string;
  config?: Record<string, unknown>;
}

export interface AgentDefinition {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  trigger: AgentTrigger;
  skills: AgentSkillRef[];
  outputs: AgentOutput[];
}

export interface TriggerEvent {
  type: string;
  timestamp: number;
  payload: Record<string, unknown>;
}

export interface AgentRunResult {
  agentId: string;
  trigger: TriggerEvent;
  outputs: Record<string, unknown>;
  skillChain: Record<string, unknown>;
  finishedAt: number;
}
