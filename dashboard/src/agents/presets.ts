/**
 * realmspace — the eight agent definitions, compiled to rule documents.
 *
 * ADR-002, "The browser stops owning rules": `dashboard/src/agents/` held eight
 * `AgentDefinition` objects with their own trigger vocabulary —
 * `{ event: "person_dwell_exceeded", config: { thresholdSec: 30 } }`. "That is a
 * second rule language, and it is the split-brain the roadmap names. The
 * definitions become **presets** that compile to the spec above."
 *
 * This is that compiler. The definitions keep their shape — they are pleasant to
 * author and they are what `registry.ts` and the skills pipeline already read —
 * but nothing downstream of here speaks their trigger language.
 *
 * ## The three definitions that do not compile, and why that is the point
 *
 * `heatmap`, `report` and `layout` are not rules. They are jobs the browser runs
 * over its own state: build a heatmap from frames, render a markdown report at
 * close-out, load a twin layout. None of them acts on the room, and none has a
 * condition an operator would recognise as a rule.
 *
 * That they do not fit is the finding, not a gap. Calling all eight "agents" was
 * what made the split-brain hard to see — a list mixing "ping ops when the
 * entrance is crowded" with "regenerate the heatmap" reads as one kind of thing
 * and is two. `toRule` returns null for those, and the composer shows them as
 * what they are.
 *
 * `nlq` is a fourth: Ask the Room is a question a human types, not a condition
 * the room meets.
 */

import type { RuleDocument } from "@/lib/contracts/rules";

import type { AgentDefinition } from "./types";

/**
 * The browser's trigger vocabulary, translated to the event taxonomy.
 *
 * Every entry here is a line that used to be implicit — `person_dwell_exceeded`
 * "meant" a dwell, and only the runtime knew. Writing the mapping down is most
 * of what ending the split-brain amounts to.
 *
 * `frame_tracks` has no entry on purpose. It is a per-frame local signal, not an
 * event on the bus; a rule triggered by it would be asking the edge to evaluate
 * something the edge never sees.
 */
const TRIGGER_EVENTS: Record<string, string> = {
  person_dwell_exceeded: "spatial.dwell",
  zone_threshold_exceeded: "spatial.zone_enter",
};

/**
 * Compile one definition to a rule document, or null if it is not a rule.
 *
 * The `id` is prefixed `preset_` so a saved preset is distinguishable from a
 * rule an operator wrote, and so saving the same preset twice replaces it rather
 * than accumulating copies.
 */
export function toRule(agent: AgentDefinition): RuleDocument | null {
  const triggerType = TRIGGER_EVENTS[agent.trigger.event];
  if (!triggerType) return null;

  const config = (agent.trigger.config ?? {}) as Record<string, unknown>;
  const zoneId = typeof config.zoneId === "string" ? config.zoneId : null;

  return {
    ruleId: `preset_${agent.id}`,
    name: agent.name,
    triggerType,
    triggerZoneId: zoneId,
    condition: conditionFor(agent, config, zoneId),
    action: actionFor(agent),
    enabled: agent.enabled,
    // The one preset that carried a cooldown had it in milliseconds, under a
    // different key. Rounded up rather than truncated: a cooldown shorter than
    // the operator asked for is the failure that floods a Slack channel.
    cooldownSec:
      typeof config.cooldownMs === "number"
        ? Math.ceil(config.cooldownMs / 1000)
        : 60,
  };
}

function conditionFor(
  agent: AgentDefinition,
  config: Record<string, unknown>,
  zoneId: string | null
): RuleDocument["condition"] {
  if (typeof config.threshold === "number") {
    return {
      type: "threshold",
      count: config.threshold,
      // The definition never said over what period its threshold applied — the
      // browser runtime evaluated it against whatever it happened to be holding.
      // An edge evaluator cannot work from "whatever is in memory", so the
      // window has to be stated. 60s matches the cooldown the same preset
      // carried, which is the closest thing to an intent it recorded.
      windowSec: 60,
      zoneId,
    };
  }
  if (typeof config.thresholdSec === "number") {
    return {
      type: "threshold",
      count: 1,
      windowSec: config.thresholdSec,
      zoneId,
      // `person_dwell_exceeded` meant *this* — a dwell longer than the
      // threshold. On the bus every stay emits a `spatial.dwell`, so the
      // filtering that used to be the trigger's name is now a condition.
      minDwellSec: config.thresholdSec,
    };
  }
  return { type: "any", zoneId };
}

/**
 * What the preset does when it fires.
 *
 * `AgentOutput` is a delivery mechanism (`stream` | `webhook` | `log`), not an
 * action, and the difference matters: `stream` means "put it on the browser's
 * in-page bus", which is not something an edge evaluator can do or should try
 * to. So a preset whose only output is `stream` compiles to `log` — it records
 * that it matched and does nothing to the room, which is exactly what it did
 * before, stated honestly.
 *
 * An operator arms it for real by editing the action in the composer, which is
 * the moment they decide a rule should act. Silently upgrading a `stream` output
 * to a Slack post would be this file deciding that on their behalf.
 */
function actionFor(agent: AgentDefinition): RuleDocument["action"] {
  const hasWebhook = agent.outputs.some((o) => o.type === "webhook");
  if (hasWebhook) {
    const url =
      (agent.outputs.find((o) => o.type === "webhook")?.config?.url as string) ??
      "";
    // A webhook output with no URL configured — which is all of them today, the
    // runtime having only ever console.logged a stub. `log` rather than a
    // webhook to nowhere, so it cannot be saved as a rule that appears armed.
    if (url) return { type: "webhook", url };
  }
  return { type: "log", message: agent.description };
}

/** Why a definition is not a rule, for the composer to show. */
export function notARuleReason(agent: AgentDefinition): string | null {
  if (TRIGGER_EVENTS[agent.trigger.event]) return null;
  if (agent.trigger.event === "frame_tracks") {
    return "runs per frame in this browser — there is no bus event to trigger on";
  }
  if (agent.trigger.event === "nlq_query") {
    return "answers a question somebody types; it is not a condition the room meets";
  }
  if (agent.trigger.event === "twin_layout_loaded") {
    return "loads a layout into the twin; it does not act on the room";
  }
  if (agent.trigger.event === "session_ended") {
    // `session.ended` *is* a bus event and an operator can absolutely write a
    // rule on it — the composer will take one, since `triggerType` is open. This
    // particular preset is not that: it renders a markdown document here, and a
    // rule that fired it would have nowhere to send the result.
    return "renders a report in this browser; a rule has no way to deliver one";
  }
  return `no bus event corresponds to “${agent.trigger.event}”`;
}
