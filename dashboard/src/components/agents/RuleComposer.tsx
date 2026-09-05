"use client";

/**
 * realmspace — where an operator writes a rule.
 *
 * ADR-002's first reason for rules being data at all: *"an operator writes
 * rules, not an engineer. The composer UI is plain-English → spec → operator
 * confirms. That is only possible if a rule is a value the UI can build, show
 * back, and store."* Until now `/agents` could arm the presets and nothing else
 * — the "New rule" button had no handler, and editing one meant a `curl`.
 *
 * ## Two ways in, one document out
 *
 * The sentence box asks the backend to compose (`POST /v1/rules/compose`), and
 * what comes back lands **in the fields below it** rather than being armed. The
 * fields are the whole spec, so an operator who wants something the composer
 * cannot express writes it directly, and one who used the composer can see and
 * change every part of what a model proposed before anything is armed.
 *
 * ## Switched on the discriminator
 *
 * `RuleCondition` and `RuleAction` are closed discriminated unions
 * (`lib/contracts/rules.ts`). Each branch renders its own members, so a member
 * added to the spec shows up here as a missing case rather than as a field
 * nobody can reach — the same pressure `useBenchmark.test.ts` puts on a new
 * event type.
 *
 * ## What it refuses to let an operator do by accident
 *
 * Zones are a picker over this activation's own zones, never a free-text id: a
 * rule naming a zone that does not exist is armed, correct-looking and matches
 * nothing, which `consumers/rules._payload_field` records having shipped once
 * already. `ruleProblems` runs on every keystroke — the reason that function
 * says it exists — and `describeRule` reads the document back as the sentence
 * ADR-002 asks an operator to confirm. `previewRule` says how often it would
 * have fired against this session's log, and dispatches nothing.
 */

import { Sparkles, X } from "lucide-react";
import { useMemo, useState } from "react";

import { previewRule } from "@/agents/preview";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import type { RealmEvent } from "@/lib/contracts";
import {
  describeRule,
  ruleProblems,
  type RuleAction,
  type RuleCondition,
  type RuleDocument,
} from "@/lib/contracts/rules";
import type { ComposedRule } from "@/lib/rules/useRules";
import type { Zone } from "@/lib/session/types";

/** The event types an operator is offered, in the order a floor happens. */
const TRIGGER_TYPES = [
  "spatial.zone_enter",
  "spatial.zone_exit",
  "spatial.dwell",
  "spatial.passby",
  "spatial.occupancy",
  "spatial.group",
  "spatial.gaze",
  "surface.touched",
  "surface.interaction",
  "insight.generated",
  "drift.detected",
  "session.started",
  "session.ended",
] as const;

const FIELD =
  "h-10 px-3 rounded-lg bg-bg-canvas border border-border-subtle text-sm " +
  "focus:border-accent focus:outline-none transition-colors w-full";
const SELECT = `${FIELD} appearance-none cursor-pointer`;
const LABEL =
  "text-[10px] uppercase tracking-[0.16em] text-text-muted font-medium";

/** A blank document, which is a `log` rule on dwell: the least it can do. */
export function blankRule(ruleId: string): RuleDocument {
  return {
    ruleId,
    name: "",
    triggerType: "spatial.dwell",
    triggerZoneId: null,
    condition: { type: "any", zoneId: null },
    action: { type: "log", message: "" },
    enabled: true,
    cooldownSec: 60,
  };
}

/** `r_<slug>_<6 hex>` — the browser's half of the id convention in `presets.ts`. */
export function mintRuleId(name: string): string {
  const slug =
    name
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_|_$/g, "")
      .slice(0, 24) || "rule";
  const tail = Math.floor(Math.random() * 0xffffff)
    .toString(16)
    .padStart(6, "0");
  return `r_${slug}_${tail}`;
}

/**
 * The condition an operator gets when they switch the dropdown.
 *
 * A fresh member rather than a merge, and that is a correctness condition
 * rather than tidiness: the backend refuses unknown keys on a condition
 * (`schemas.ThresholdCondition`, `extra="forbid"`), so a `count` carried over
 * onto a `none` condition is a 422 on a rule the operator can see is fine. The
 * zone survives because it means the same thing in all three.
 */
export function switchCondition(
  current: RuleCondition,
  type: RuleCondition["type"]
): RuleCondition {
  const zoneId = current.zoneId ?? null;
  if (type === "threshold") {
    return { type, count: 5, windowSec: 60, zoneId };
  }
  if (type === "none") {
    return { type, windowSec: 600, zoneId };
  }
  return { type, zoneId };
}

/**
 * The action an operator gets when they switch the dropdown.
 *
 * The message follows them across, because "greet the group at the entrance" is
 * the thing they wrote and it is as true of a Slack post as of a staff prompt.
 * A channel, a URL and a screen id do not: those name somewhere, and carrying
 * one onto a different action would point a rule at a destination the operator
 * chose for something else.
 */
export function switchAction(
  current: RuleAction,
  type: RuleAction["type"]
): RuleAction {
  const message = "message" in current ? (current.message ?? "") : "";
  if (type === "slack") return { type, channel: "", message };
  if (type === "webhook") return { type, url: "" };
  if (type === "screen_swap") return { type, screenId: "", contentId: "" };
  if (type === "staff_prompt") {
    return { type, message, zoneId: null, priority: "normal" };
  }
  return { type, message };
}

interface RuleComposerProps {
  /** The document being written. Pre-filled when editing an existing rule. */
  initial: RuleDocument;
  /** True when `initial` is already stored — its id must not change. */
  editing: boolean;
  zones: Zone[];
  events: RealmEvent[];
  sessionId: string;
  compose: (
    instruction: string,
    sessionId: string
  ) => Promise<ComposedRule | string>;
  onArm: (rule: RuleDocument) => void;
  onClose: () => void;
}

export function RuleComposer({
  initial,
  editing,
  zones,
  events,
  sessionId,
  compose,
  onArm,
  onClose,
}: RuleComposerProps) {
  const [rule, setRule] = useState<RuleDocument>(initial);
  const [instruction, setInstruction] = useState("");
  const [composing, setComposing] = useState(false);
  const [composed, setComposed] = useState<ComposedRule | null>(null);
  const [transportError, setTransportError] = useState<string | null>(null);

  const problems = ruleProblems(rule);
  const preview = useMemo(() => previewRule(rule, events), [rule, events]);

  function set(patch: Partial<RuleDocument>) {
    setRule((r) => ({ ...r, ...patch }));
  }

  async function askForOne() {
    if (!instruction.trim()) return;
    setComposing(true);
    setTransportError(null);
    const result = await compose(instruction, sessionId);
    setComposing(false);
    if (typeof result === "string") {
      setTransportError(result);
      return;
    }
    setComposed(result);
    // Into the fields, never straight into the room. The id is kept when
    // editing: a rule that changed id because its message was rewritten would
    // leave the old one armed.
    if (result.rule) {
      setRule({ ...result.rule, ruleId: editing ? rule.ruleId : result.rule.ruleId });
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-sm font-medium">
            {editing ? `Editing “${initial.name}”` : "New rule"}
          </h3>
          <p className="mt-0.5 text-[11px] text-text-muted">
            Nothing is armed until you press Arm. The edge evaluates it, never
            this browser.
          </p>
        </div>
        <Button variant="ghost" size="sm" icon={<X size={14} />} onClick={onClose} />
      </div>

      {/* ── plain English ─────────────────────────────────────────────── */}
      <div className="space-y-2">
        <label className={LABEL} htmlFor="rule-instruction">
          Describe it
        </label>
        <div className="flex gap-2">
          <input
            id="rule-instruction"
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void askForOne();
            }}
            placeholder="when five people wait at the entrance for 30 seconds, tell staff"
            className={FIELD}
          />
          <Button
            variant="secondary"
            size="sm"
            icon={<Sparkles size={14} />}
            onClick={() => void askForOne()}
            disabled={composing || !instruction.trim()}
          >
            {composing ? "Composing…" : "Compose"}
          </Button>
        </div>

        {transportError && (
          <p className="text-[11px] text-accent-action">{transportError}</p>
        )}

        {composed && (
          <div className="space-y-1.5 rounded-lg border border-border-subtle bg-bg-elevated px-3 py-2.5">
            <div className="flex items-center gap-2">
              {/* Which kind of thing wrote this. A model's draft and a keyword
                  match are different claims and an operator judges them
                  differently — the rule every LLM surface here follows. */}
              <Pill variant={composed.basis === "deterministic" ? "neutral" : "success"}>
                {composed.basis}
              </Pill>
              <span className="text-[11px] text-text-muted tabular">
                {composed.tookMs}ms
              </span>
            </div>
            {composed.rule ? (
              composed.warnings.map((w) => (
                <p key={w} className="text-[11px] text-text-secondary">
                  {w}
                </p>
              ))
            ) : (
              <>
                <p className="text-[11px] text-text-secondary">{composed.reason}</p>
                {composed.canBuild.length > 0 && (
                  <ul className="mt-1 space-y-0.5">
                    {composed.canBuild.map((s) => (
                      <li key={s.shape} className="text-[11px] text-text-faint">
                        {s.asks}
                        {s.examples[0] ? ` — “${s.examples[0]}”` : ""}
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </div>
        )}
      </div>

      {/* ── the document ──────────────────────────────────────────────── */}
      <div className="grid gap-3 md:grid-cols-2">
        <Field label="Name">
          <input
            value={rule.name}
            onChange={(e) => set({ name: e.target.value })}
            placeholder="Entrance crowding"
            className={FIELD}
          />
        </Field>

        <Field label="When this happens">
          <select
            value={rule.triggerType}
            onChange={(e) => set({ triggerType: e.target.value })}
            className={SELECT}
          >
            {TRIGGER_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </Field>

        <Field label="In this zone">
          <ZonePicker
            zones={zones}
            value={rule.triggerZoneId ?? null}
            onChange={(zoneId) => set({ triggerZoneId: zoneId })}
          />
        </Field>

        <Field label="Cooldown (seconds)">
          <input
            type="number"
            min={0}
            value={rule.cooldownSec}
            onChange={(e) =>
              set({ cooldownSec: Math.max(0, parseInt(e.target.value || "0", 10)) })
            }
            className={`${FIELD} tabular`}
          />
        </Field>
      </div>

      <ConditionFields
        condition={rule.condition}
        zones={zones}
        onChange={(condition) => set({ condition })}
      />
      <ActionFields action={rule.action} zones={zones} onChange={(action) => set({ action })} />

      {/* ── read it back, then arm it ─────────────────────────────────── */}
      <div className="space-y-2 rounded-lg border border-border-subtle bg-bg-elevated px-3 py-2.5">
        <p className="text-xs text-text-secondary">{describeRule(rule)}</p>
        {preview.unsupported ? (
          <p className="text-[11px] text-text-faint">
            Cannot be previewed here — {preview.unsupported}
          </p>
        ) : (
          <p className="text-[11px] text-text-faint">
            Against this session&apos;s log it would have fired{" "}
            <span className="tabular">{preview.firings.length}</span>{" "}
            {preview.firings.length === 1 ? "time" : "times"}, from{" "}
            <span className="tabular">{preview.considered}</span> matching events.
            Nothing has been dispatched.
          </p>
        )}
        {problems.map((problem) => (
          <p key={problem} className="text-[11px] text-accent-action">
            {problem}
          </p>
        ))}
      </div>

      <div className="flex items-center gap-2">
        <Button
          variant="primary"
          size="sm"
          onClick={() => onArm(rule)}
          disabled={problems.length > 0}
        >
          {editing ? "Save changes" : "Arm"}
        </Button>
        <Button variant="ghost" size="sm" onClick={onClose}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="space-y-1.5 block">
      <span className={LABEL}>{label}</span>
      {children}
    </label>
  );
}

/**
 * The zones this activation has, and "every zone".
 *
 * `null` is a real choice rather than an empty one: it is what
 * `triggerZoneId: null` means to the evaluator, and what the crowding presets
 * use for a zone nobody named.
 */
function ZonePicker({
  zones,
  value,
  onChange,
}: {
  zones: Zone[];
  value: string | null;
  onChange: (zoneId: string | null) => void;
}) {
  return (
    <select
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value || null)}
      className={SELECT}
    >
      <option value="">Every zone</option>
      {zones.map((z) => (
        <option key={z.id} value={z.id}>
          {z.name}
        </option>
      ))}
    </select>
  );
}

function ConditionFields({
  condition,
  zones,
  onChange,
}: {
  condition: RuleCondition;
  zones: Zone[];
  onChange: (condition: RuleCondition) => void;
}) {
  return (
    <div className="space-y-3">
      <div className="grid gap-3 md:grid-cols-2">
        <Field label="Condition">
          <select
            value={condition.type}
            onChange={(e) =>
              onChange(
                switchCondition(condition, e.target.value as RuleCondition["type"])
              )
            }
            className={SELECT}
          >
            <option value="any">any — the event itself</option>
            <option value="threshold">threshold — N people in a window</option>
            <option value="none">none — it has not happened for a while</option>
          </select>
        </Field>

        <Field label="Counting only this zone">
          <ZonePicker
            zones={zones}
            value={condition.zoneId ?? null}
            onChange={(zoneId) => onChange({ ...condition, zoneId })}
          />
        </Field>

        {condition.type === "threshold" && (
          <>
            <Field label="How many people">
              <input
                type="number"
                min={1}
                value={condition.count}
                onChange={(e) =>
                  onChange({
                    ...condition,
                    count: Math.max(1, parseInt(e.target.value || "1", 10)),
                  })
                }
                className={`${FIELD} tabular`}
              />
            </Field>
            <Field label="Within (seconds)">
              <input
                type="number"
                min={1}
                value={condition.windowSec}
                onChange={(e) =>
                  onChange({
                    ...condition,
                    windowSec: Math.max(1, parseInt(e.target.value || "1", 10)),
                  })
                }
                className={`${FIELD} tabular`}
              />
            </Field>
            <Field label="Ignoring dwells shorter than (seconds)">
              <input
                type="number"
                min={0}
                value={condition.minDwellSec ?? ""}
                placeholder="—"
                onChange={(e) =>
                  onChange({
                    ...condition,
                    minDwellSec: e.target.value
                      ? parseInt(e.target.value, 10)
                      : null,
                  })
                }
                className={`${FIELD} tabular`}
              />
            </Field>
          </>
        )}

        {condition.type === "none" && (
          <Field label="Quiet for (seconds)">
            <input
              type="number"
              min={1}
              value={condition.windowSec}
              onChange={(e) =>
                onChange({
                  ...condition,
                  windowSec: Math.max(1, parseInt(e.target.value || "1", 10)),
                })
              }
              className={`${FIELD} tabular`}
            />
          </Field>
        )}
      </div>

      {condition.type !== "none" && (
        <StatusFilter
          value={condition.payloadEquals ?? null}
          onChange={(payloadEquals) => onChange({ ...condition, payloadEquals })}
        />
      )}
    </div>
  );
}

/**
 * `payloadEquals`, offered as the one thing operators actually need it for.
 *
 * `spatial.occupancy` says both that a zone filled and that it cleared, and
 * `spatial.group` says formed, changed and dissolved. A rule that does not
 * narrow acts on both ends — "the entrance is at capacity", raised again as it
 * empties. The general map is in the document and expressible by hand; what is
 * on screen is the status, because that is the case that bites.
 */
function StatusFilter({
  value,
  onChange,
}: {
  value: Record<string, string> | null;
  onChange: (next: Record<string, string> | null) => void;
}) {
  const status = value?.status ?? "";
  return (
    <Field label="Only when the event says">
      <select
        value={status}
        onChange={(e) => onChange(e.target.value ? { status: e.target.value } : null)}
        className={SELECT}
      >
        <option value="">Anything (both ends of a crossing)</option>
        <option value="over">over — a zone reached its capacity</option>
        <option value="cleared">cleared — it dropped back below</option>
        <option value="formed">formed — a group came together</option>
        <option value="dissolved">dissolved — a group broke up</option>
      </select>
    </Field>
  );
}

function ActionFields({
  action,
  zones,
  onChange,
}: {
  action: RuleAction;
  zones: Zone[];
  onChange: (action: RuleAction) => void;
}) {
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <Field label="Then do this">
        <select
          value={action.type}
          onChange={(e) =>
            onChange(switchAction(action, e.target.value as RuleAction["type"]))
          }
          className={SELECT}
        >
          <option value="staff_prompt">staff_prompt — tell the floor</option>
          <option value="log">log — record that it matched</option>
          <option value="slack">slack — post to a channel</option>
          <option value="webhook">webhook — POST somewhere</option>
          <option value="screen_swap">screen_swap — change a screen</option>
        </select>
      </Field>

      {action.type === "slack" && (
        <>
          <Field label="Channel">
            <input
              value={action.channel}
              onChange={(e) => onChange({ ...action, channel: e.target.value })}
              placeholder="#ops"
              className={FIELD}
            />
          </Field>
          <Field label="Message">
            <input
              value={action.message}
              onChange={(e) => onChange({ ...action, message: e.target.value })}
              className={FIELD}
            />
          </Field>
        </>
      )}

      {action.type === "webhook" && (
        <Field label="URL">
          <input
            value={action.url}
            onChange={(e) => onChange({ ...action, url: e.target.value })}
            placeholder="https://…"
            className={FIELD}
          />
        </Field>
      )}

      {action.type === "screen_swap" && (
        <>
          <Field label="Screen">
            <input
              value={action.screenId}
              onChange={(e) => onChange({ ...action, screenId: e.target.value })}
              className={FIELD}
            />
          </Field>
          <Field label="Content">
            <input
              value={action.contentId}
              onChange={(e) => onChange({ ...action, contentId: e.target.value })}
              className={FIELD}
            />
          </Field>
        </>
      )}

      {action.type === "staff_prompt" && (
        <>
          <Field label="What staff should do">
            <input
              value={action.message}
              onChange={(e) => onChange({ ...action, message: e.target.value })}
              placeholder="Greet the group at the entrance"
              className={FIELD}
            />
          </Field>
          <Field label="Shown for this zone">
            <ZonePicker
              zones={zones}
              value={action.zoneId ?? null}
              onChange={(zoneId) => onChange({ ...action, zoneId })}
            />
          </Field>
        </>
      )}

      {action.type === "log" && (
        <Field label="Message">
          <input
            value={action.message ?? ""}
            onChange={(e) => onChange({ ...action, message: e.target.value })}
            className={FIELD}
          />
        </Field>
      )}
    </div>
  );
}
