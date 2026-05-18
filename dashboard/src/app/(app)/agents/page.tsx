"use client";

import {
  Bell,
  CircleAlert,
  Clock,
  Plus,
  Settings2,
  Sparkles,
  Webhook,
  Zap,
} from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Panel } from "@/components/ui/Panel";
import { Pill } from "@/components/ui/Pill";
import { useActiveSession } from "@/lib/session/store";
import { cn, formatRelative } from "@/lib/utils";

type Trigger = "dwell" | "count" | "gaze" | "group" | "exit_funnel";
type Action = "slack" | "webhook" | "screen" | "log";

interface Agent {
  id: string;
  name: string;
  trigger: Trigger;
  conditionText: string;
  action: Action;
  actionText: string;
  active: boolean;
  fired: number;
  lastFired?: number;
  notes?: string;
}

const NOW = Date.UTC(2026, 4, 18, 21, 14, 0);

const seed: Agent[] = [
  {
    id: "ag_entry_crowd",
    name: "Entry Crowd Alert",
    trigger: "count",
    conditionText: "5+ people in Entry Arch for 30s+",
    action: "slack",
    actionText: "Ping #pavilion-ops in Slack",
    active: true,
    fired: 4,
    lastFired: NOW - 12 * 60_000,
  },
  {
    id: "ag_mirror_engage",
    name: "Mirror Engagement",
    trigger: "gaze",
    conditionText: "Gaze on AR Mirror > 5s",
    action: "screen",
    actionText: "Trigger personalised scent recommendation",
    active: true,
    fired: 217,
    lastFired: NOW - 32 * 1000,
  },
  {
    id: "ag_lounge_vip",
    name: "Lounge VIP Detector",
    trigger: "dwell",
    conditionText: "Group of 3+ in Lounge for 4m+",
    action: "slack",
    actionText: "Page brand ambassador to lounge",
    active: true,
    fired: 9,
    lastFired: NOW - 6 * 60_000,
  },
  {
    id: "ag_rfid_capture",
    name: "RFID Capture Logger",
    trigger: "gaze",
    conditionText: "Visitor captures memory at RFID Wall",
    action: "webhook",
    actionText: "POST to CRM /api/leads/anonymous",
    active: true,
    fired: 488,
    lastFired: NOW - 18 * 1000,
  },
  {
    id: "ag_dropoff",
    name: "Entry Drop-off Watchdog",
    trigger: "exit_funnel",
    conditionText: "Entry → exit within 30s exceeds 25%",
    action: "slack",
    actionText: "Alert experience lead — review queue signage",
    active: false,
    fired: 0,
    notes: "Paused — being recalibrated",
  },
];

const TRIGGER_META: Record<Trigger, { label: string; color: string; icon: React.ReactNode }> = {
  count: { label: "People count", color: "text-accent-blue", icon: <CircleAlert size={14} /> },
  dwell: { label: "Dwell threshold", color: "text-accent-cyan", icon: <Clock size={14} /> },
  gaze: { label: "Gaze / attention", color: "text-accent-violet", icon: <Sparkles size={14} /> },
  group: { label: "Group formation", color: "text-accent-amber", icon: <CircleAlert size={14} /> },
  exit_funnel: { label: "Funnel breach", color: "text-accent-red", icon: <CircleAlert size={14} /> },
};

const ACTION_META: Record<Action, { label: string; icon: React.ReactNode }> = {
  slack: { label: "Slack", icon: <Bell size={14} /> },
  webhook: { label: "Webhook", icon: <Webhook size={14} /> },
  screen: { label: "Screen / signage", icon: <Zap size={14} /> },
  log: { label: "Log insight", icon: <Sparkles size={14} /> },
};

export default function AgentsPage() {
  const activeSession = useActiveSession();
  const isDemo = activeSession.isDemo;
  // Fresh sessions start with no rules. The demo keeps the curated seed.
  const [agents, setAgents] = useState(isDemo ? seed : []);

  function toggle(id: string) {
    setAgents((cur) =>
      cur.map((a) => (a.id === id ? { ...a, active: !a.active } : a))
    );
  }

  // Pre-seed the composer textarea with a rule shaped around this session's
  // actual zones and touchpoints so the user immediately sees how to write one.
  const composerSeed = isDemo
    ? `When 5+ people are in the Mirror Room for more than 90 seconds AND the Bottle Wall has fewer than 2 visitors, dim the Bottle Wall lighting by 30% and POST to /api/staff/redirect.`
    : `When 5+ people are in the ${activeSession.zones[0]?.name ?? "Entry"} for more than 30 seconds, ping #ops-${(activeSession.brand ?? "session").toLowerCase().replace(/\s+/g, "-")} in Slack.${
        activeSession.touchpoints[0]
          ? `\n\nWhen a visitor interacts with the ${activeSession.touchpoints[0].name}, POST to https://hooks.your-crm.com/lead.`
          : ""
      }`;

  return (
    <div className="p-5 max-w-[1400px] mx-auto space-y-5">
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <Pill variant="warn" className="mb-2">
            <Zap size={11} />
            Spatial agents
          </Pill>
          <h1 className="text-2xl font-semibold tracking-tight">
            Turn observations into actions.
          </h1>
          <p className="text-sm text-text-secondary mt-1 max-w-xl">
            Watch the spatial graph for patterns. When a condition fires,
            RealmSpace can ping Slack, hit a webhook, change what a screen is
            showing, or log a new insight to the graph.
          </p>
        </div>
        <Button variant="primary" icon={<Plus size={14} />}>New agent</Button>
      </div>

      <div className="grid grid-cols-4 gap-3">
        <Summary label="Active agents" value={agents.filter((a) => a.active).length} />
        <Summary
          label="Fires today"
          value={isDemo ? 718 : 0}
          accent="cyan"
        />
        <Summary
          label="Avg latency"
          value={isDemo ? "240ms" : "—"}
          accent="green"
        />
        <Summary
          label="Insights logged"
          value={isDemo ? 23 : 0}
          accent="violet"
        />
      </div>

      <Panel title="All agents" subtitle="Click to enable / disable">
        {agents.length === 0 && (
          <EmptyState
            icon={<Zap size={18} />}
            title="No agents yet."
            hint="Write a rule below in plain English — RealmSpace translates it to a graph subscription you can enable here."
          />
        )}
        <ul className="divide-y divide-border-hairline -m-5">
          {agents.map((a) => {
            const t = TRIGGER_META[a.trigger];
            const ac = ACTION_META[a.action];
            return (
              <li
                key={a.id}
                className="grid grid-cols-[1fr_auto_auto] items-center gap-4 px-5 py-4 hover:bg-bg-elevated/60 transition-colors"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-medium">{a.name}</span>
                    {!a.active && <Pill variant="neutral">paused</Pill>}
                    {a.notes && (
                      <span className="text-[10px] text-text-muted italic">
                        {a.notes}
                      </span>
                    )}
                  </div>
                  <div className="mt-1.5 flex items-center gap-3 text-xs flex-wrap">
                    <span className={cn("inline-flex items-center gap-1.5", t.color)}>
                      {t.icon}
                      <span className="text-text-secondary">When</span>
                      <span className="text-text-primary">{a.conditionText}</span>
                    </span>
                    <span className="text-text-faint">→</span>
                    <span className="inline-flex items-center gap-1.5 text-text-secondary">
                      <span className="text-accent-amber">{ac.icon}</span>
                      <span className="text-text-primary">{a.actionText}</span>
                    </span>
                  </div>
                </div>
                <div className="hidden md:flex flex-col items-end gap-0.5 min-w-[100px]">
                  <span className="text-lg font-semibold tabular leading-none">
                    {a.fired}
                  </span>
                  <span className="text-[10px] text-text-muted uppercase tracking-[0.15em]">
                    fires
                  </span>
                  {a.lastFired && (
                    <span className="text-[10px] text-text-muted tabular">
                      {formatRelative(NOW - a.lastFired)}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => toggle(a.id)}
                    className={cn(
                      "h-7 w-12 rounded-full relative transition-colors",
                      a.active ? "bg-accent/30 border border-accent/50" : "bg-bg-elevated border border-border-subtle"
                    )}
                  >
                    <span
                      className={cn(
                        "absolute top-0.5 h-5 w-5 rounded-full transition-all",
                        a.active
                          ? "left-[calc(100%-22px)] bg-accent shadow-[var(--glow-green)]"
                          : "left-0.5 bg-text-muted"
                      )}
                    />
                  </button>
                  <Button variant="ghost" size="sm" icon={<Settings2 size={14} />} />
                </div>
              </li>
            );
          })}
        </ul>
      </Panel>

      <div className="grid md:grid-cols-2 gap-5">
        <Panel
          title="Compose new agent"
          subtitle="Plain English. We translate it to a graph subscription."
        >
          <textarea
            key={composerSeed /* re-seed when the active session changes */}
            className="w-full bg-bg-canvas border border-border-subtle rounded-lg p-4 text-sm font-mono text-text-secondary focus:border-accent focus:outline-none min-h-[160px] resize-none"
            defaultValue={composerSeed}
          />
          <div className="flex gap-2 mt-3">
            <Button variant="primary" size="sm" icon={<Sparkles size={14} />}>
              Generate rule
            </Button>
            <Button variant="secondary" size="sm">
              Preview triggers
            </Button>
          </div>
        </Panel>

        <Panel
          title="Recent firings"
          subtitle="Live event log of agent activity"
          padded={false}
        >
          {isDemo ? (
            <ul className="p-3 space-y-1 font-mono text-xs">
              <RecentFire
                when="34s ago"
                text="Mirror Engagement fired for P-216 — screen swapped to 'Rose Nuit' content"
                color="text-accent-violet"
              />
              <RecentFire
                when="1m 18s ago"
                text="RFID Capture Logger fired for P-214 — POST /api/leads ok"
                color="text-accent-blue"
              />
              <RecentFire
                when="4m ago"
                text="Lounge VIP Detector fired — group of 3 dwelling 4m+, paged @ambassador-amaka"
                color="text-accent-amber"
              />
              <RecentFire
                when="11m ago"
                text="Entry Crowd Alert fired — 6 people in Entry Arch for 38s"
                color="text-accent-red"
              />
              <RecentFire
                when="22m ago"
                text="Mirror Engagement fired for P-211 — screen swapped to 'Sable Vert'"
                color="text-accent-violet"
              />
            </ul>
          ) : (
            <EmptyState
              icon={<Sparkles size={18} />}
              title="No firings yet."
              hint="Recent agent activity will appear here in real time."
            />
          )}
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

function RecentFire({
  when,
  text,
  color,
}: {
  when: string;
  text: string;
  color: string;
}) {
  // map text colors → matching background colors (Tailwind v4 needs static classes)
  const bgMap: Record<string, string> = {
    "text-accent-violet": "bg-accent-violet",
    "text-accent-blue": "bg-accent-blue",
    "text-accent-cyan": "bg-accent-cyan",
    "text-accent-amber": "bg-accent-amber",
    "text-accent-red": "bg-accent-red",
    "text-accent": "bg-accent",
  };
  return (
    <li className="grid grid-cols-[80px_8px_1fr] items-center gap-3 px-2 py-1.5 rounded hover:bg-bg-elevated">
      <span className="text-text-faint tabular">{when}</span>
      <span className={cn("h-1.5 w-1.5 rounded-full", bgMap[color] ?? "bg-text-muted")} />
      <span className={color}>{text}</span>
    </li>
  );
}
