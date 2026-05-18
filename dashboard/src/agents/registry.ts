import { agentDefinitions } from "./definitions";
import type { AgentDefinition } from "./types";

const LS_KEY = "realmspace.agents.enabled.v1";

let agents: AgentDefinition[] = agentDefinitions.map((a) => ({ ...a }));

function loadEnabledOverrides(): Record<string, boolean> {
  if (typeof window === "undefined") return {};
  try {
    return JSON.parse(localStorage.getItem(LS_KEY) ?? "{}") as Record<
      string,
      boolean
    >;
  } catch {
    return {};
  }
}

function applyOverrides() {
  const overrides = loadEnabledOverrides();
  agents = agentDefinitions.map((a) => ({
    ...a,
    enabled: overrides[a.id] ?? a.enabled,
  }));
}

if (typeof window !== "undefined") {
  applyOverrides();
}

export function listAgents(): AgentDefinition[] {
  if (typeof window !== "undefined") applyOverrides();
  return [...agents];
}

export function getAgent(id: string): AgentDefinition | undefined {
  return listAgents().find((a) => a.id === id);
}

export function setAgentEnabled(id: string, enabled: boolean) {
  const overrides = loadEnabledOverrides();
  overrides[id] = enabled;
  if (typeof window !== "undefined") {
    localStorage.setItem(LS_KEY, JSON.stringify(overrides));
    applyOverrides();
  }
}

export function agentsForTrigger(eventType: string): AgentDefinition[] {
  return listAgents().filter((a) => a.enabled && a.trigger.event === eventType);
}
