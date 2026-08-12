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
    // Named a dry run rather than a stub, because the difference is the whole of
    // ADR-002's last section. A "webhook stub" reads as an action that is nearly
    // finished; what it actually is, and must stay, is the browser declining to
    // act — "nothing in the browser decides whether a rule fires in production".
    //
    // The webhook a rule performs is dispatched on the edge
    // (`backend/app/actions/webhook.py`), signed, idempotent, and parked in the
    // HITL queue when it fails. None of that is available here and none of it
    // should be reimplemented here.
    console.info(
      `[agent:${agent.id}] dry run — an armed rule would POST this from the edge`,
      result
    );
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
