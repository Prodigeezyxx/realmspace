import type { EventContext } from "@/lib/event-context";
import type { TriggerEvent } from "@/agents/types";

/** Passed into every skill — prior outputs keyed by skill id */
export interface SkillRunInput {
  trigger: TriggerEvent;
  session: EventContext;
  chain: Record<string, unknown>;
}

export interface SkillModule<TOut = unknown> {
  id: string;
  /** One-line stack hint for composers / docs */
  stack: string;
  run: (
    input: SkillRunInput,
    config: Record<string, unknown>
  ) => Promise<TOut>;
}
