"use client";

/**
 * realmspace — the rule composer.
 *
 * ## What changed here, and why the old page could not stay
 *
 * This screen used to hold a *third* rule vocabulary: five hardcoded agents with
 * their own `Trigger = "dwell" | "count" | "gaze" | "group" | "exit_funnel"`,
 * hand-written condition strings, and fire counts (`fired: 488`, "718 fires
 * today", "240ms avg latency") that came from nowhere. Alongside it sat the
 * eight `AgentDefinition`s, with a *fourth* vocabulary of their own.
 *
 * ADR-002 settles it: a rule is one JSON document, evaluated by exactly one
 * evaluator on the edge, and "the browser does not implement rules; it
 * *previews* them". So:
 *
 *  - **Rules** are read from and written to `/v1/rules`. What is listed here is
 *    what the edge will act on, not a local imitation of it.
 *  - **Presets** are the eight definitions, compiled by `agents/presets.ts`.
 *    Three of them are not rules at all and say so rather than being padded
 *    into the shape.
 *  - **Firings** come from `rule.fired` on the log. Zero means zero.
 *  - **Preview** runs the document against this session's events and reports how
 *    often it would have fired. It dispatches nothing.
 *
 * The invented figures are gone on the same grounds the report's and the live
 * tile's were: a number with nothing behind it is worse than a blank, because
 * only one of the two can be checked.
 */

import { Plus, Sparkles, Trash2, Zap } from "lucide-react";
import { useMemo, useState } from "react";

import { listAgents } from "@/agents/registry";
import { notARuleReason, toRule } from "@/agents/presets";
import { previewRule, type RulePreview } from "@/agents/preview";
import type { AgentDefinition } from "@/agents/types";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Panel } from "@/components/ui/Panel";
import { Pill } from "@/components/ui/Pill";
import { readAll } from "@/lib/bus";
import type { RealmEvent } from "@/lib/contracts";
import { describeRule, type RuleDocument, type StoredRule } from "@/lib/contracts/rules";
import { useRuleActivity } from "@/lib/rules/useRuleActivity";
import { useRules } from "@/lib/rules/useRules";
import { useActiveSession } from "@/lib/session/store";
import { getTenantId } from "@/lib/tenant/context";
import { cn, formatRelative } from "@/lib/utils";

export default function AgentsPage() {
  const activeSession = useActiveSession();
  const rules = useRules();
  const activity = useRuleActivity(activeSession.id);
  const [notice, setNotice] = useState<string | null>(null);

  const definitions = useMemo(() => listAgents(), []);

  // Read once per render rather than subscribing: a preview is a thing the
  // operator asks for by pressing a button, and a feed that re-ran it on every
  // detection would show a number that changes while they are reading it.
  const events = useMemo(
    () => readAll(getTenantId(), activeSession.id) as RealmEvent[],
    [activeSession.id]
  );

  // The log's clock, not the browser's — "4m ago" on a replayed session should
  // mean four minutes before the last thing that happened, not before now. The
  // fallback is unreachable in the only branch that reads it: `now` is used to
  // age firings, and with no firings there is nothing to age.
  const now = activity.firings[0]?.at ?? 0;

  async function save(rule: RuleDocument) {
    const problem = await rules.save(rule);
    setNotice(problem ?? `Saved “${rule.name}”. The edge will act on it.`);
  }

  return (
    <div className="p-5 max-w-[1400px] mx-auto space-y-5">
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <Pill variant="warn" className="mb-2">
            <Zap size={11} />
            Spatial rules
          </Pill>
          <h1 className="text-2xl font-semibold tracking-tight">
            Turn observations into actions.
          </h1>
          <p className="text-sm text-text-secondary mt-1 max-w-xl">
            A rule watches the event bus for a pattern. When it matches, the edge
            pings Slack, hits a webhook, changes what a screen is showing, or
            prompts the staff on the floor — within three seconds. Rules are
            evaluated on the edge, never in this browser.
          </p>
        </div>
        <Button variant="primary" icon={<Plus size={14} />} disabled>
          New rule
        </Button>
      </div>

      {notice && (
        <p className="rounded-lg border border-border-subtle bg-bg-elevated px-4 py-2.5 text-sm text-text-secondary">
          {notice}
        </p>
      )}

      <div className="grid grid-cols-3 gap-3">
        <Summary
          label="Armed rules"
          value={rules.status === "ready" ? rules.items.filter((r) => r.enabled).length : "—"}
        />
        <Summary
          label="Firings this session"
          value={activity.firings.length ? Object.values(activity.countByRule).reduce((a, b) => a + b, 0) : 0}
          accent="cyan"
        />
        <Summary
          label="Rules that have fired"
          value={Object.keys(activity.countByRule).length}
          accent="violet"
        />
      </div>

      <Panel
        title="Armed rules"
        subtitle="Stored on the edge. These are what actually fire."
        action={
          <Pill variant={rules.status === "ready" ? "success" : "neutral"}>
            {rules.status === "ready" ? "live" : rules.status}
          </Pill>
        }
      >
        {rules.status !== "ready" ? (
          <EmptyState
            icon={<Zap size={18} />}
            title={rules.status === "loading" ? "Reading rules…" : "No rule store."}
            hint={rules.detail ?? ""}
          />
        ) : rules.items.length === 0 ? (
          <EmptyState
            icon={<Zap size={18} />}
            title="No rules armed."
            hint="Arm one from the presets below, and it takes effect on the next event."
          />
        ) : (
          <ul className="divide-y divide-border-hairline -m-5">
            {rules.items.map((rule) => (
              <RuleRow
                key={rule.ruleId}
                rule={rule}
                fired={activity.countByRule[rule.ruleId] ?? 0}
                lastFired={activity.lastFiredByRule[rule.ruleId]}
                now={now}
                preview={previewRule(rule, events)}
                onRemove={async () => {
                  const problem = await rules.remove(rule.ruleId);
                  setNotice(problem ?? `Removed “${rule.name}”.`);
                }}
              />
            ))}
          </ul>
        )}
      </Panel>

      <Panel
        title="Presets"
        subtitle="Starting points, compiled to the same document an operator would write by hand."
      >
        <ul className="divide-y divide-border-hairline -m-5">
          {definitions.map((definition) => (
            <PresetRow
              key={definition.id}
              definition={definition}
              events={events}
              onArm={save}
            />
          ))}
        </ul>
      </Panel>

      <Panel
        title="Recent firings"
        subtitle="From rule.fired on the log — every match the edge made, in order."
        padded={false}
      >
        {activity.firings.length === 0 ? (
          <EmptyState
            icon={<Sparkles size={18} />}
            title="Nothing has fired."
            hint="A firing appears here the moment a rule matches, whether or not its action succeeded."
          />
        ) : (
          <ul className="p-3 space-y-1 font-mono text-xs">
            {activity.firings.map((firing) => (
              <li
                key={firing.eventId}
                className="grid grid-cols-[86px_1fr] items-baseline gap-3 rounded px-2 py-1.5 hover:bg-bg-elevated"
              >
                <span className="text-text-faint tabular">
                  {formatRelative(Math.max(0, now - firing.at))}
                </span>
                <span className="text-text-secondary">
                  <span className="text-text-primary">{firing.ruleName}</span> —{" "}
                  {firing.matched.observed} {firing.matched.countedBy} matched{" "}
                  <span className="text-text-faint">
                    ({firing.triggerType} · {firing.action.type})
                  </span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}

function RuleRow({
  rule,
  fired,
  lastFired,
  now,
  preview,
  onRemove,
}: {
  rule: StoredRule;
  fired: number;
  lastFired?: number;
  now: number;
  preview: RulePreview;
  onRemove: () => void;
}) {
  return (
    <li className="grid grid-cols-[1fr_auto_auto] items-center gap-4 px-5 py-4">
      <div className="min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm font-medium">{rule.name}</span>
          {!rule.enabled && <Pill variant="neutral">paused</Pill>}
        </div>
        {/* The document read back as a sentence — ADR-002's "operator confirms"
            half. What is armed should be legible without reading JSON. */}
        <p className="mt-1.5 text-xs text-text-secondary">{describeRule(rule)}</p>
        <PreviewNote preview={preview} />
      </div>
      <div className="hidden md:flex flex-col items-end gap-0.5 min-w-[92px]">
        <span className="text-lg font-semibold tabular leading-none">{fired}</span>
        <span className="text-[10px] uppercase tracking-[0.15em] text-text-muted">
          fires
        </span>
        {lastFired ? (
          <span className="tabular text-[10px] text-text-muted">
            {formatRelative(Math.max(0, now - lastFired))}
          </span>
        ) : null}
      </div>
      <Button
        variant="ghost"
        size="sm"
        icon={<Trash2 size={14} />}
        onClick={onRemove}
      />
    </li>
  );
}

function PresetRow({
  definition,
  events,
  onArm,
}: {
  definition: AgentDefinition;
  events: RealmEvent[];
  onArm: (rule: RuleDocument) => void;
}) {
  const rule = toRule(definition);
  const reason = notARuleReason(definition);
  const preview = rule ? previewRule(rule, events) : null;

  return (
    <li className="grid grid-cols-[1fr_auto] items-center gap-4 px-5 py-3">
      <div className="min-w-0">
        <div className="text-sm font-medium">{definition.name}</div>
        <p className="mt-0.5 text-[11px] text-text-muted">{definition.description}</p>
        {rule ? (
          <>
            <p className="mt-1 text-xs text-text-secondary">{describeRule(rule)}</p>
            {preview && <PreviewNote preview={preview} />}
          </>
        ) : (
          /* Not padded into the shape of a rule. Calling all eight of these
             "agents" is what made the split-brain hard to see. */
          <p className="mt-1 text-[11px] italic text-text-faint">
            Not a rule — {reason}
          </p>
        )}
      </div>
      {rule ? (
        <Button variant="secondary" size="sm" onClick={() => onArm(rule)}>
          Arm
        </Button>
      ) : null}
    </li>
  );
}

/**
 * The dry run, in one line.
 *
 * Phrased in the past conditional throughout — "would have fired" — because that
 * is what it is: this browser's log replayed against the document. Nothing here
 * has fired anything.
 */
function PreviewNote({ preview }: { preview: RulePreview }) {
  if (preview.unsupported) {
    return (
      <p className="mt-1 text-[11px] text-text-faint">
        Cannot be previewed here — {preview.unsupported}
      </p>
    );
  }
  if (preview.considered === 0) {
    return (
      <p className="mt-1 text-[11px] text-text-faint">
        Nothing in this session&apos;s log matches its trigger yet.
      </p>
    );
  }
  return (
    <p className="mt-1 text-[11px] text-text-muted">
      Dry run: would have fired{" "}
      <span className="text-text-secondary">{preview.firings.length}×</span> over{" "}
      {preview.considered} matching events in this session.
    </p>
  );
}

function Summary({
  label,
  value,
  accent,
}: {
  label: string;
  value: string | number;
  accent?: "cyan" | "violet" | "green" | "amber";
}) {
  const map = {
    cyan: "text-accent-cyan",
    violet: "text-accent-violet",
    green: "text-accent",
    amber: "text-accent-amber",
  };
  return (
    <div className="panel-elevated px-5 py-4">
      <div className="text-[11px] uppercase tracking-[0.16em] text-text-secondary">
        {label}
      </div>
      <div
        className={cn(
          "text-3xl font-semibold tabular tracking-tight mt-1",
          accent ? map[accent] : "text-text-primary"
        )}
      >
        {value}
      </div>
    </div>
  );
}
