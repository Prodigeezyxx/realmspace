"use client";

import {
  Bell,
  CircleAlert,
  Clock,
  Plus,
  Settings2,
  Sparkles,
  Trash2,
  Webhook,
  Zap,
} from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Panel } from "@/components/ui/Panel";
import { Pill } from "@/components/ui/Pill";
import { getTenantId } from "@/lib/tenant/context";
import { useActiveSession } from "@/lib/session/store";
import { cn } from "@/lib/utils";

// ── Types ──────────────────────────────────────────────────────────────────

interface RuleCondition {
  type: string;
  count?: number | null;
  windowSec?: number;
  zoneId?: string | null;
  minDwellSec?: number | null;
}

interface RuleAction {
  type: string;
  channel?: string | null;
  url?: string | null;
  message?: string;
  screenId?: string | null;
}

interface BackendRule {
  ruleId: string;
  tenantId: string;
  name: string;
  triggerType: string;
  triggerZoneId: string | null;
  condition: RuleCondition;
  action: RuleAction;
  enabled: boolean;
  cooldownSec: number;
}

interface AgentUI {
  id: string;
  name: string;
  triggerType: string;
  conditionText: string;
  actionType: string;
  actionText: string;
  enabled: boolean;
  cooldownSec: number;
}

// ── Backend client ─────────────────────────────────────────────────────────

const BUS_URL =
  (typeof process !== "undefined" &&
    process.env.NEXT_PUBLIC_BUS_URL?.replace(/\/$/, "")) ||
  "http://localhost:8000";

async function fetchRules(tenantId: string): Promise<BackendRule[]> {
  const res = await fetch(`${BUS_URL}/v1/rules/${tenantId}`);
  if (!res.ok) return [];
  const data = await res.json();
  return data.rules ?? [];
}

async function createRule(tenantId: string, rule: {
  name: string; triggerType: string; triggerZoneId?: string;
  condition: RuleCondition; action: RuleAction; cooldownSec?: number;
}): Promise<BackendRule | null> {
  const res = await fetch(`${BUS_URL}/v1/rules`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tenantId, ...rule }),
  });
  if (!res.ok) return null;
  return res.json();
}

async function toggleRule(ruleId: string, enabled: boolean) {
  await fetch(`${BUS_URL}/v1/rules/${ruleId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
}

async function deleteRule(ruleId: string) {
  await fetch(`${BUS_URL}/v1/rules/${ruleId}`, { method: "DELETE" });
}

// ── Helpers ────────────────────────────────────────────────────────────────

function backendToUI(r: BackendRule): AgentUI {
  const cond = r.condition;
  let conditionText = "";
  if (cond.type === "threshold") {
    conditionText = `${cond.count ?? "?"}+ ${r.triggerType.replace("spatial.", "")} events`;
    if (r.triggerZoneId) conditionText += ` in ${r.triggerZoneId}`;
    if (cond.windowSec) conditionText += ` within ${cond.windowSec}s`;
  } else if (cond.type === "any") {
    conditionText = `Any ${r.triggerType.replace("spatial.", "")}`;
    if (r.triggerZoneId) conditionText += ` in ${r.triggerZoneId}`;
  } else {
    conditionText = `${cond.type} ${r.triggerType.replace("spatial.", "")}`;
  }

  const act = r.action;
  let actionText = "";
  if (act.type === "slack") actionText = `Ping ${act.channel ?? "#ops"} in Slack`;
  else if (act.type === "webhook") actionText = `POST to ${act.url ?? "webhook"}`;
  else if (act.type === "screen_swap") actionText = `Swap ${act.screenId ?? "screen"}`;
  else if (act.type === "staff_prompt") actionText = `Prompt staff: ${act.message ?? ""}`;
  else if (act.type === "log") actionText = act.message ?? "Log insight";
  else actionText = act.type;

  return {
    id: r.ruleId,
    name: r.name,
    triggerType: r.triggerType,
    conditionText,
    actionType: act.type,
    actionText,
    enabled: r.enabled,
    cooldownSec: r.cooldownSec,
  };
}

// ── Page ───────────────────────────────────────────────────────────────────

export default function AgentsPage() {
  const activeSession = useActiveSession();
  const isDemo = activeSession.isDemo;
  const tenantId = getTenantId();

  const [agents, setAgents] = useState<AgentUI[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Fetch rules on mount and when demo mode changes
  useEffect(() => {
    if (!isDemo) { setAgents([]); setLoading(false); return; }
    setLoading(true);
    fetchRules(tenantId)
      .then((rules) => setAgents(rules.map(backendToUI)))
      .catch(() => setError("Could not reach backend"))
      .finally(() => setLoading(false));
  }, [isDemo, tenantId]);

  async function handleToggle(agent: AgentUI) {
    const newEnabled = !agent.enabled;
    try {
      await toggleRule(agent.id, newEnabled);
      setAgents((cur) => cur.map((a) => a.id === agent.id ? { ...a, enabled: newEnabled } : a));
    } catch {
      // revert on failure
    }
  }

  async function handleDelete(agent: AgentUI) {
    try {
      await deleteRule(agent.id);
      setAgents((cur) => cur.filter((a) => a.id !== agent.id));
    } catch {
      // revert on failure
    }
  }

  async function handleCreate() {
    const zone = activeSession.zones[0];
    try {
      const rule = await createRule(tenantId, {
        name: `Alert: ${zone?.name ?? "zone"} entry`,
        triggerType: "spatial.zone_enter",
        triggerZoneId: zone?.id ?? null,
        condition: { type: "threshold", count: 3, windowSec: 60 },
        action: { type: "log", message: `3+ people entered ${zone?.name ?? "zone"}` },
        cooldownSec: 60,
      });
      if (rule) {
        setAgents((cur) => [...cur, backendToUI(rule)]);
      }
    } catch {
      // silently fail
    }
  }

  if (!isDemo) {
    return (
      <div className="p-5 max-w-[1400px] mx-auto">
        <EmptyState
          variant="page"
          icon={<Zap size={26} strokeWidth={1.8} />}
          title="Agents activate once a session is recording."
          hint="Create spatial rules that fire actions in real time — ping Slack, hit webhooks, swap screens. Start recording a session to begin."
          cta={{ href: "/live", label: "Open live & start recording" }}
        />
      </div>
    );
  }

  return (
    <div className="p-5 max-w-[1400px] mx-auto space-y-5">
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <Pill variant="warn" className="mb-2">
            <Zap size={11} />
            Rules Engine · Phase 3
          </Pill>
          <h1 className="text-2xl font-semibold tracking-tight">
            Turn observations into actions.
          </h1>
          <p className="text-sm text-text-secondary mt-1 max-w-xl">
            Every spatial event is evaluated against your rules. When a condition
            is met, RealmSpace dispatches the action — Slack, webhook, screen
            swap, staff prompt, or logged insight. Rules run on the edge kit
            with sub-3 second latency.
          </p>
        </div>
        <Button variant="primary" icon={<Plus size={14} />} onClick={handleCreate}>
          New rule
        </Button>
      </div>

      {/* Summary */}
      <div className="grid grid-cols-4 gap-3">
        <Summary label="Active rules" value={agents.filter((a) => a.enabled).length} />
        <Summary label="Total rules" value={agents.length} accent="cyan" />
        <Summary label="Rule types" value={[...new Set(agents.map((a) => a.triggerType))].length} accent="green" />
        <Summary label="Action types" value={[...new Set(agents.map((a) => a.actionType))].length} accent="violet" />
      </div>

      {/* Rules list */}
      <Panel title="Active rules" subtitle={`${agents.length} rules from the edge engine`}>
        {loading && (
          <div className="p-8 text-center text-sm text-text-muted animate-pulse">
            Loading rules…
          </div>
        )}
        {error && (
          <div className="p-4 text-sm text-accent-amber">
            {error} — rules engine may not be running. Start the backend with{" "}
            <code className="text-xs bg-bg-elevated px-1 rounded">uvicorn app.main:app</code>
          </div>
        )}
        {!loading && agents.length === 0 && (
          <EmptyState
            icon={<Zap size={18} />}
            title="No rules yet."
            hint='Click "New rule" to create one. Rules are evaluated on every spatial event in real time.'
          />
        )}
        <ul className="divide-y divide-border-hairline -m-5">
          {agents.map((a) => (
            <li
              key={a.id}
              className="grid grid-cols-[1fr_auto] items-center gap-4 px-5 py-4 hover:bg-bg-elevated/60 transition-colors"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm font-medium">{a.name}</span>
                  {!a.enabled && <Pill variant="neutral">paused</Pill>}
                  <span className="text-[10px] text-text-muted font-mono">
                    {a.id.slice(0, 30)}…
                  </span>
                </div>
                <div className="mt-1.5 flex items-center gap-3 text-xs flex-wrap">
                  <span className="inline-flex items-center gap-1.5 text-accent-cyan">
                    <Clock size={14} />
                    <span className="text-text-secondary">When</span>
                    <span className="text-text-primary">{a.conditionText}</span>
                  </span>
                  <span className="text-text-faint">→</span>
                  <span className="inline-flex items-center gap-1.5 text-accent-amber">
                    {a.actionType === "slack" ? <Bell size={14} /> :
                     a.actionType === "webhook" ? <Webhook size={14} /> :
                     a.actionType === "screen_swap" ? <Zap size={14} /> :
                     <Sparkles size={14} />}
                    <span className="text-text-primary">{a.actionText}</span>
                  </span>
                </div>
                <div className="mt-1 flex gap-2 text-[10px] text-text-muted">
                  <span>cooldown: {a.cooldownSec}s</span>
                  <span>trigger: {a.triggerType}</span>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => handleToggle(a)}
                  className={cn(
                    "h-7 w-12 rounded-full relative transition-colors",
                    a.enabled
                      ? "bg-accent/30 border border-accent/50"
                      : "bg-bg-elevated border border-border-subtle"
                  )}
                  title={a.enabled ? "Disable rule" : "Enable rule"}
                >
                  <span
                    className={cn(
                      "absolute top-0.5 h-5 w-5 rounded-full transition-all",
                      a.enabled
                        ? "left-[calc(100%-22px)] bg-accent shadow-[var(--glow-green)]"
                        : "left-0.5 bg-text-muted"
                    )}
                  />
                </button>
                <Button
                  variant="ghost"
                  size="sm"
                  icon={<Trash2 size={14} />}
                  onClick={() => handleDelete(a)}
                  title="Delete rule"
                />
              </div>
            </li>
          ))}
        </ul>
      </Panel>

      {/* Compose section */}
      <div className="grid md:grid-cols-2 gap-5">
        <Panel
          title="Compose new rule"
          subtitle="Plain English. The edge engine evaluates conditions on every event."
        >
          <textarea
            className="w-full bg-bg-canvas border border-border-subtle rounded-lg p-4 text-sm font-mono text-text-secondary focus:border-accent focus:outline-none min-h-[120px] resize-none"
            defaultValue={
              activeSession.zones[0]
                ? `When 3+ people enter the ${activeSession.zones[0].name}, ping #ops in Slack.`
                : "When 5+ people dwell in the entry zone for 30s, ping #ops in Slack."
            }
            placeholder="Describe your rule in plain English…"
          />
          <div className="flex gap-2 mt-3">
            <Button variant="primary" size="sm" icon={<Sparkles size={14} />} onClick={handleCreate}>
              Create rule
            </Button>
            <Button variant="secondary" size="sm" onClick={handleCreate}>
              Quick add (zone entry)
            </Button>
          </div>
          <p className="text-[10px] text-text-muted mt-3">
            Rules are persisted to SQLite, evaluated on every bus event, and
            dispatched within 3 seconds. Use the REST API for advanced
            configuration.
          </p>
        </Panel>

        <Panel title="Quick reference" subtitle="Supported triggers and actions">
          <div className="space-y-3 text-xs">
            <div>
              <div className="text-[10px] uppercase tracking-[0.16em] text-text-muted mb-1.5">Triggers</div>
              <div className="flex flex-wrap gap-1">
                {["spatial.zone_enter", "spatial.zone_exit", "spatial.dwell", "spatial.passby", "surface.interaction"].map((t) => (
                  <span key={t} className="text-[10px] bg-bg-elevated border border-border-subtle px-2 py-1 rounded-md font-mono">{t}</span>
                ))}
              </div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-[0.16em] text-text-muted mb-1.5">Actions</div>
              <div className="flex flex-wrap gap-1">
                {["slack", "webhook", "screen_swap", "staff_prompt", "log"].map((a) => (
                  <span key={a} className="text-[10px] bg-bg-elevated border border-border-subtle px-2 py-1 rounded-md font-mono">{a}</span>
                ))}
              </div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-[0.16em] text-text-muted mb-1.5">Conditions</div>
              <div className="flex flex-wrap gap-1">
                {["threshold", "any", "none"].map((c) => (
                  <span key={c} className="text-[10px] bg-bg-elevated border border-border-subtle px-2 py-1 rounded-md font-mono">{c}</span>
                ))}
              </div>
            </div>
          </div>
        </Panel>
      </div>
    </div>
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
  const map: Record<string, string> = {
    cyan: "text-accent-cyan",
    violet: "text-accent-violet",
    green: "text-accent",
    amber: "text-accent-amber",
  };
  return (
    <div className="panel-elevated px-5 py-4">
      <div className="text-[11px] uppercase tracking-[0.16em] text-text-secondary">{label}</div>
      <div className={cn("text-3xl font-semibold tabular tracking-tight mt-1", accent ? map[accent] : "text-text-primary")}>
        {value}
      </div>
    </div>
  );
}
