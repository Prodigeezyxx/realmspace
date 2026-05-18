import { getEventContext } from "@/lib/event-context";
import {
  AGENT_STREAM_CHANNEL,
  busEmit,
} from "@/lib/event-bus";
import { getSkill } from "@/skills";
import type { SkillRunInput } from "@/skills/types";

import type { AgentDefinition, AgentRunResult, TriggerEvent } from "./types";

async function emitOutput(
  agent: AgentDefinition,
  outputType: string,
  result: AgentRunResult
) {
  if (outputType === "stream") {
    busEmit(AGENT_STREAM_CHANNEL, result);
  }
  if (outputType === "log") {
    console.info(`[agent:${agent.id}]`, result.skillChain);
  }
  if (outputType === "webhook") {
    console.info(`[agent:${agent.id}:webhook stub]`, result);
  }
}

export async function runAgent(
  agent: AgentDefinition,
  trigger: TriggerEvent
): Promise<AgentRunResult> {
  const session = getEventContext();
  const chain: Record<string, unknown> = {};

  for (const ref of agent.skills) {
    const skill = getSkill(ref.id);
    if (!skill) {
      console.warn(`[runtime] unknown skill: ${ref.id}`);
      continue;
    }
    const input: SkillRunInput = { trigger, session, chain };
    const config = { ...ref.config };
    chain[ref.id] = await skill.run(input, config);
  }

  const result: AgentRunResult = {
    agentId: agent.id,
    trigger,
    skillChain: chain,
    outputs: chain,
    finishedAt: Date.now(),
  };

  for (const out of agent.outputs) {
    await emitOutput(agent, out.type, result);
  }

  return result;
}
