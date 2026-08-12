/**
 * realmspace — the rule document, per `docs/adr/002-rule-spec.md`.
 *
 * ADR-002: "A rule is a JSON document, not code. One shape, stored per tenant,
 * evaluated by exactly one evaluator on the edge. The browser does not implement
 * rules; it *previews* them by running the same document against local events."
 *
 * This file is the browser's half of that. It is a type and a compiler, and
 * deliberately not an evaluator — `agents/runtime.ts` runs a preview, and what
 * it decides has no authority over what the room does.
 *
 * ## Why this exists at all
 *
 * There were three rule vocabularies in this app before it, which is two more
 * than a system can keep in agreement:
 *
 *  - `agents/types.ts` — `{ event: "person_dwell_exceeded", config: {...} }`,
 *    eight definitions, run by the browser's own runtime;
 *  - the composer screen's own `Trigger = "dwell" | "count" | "gaze" | …`, with
 *    hand-written condition strings;
 *  - and, on the edge, the thing that actually decides whether a booth acts.
 *
 * The roadmap calls that the split-brain. The first two become *presets* that
 * compile to the document below; the third is the only one that fires.
 *
 * Mirrors `backend/app/schemas.py` (`RuleIn`), camelCase on the wire like every
 * other contract here.
 */

/**
 * Any event type registered in `event-bus-spec.md` §3 — checked as a namespace
 * prefix, never as a closed union.
 *
 * ADR-002 is explicit about why: "a rule spec that cannot name `intent.scored`
 * or `spatial.tagged` the day those producers land would force a spec migration
 * to use them, which is the tax Phase 3's pre-registration was meant to avoid."
 * Typing this as a union of today's event types would reintroduce that tax in
 * TypeScript instead of in Python, which is not an improvement.
 */
export type RuleTriggerType = string;

/** N of the trigger within a window. The only condition that counts. */
export interface ThresholdCondition {
  type: "threshold";
  count: number;
  windowSec: number;
  /** Narrows what is counted, as distinct from what wakes the rule. */
  zoneId?: string | null;
  /**
   * Ignore a dwell shorter than this. `spatial.dwell` is emitted for every stay
   * including a two-second one, so "5 people at the entrance for 30 seconds"
   * without this counts five people who walked past it.
   */
  minDwellSec?: number | null;
}

/** The trigger itself, uncounted. */
export interface AnyCondition {
  type: "any";
  zoneId?: string | null;
}

/**
 * Nothing happened for `windowSec`.
 *
 * Judged at a boundary on the edge — the next event past the window, or
 * `session.ended` — because asking "did nothing happen?" at the moment
 * something did is close to a contradiction (ADR-002 §4).
 */
export interface NoneCondition {
  type: "none";
  windowSec: number;
  zoneId?: string | null;
}

export type RuleCondition = ThresholdCondition | AnyCondition | NoneCondition;

export interface SlackAction {
  type: "slack";
  channel: string;
  message: string;
}

export interface WebhookAction {
  type: "webhook";
  url: string;
  payload?: Record<string, unknown>;
}

export interface ScreenSwapAction {
  type: "screen_swap";
  screenId: string;
  contentId: string;
}

export interface StaffPromptAction {
  type: "staff_prompt";
  message: string;
  zoneId?: string | null;
  priority?: "low" | "normal" | "high";
}

export interface LogAction {
  type: "log";
  message?: string;
}

/**
 * A closed union, unlike the trigger. An action type nothing dispatches is a
 * rule that looks armed on this screen and does nothing in the room, which is
 * worse than being refused at the moment of saving.
 */
export type RuleAction =
  | SlackAction
  | WebhookAction
  | ScreenSwapAction
  | StaffPromptAction
  | LogAction;

export interface RuleDocument {
  ruleId: string;
  name: string;
  triggerType: RuleTriggerType;
  triggerZoneId?: string | null;
  condition: RuleCondition;
  action: RuleAction;
  enabled: boolean;
  cooldownSec: number;
}

/** What the backend returns: the document plus what only the server knows. */
export interface StoredRule extends RuleDocument {
  tenantId: string;
  createdAt: string;
  updatedAt: string;
}

/**
 * `rule.fired`, as it arrives over the WebSocket.
 *
 * The action rides along because the evaluator copies it into the firing, so
 * that what was carried out is the document **as it was when the rule matched**
 * — ADR-002's second reason for rules-as-data, applied to the log rather than to
 * the current row.
 */
export interface RuleFiredPayload {
  ruleId: string;
  ruleName: string;
  triggerType: string;
  triggerSeq: number;
  condition: RuleCondition;
  action: RuleAction;
  matched: {
    observed: number;
    /**
     * Whether `observed` counts distinct people or raw events. It changes what
     * the number means, so it is stated rather than assumed: one visitor
     * leaving and re-entering five times is five events and one person.
     */
    countedBy: "people" | "events";
    windowSec?: number;
    quietSince?: string;
    noticedBy?: string;
  };
}

/** `rule.staff_prompt` — what the floor is being asked to do. */
export interface StaffPromptPayload {
  message: string;
  zoneId: string | null;
  priority: "low" | "normal" | "high";
  ruleId: string;
  ruleName: string;
}

/** `rule.screen_swap` — what a screen in the room should now show. */
export interface ScreenSwapPayload {
  screenId: string;
  contentId: string;
  ruleId: string;
  ruleName: string;
}

/** Every event namespace the bus accepts (`backend/app/schemas.py`). */
const EVENT_NAMESPACES = [
  "perception.",
  "spatial.",
  "surface.",
  "rfid.",
  "consent.",
  "identity.",
  "rule.",
  "handoff.",
  "insight.",
  "cost.",
  "session.",
  "intent.",
  "drift.",
  "calibration.",
  "crm.",
];

const ACTION_TYPES = ["slack", "webhook", "screen_swap", "staff_prompt", "log"];

/**
 * Why a document would be refused, or an empty list.
 *
 * The same checks the backend applies, run here so the composer can say what is
 * wrong before an operator presses save rather than surfacing a 422 field path.
 * The backend still validates — this is a courtesy, not the boundary.
 */
export function ruleProblems(rule: RuleDocument): string[] {
  const problems: string[] = [];

  if (!rule.ruleId.trim()) problems.push("a rule needs an id");
  if (!rule.name.trim()) problems.push("a rule needs a name");

  if (!EVENT_NAMESPACES.some((ns) => rule.triggerType.startsWith(ns))) {
    problems.push(
      `“${rule.triggerType}” is not in a known event namespace — see event-bus-spec.md §3`
    );
  }

  if (rule.condition.type === "threshold") {
    if (rule.condition.count < 1) problems.push("a threshold needs to count at least one");
    if (rule.condition.windowSec <= 0) {
      problems.push("a window of zero seconds counts nothing");
    }
  }
  if (rule.condition.type === "none" && rule.condition.windowSec <= 0) {
    problems.push("a window of zero seconds is never quiet");
  }

  if (!ACTION_TYPES.includes(rule.action.type)) {
    problems.push(`nothing dispatches an action of type “${rule.action.type}”`);
  }
  if (rule.cooldownSec < 0) problems.push("a cooldown cannot be negative");

  return problems;
}

/**
 * The rule in the operator's words.
 *
 * ADR-002's first reason for rules-as-data is that "an operator writes rules,
 * not an engineer. The composer UI is plain-English → spec → operator confirms."
 * This is the confirming half — the document read back as a sentence, so what is
 * being armed is legible without reading JSON.
 */
export function describeRule(rule: RuleDocument): string {
  const where = rule.condition.zoneId ?? rule.triggerZoneId;
  const zone = where ? ` in ${where}` : "";
  const subject = rule.triggerType.split(".").pop() ?? rule.triggerType;

  let when: string;
  switch (rule.condition.type) {
    case "threshold": {
      const dwell = rule.condition.minDwellSec
        ? ` of at least ${rule.condition.minDwellSec}s`
        : "";
      when = `${rule.condition.count} ${subject} events${dwell}${zone} within ${rule.condition.windowSec}s`;
      break;
    }
    case "any":
      when = `any ${subject}${zone}`;
      break;
    case "none":
      when = `no ${subject}${zone} for ${rule.condition.windowSec}s`;
      break;
  }

  let then: string;
  switch (rule.action.type) {
    case "slack":
      then = `post “${rule.action.message}” to ${rule.action.channel}`;
      break;
    case "webhook":
      then = `POST to ${rule.action.url}`;
      break;
    case "screen_swap":
      then = `show ${rule.action.contentId} on ${rule.action.screenId}`;
      break;
    case "staff_prompt":
      then = `prompt staff: “${rule.action.message}”`;
      break;
    case "log":
      then = "record it and do nothing else";
      break;
  }

  const cooldown =
    rule.cooldownSec > 0 ? `, then wait ${rule.cooldownSec}s` : ", every time";
  return `When ${when}, ${then}${cooldown}.`;
}
