"use client";

import {
  ArrowRight,
  Brain,
  Code2,
  Database,
  MessageSquareText,
  Sparkles,
} from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Panel } from "@/components/ui/Panel";
import { Pill } from "@/components/ui/Pill";
import { getTenantId } from "@/lib/tenant/context";
import { useActiveSession } from "@/lib/session/store";
import { cn } from "@/lib/utils";
import type { NlqOutput } from "@/skills/nlq";

// ── Backend ask client ─────────────────────────────────────────────────────

const BUS_URL =
  (typeof process !== "undefined" &&
    process.env.NEXT_PUBLIC_BUS_URL?.replace(/\/$/, "")) ||
  "http://localhost:8000";

interface AskResponse {
  answer: string;
  chartType?: string;
  template?: string;
  fallback?: boolean;
  table?: { zone_id?: string; surface_id?: string; [k: string]: unknown }[];
  labels?: string[];
  values?: number[];
  value?: number;
}

async function fetchAsk(
  question: string,
  tenantId: string,
  sessionId: string
): Promise<NlqOutput> {
  const res = await fetch(`${BUS_URL}/v1/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      question,
      tenantId,
      sessionId,
    }),
  });

  if (!res.ok) {
    const err = await res.text().catch(() => "unknown error");
    throw new Error(`Ask API ${res.status}: ${err}`);
  }

  const data: AskResponse = await res.json();

  // Map the backend response to the NlqOutput shape the UI expects
  const chartData =
    data.labels && data.values
      ? {
          type: (data.chartType as "bar" | "line" | "pie") ?? "bar",
          labels: data.labels,
          values: data.values,
        }
      : data.value !== undefined && data.value !== null
        ? {
            type: "bar" as const,
            labels: [data.answer],
            values: [data.value],
          }
        : undefined;

  return {
    query: question,
    cypher: data.template
      ? `-- template: ${data.template}\n-- chart: ${data.chartType ?? "text"}`
      : data.fallback
        ? "-- fallback: no matching template"
        : "-- answered",
    explanation: data.answer,
    chartData,
    raw: data.table ?? { value: data.value, labels: data.labels, values: data.values },
  };
}

// ── Suggested questions ────────────────────────────────────────────────────

const suggestedQueries = [
  "How many people visited?",
  "What was the average dwell time?",
  "Dwell time by zone",
  "Which zone had the most entries?",
  "How many surface interactions?",
  "How long did the session last?",
  "Breakdown of event types",
  "How many people were engaged (stayed 60s+)?",
];

// ── Page component ─────────────────────────────────────────────────────────

interface ConversationTurn {
  q: string;
  nlq: NlqOutput;
  thinking?: boolean;
  error?: string;
}

export default function AskPage() {
  const activeSession = useActiveSession();
  const isDemo = activeSession.isDemo;

  const [q, setQ] = useState("");
  const [turns, setTurns] = useState<ConversationTurn[]>([]);

  async function ask(query: string) {
    if (!query.trim()) return;
    setTurns((cur) => [
      ...cur,
      { q: query, nlq: { query, cypher: "", explanation: "…" }, thinking: true },
    ]);
    setQ("");

    const tenantId = getTenantId();
    const sessionId = activeSession.id;

    try {
      const nlq = await fetchAsk(query, tenantId, sessionId);
      setTurns((cur) => {
        const next = [...cur];
        next[next.length - 1] = { q: query, nlq };
        return next;
      });
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Could not reach the backend.";
      setTurns((cur) => {
        const next = [...cur];
        next[next.length - 1] = {
          q: query,
          nlq: {
            query,
            cypher: "-- error",
            explanation: message,
          },
          error: message,
        };
        return next;
      });
    }
  }

  if (!isDemo) {
    return <AskEmptyState />;
  }

  return (
    <div className="p-5 max-w-[1400px] mx-auto grid grid-cols-12 gap-5">
      {/* Main column */}
      <div className="col-span-12 lg:col-span-8 space-y-5">
        <div>
          <Pill variant="info" className="mb-2">
            <Brain size={11} />
            Ask the Room · NLQ engine
          </Pill>
          <h1 className="text-3xl font-semibold tracking-tight">
            Plain English. Real answers.
          </h1>
          <p className="text-sm text-text-secondary mt-1.5 max-w-xl">
            Your question is matched to a constrained SQL template, executed
            against the durable event bus, and returned with a chart-ready
            answer. Powered by the Hermes LLM via OpenRouter for template
            selection.
          </p>
        </div>

        {/* Conversation */}
        <div className="space-y-5">
          {turns.map((t, i) => (
            <AnswerCard key={i} turn={t} />
          ))}
        </div>

        {/* Input */}
        <div className="panel-elevated p-4 sticky bottom-4 z-10">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              ask(q);
            }}
            className="flex items-center gap-3"
          >
            <MessageSquareText size={18} className="text-accent shrink-0 ml-1" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Ask the room…  (e.g. how long was P-216 in the lounge?)"
              className="flex-1 bg-transparent outline-none text-base placeholder:text-text-muted py-2"
              autoFocus
            />
            <Button
              type="submit"
              variant="primary"
              size="md"
              iconAfter={<ArrowRight size={14} />}
              disabled={!q.trim()}
            >
              Ask
            </Button>
          </form>
        </div>
      </div>

      {/* Sidebar */}
      <div className="col-span-12 lg:col-span-4 space-y-5">
        <Panel
          title="Suggested questions"
          subtitle="Click to ask"
          action={<Sparkles size={14} className="text-accent-violet" />}
        >
          <ul className="space-y-1.5">
            {suggestedQueries.map((s) => (
              <li key={s}>
                <button
                  onClick={() => ask(s)}
                  className="w-full text-left text-sm px-3 py-2.5 rounded-md hover:bg-bg-elevated transition-colors text-text-secondary hover:text-text-primary border border-transparent hover:border-border-subtle"
                >
                  {s}
                </button>
              </li>
            ))}
          </ul>
        </Panel>

        <Panel title="Schema in context" subtitle="What the engine can query">
          <div className="space-y-3 text-xs">
            <SchemaGroup
              title="Templates (14)"
              items={[
                "Visitors",
                "Dwell time",
                "Dwell by zone",
                "Zone entries",
                "Person paths",
                "Surface interactions",
                "Passbys",
                "Engaged",
                "Session span",
                "Event types",
                "Concurrency",
                "Traffic",
              ]}
            />
            <SchemaGroup
              title="Event types queried"
              items={[
                "perception.detection",
                "spatial.zone_enter",
                "spatial.zone_exit",
                "spatial.dwell",
                "spatial.passby",
                "surface.interaction",
              ]}
            />
          </div>
        </Panel>

        <Panel title="Stack" subtitle="What runs your question">
          <ul className="space-y-2 text-xs text-text-secondary">
            <StackRow
              icon={<MessageSquareText size={12} />}
              label="LLM · laguna-xs-2.1 (free)"
              ms={320}
            />
            <StackRow
              icon={<Code2 size={12} />}
              label="SQL template selection"
              ms={45}
            />
            <StackRow
              icon={<Database size={12} />}
              label="SQLite execution"
              ms={12}
            />
            <StackRow icon={<Brain size={12} />} label="Result formatting" ms={8} />
          </ul>
          <div className="mt-3 pt-3 border-t border-border-hairline flex justify-between text-xs">
            <span className="text-text-muted">Total (typical)</span>
            <span className="tabular text-accent font-medium">~385 ms</span>
          </div>
        </Panel>
      </div>
    </div>
  );
}

function StackRow({
  icon,
  label,
  ms,
}: {
  icon: React.ReactNode;
  label: string;
  ms: number;
}) {
  return (
    <li className="flex items-center justify-between gap-3">
      <span className="flex items-center gap-2 text-text-secondary">
        <span className="text-text-muted">{icon}</span>
        {label}
      </span>
      <span className="tabular text-text-muted">{ms} ms</span>
    </li>
  );
}

function SchemaGroup({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-[0.18em] text-text-muted mb-1.5">
        {title}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {items.map((i) => (
          <span
            key={i}
            className="text-[10px] tabular bg-bg-elevated border border-border-subtle px-2 py-1 rounded-md text-text-secondary font-mono"
          >
            {i}
          </span>
        ))}
      </div>
    </div>
  );
}

function AnswerCard({ turn }: { turn: ConversationTurn }) {
  const { explanation, cypher, chartData, raw } = turn.nlq;
  const chartType = chartData?.type ?? "text";

  if (turn.thinking) {
    return (
      <div className="panel p-5 text-sm text-text-muted animate-pulse">
        Querying the room…
      </div>
    );
  }

  if (turn.error) {
    return (
      <div className="space-y-4">
        <div className="flex items-start gap-3">
          <div className="w-7 h-7 rounded-full bg-bg-elevated border border-border-subtle flex items-center justify-center shrink-0 text-text-secondary">
            <MessageSquareText size={13} />
          </div>
          <div className="flex-1 panel-elevated px-4 py-3">
            <div className="text-sm font-medium">{turn.q}</div>
          </div>
        </div>
        <div className="flex items-start gap-3">
          <div className="w-7 h-7 rounded-full bg-red-500/20 flex items-center justify-center shrink-0">
            <Brain size={13} className="text-red-400" />
          </div>
          <div className="flex-1 panel p-5">
            <p className="text-sm text-red-300">{turn.error}</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-3">
        <div className="w-7 h-7 rounded-full bg-bg-elevated border border-border-subtle flex items-center justify-center shrink-0 text-text-secondary">
          <MessageSquareText size={13} />
        </div>
        <div className="flex-1 panel-elevated px-4 py-3">
          <div className="text-sm font-medium">{turn.q}</div>
        </div>
      </div>

      <div className="flex items-start gap-3">
        <div className="w-7 h-7 rounded-full bg-accent flex items-center justify-center shrink-0 shadow-[var(--glow-green)]">
          <Brain size={13} className="text-text-inverse" />
        </div>
        <div className="flex-1 panel p-5 space-y-4">
          <p className="text-base leading-relaxed">{explanation}</p>

          <AnswerVisualization
            chartType={chartType}
            data={chartData ?? raw}
          />

          <details className="text-xs">
            <summary className="cursor-pointer text-text-muted hover:text-text-secondary inline-flex items-center gap-1.5">
              <Code2 size={11} />
              View query
            </summary>
            <pre className="mt-2 font-mono text-[11px] text-text-secondary bg-bg-canvas border border-border-hairline rounded-md p-3 overflow-x-auto whitespace-pre-wrap">
              {cypher}
            </pre>
          </details>
        </div>
      </div>
    </div>
  );
}

function AnswerVisualization({
  chartType,
  data,
}: {
  chartType: string;
  data: unknown;
}) {
  if (
    data &&
    typeof data === "object" &&
    "labels" in data &&
    "values" in data
  ) {
    const cd = data as { labels: string[]; values: number[] };
    const items = cd.labels.map((label, i) => ({
      label,
      value: cd.values[i] ?? 0,
    }));
    const max = Math.max(...items.map((i) => i.value), 1);
    return (
      <div className="space-y-2 py-1">
        {items.map((it, idx) => (
          <div key={idx} className="space-y-1">
            <div className="flex justify-between text-xs">
              <span className="text-text-secondary">{it.label}</span>
              <span className="tabular text-text-primary font-medium">
                {it.value.toLocaleString()}
              </span>
            </div>
            <div className="h-1.5 rounded-full bg-bg-elevated overflow-hidden">
              <div
                className="h-full rounded-full bg-accent"
                style={{ width: `${(it.value / max) * 100}%` }}
              />
            </div>
          </div>
        ))}
      </div>
    );
  }

  if (chartType === "number") {
    const d = data as { value?: number; labels?: string[]; values?: number[] };
    // Handle single-value responses from the backend
    const val = d.value ?? (d.values?.[0]) ?? 0;
    return (
      <div className="flex items-baseline gap-3 py-2">
        <span className="text-6xl font-semibold tabular tracking-tight text-accent">
          {typeof val === "number" ? val.toLocaleString() : String(val)}
        </span>
      </div>
    );
  }

  if (chartType === "bar" || chartType === "rank") {
    const items = Array.isArray(data)
      ? (data as { label: string; value: number }[])
      : [];
    if (items.length === 0) return null;
    const max = Math.max(...items.map((i) => i.value));
    return (
      <div className="space-y-2 py-1">
        {items.map((it, idx) => (
          <div key={idx} className="space-y-1">
            <div className="flex justify-between text-xs">
              <span className="text-text-secondary">{it.label}</span>
              <span className="tabular text-text-primary font-medium">
                {it.value.toLocaleString()}
              </span>
            </div>
            <div className="h-1.5 rounded-full bg-bg-elevated overflow-hidden">
              <div
                className={cn(
                  "h-full rounded-full transition-all",
                  idx === 0
                    ? "bg-accent"
                    : "bg-border-strong"
                )}
                style={{ width: `${(it.value / max) * 100}%` }}
              />
            </div>
          </div>
        ))}
      </div>
    );
  }

  if (chartType === "line") {
    const d = data as { label: string; series: { t: string; v: number }[] };
    const max = Math.max(...d.series.map((p) => p.v));
    const step = 100 / (d.series.length - 1);
    const path = d.series
      .map(
        (p, i) => `${i === 0 ? "M" : "L"} ${i * step} ${100 - (p.v / max) * 90}`
      )
      .join(" ");
    return (
      <div>
        <div className="text-xs text-text-muted mb-2">{d.label}</div>
        <svg
          viewBox="0 0 100 100"
          preserveAspectRatio="none"
          className="w-full h-32"
        >
          <path d={`${path} L 100 100 L 0 100 Z`} fill="rgba(0,212,255,0.12)" />
          <path d={path} stroke="#00d4ff" strokeWidth="0.7" fill="none" />
          {d.series.map((p, i) => (
            <circle
              key={i}
              cx={i * step}
              cy={100 - (p.v / max) * 90}
              r="0.9"
              fill="#00d4ff"
            />
          ))}
        </svg>
        <div className="flex justify-between text-[10px] tabular text-text-muted mt-1">
          {d.series
            .filter((_, i) => i % 2 === 0)
            .map((p) => (
              <span key={p.t}>{p.t}</span>
            ))}
        </div>
      </div>
    );
  }

  return null;
}

// ── Empty state (non-demo, no data yet) ───────────────────────────────────

function AskEmptyState() {
  const active = useActiveSession();
  const sample = [
    active.zones[0]?.name &&
      `How many people entered the ${active.zones[0].name} today?`,
    active.zones[1]?.name &&
      `Which zone had the longest average dwell — ${active.zones[1].name} or ${active.zones[2]?.name ?? "any other"}?`,
    active.touchpoints[0]?.name &&
      `How many times was the ${active.touchpoints[0].name} interacted with?`,
    "Show me the busiest 5 minutes today.",
    "Which touchpoint converted the most visitors into captures?",
  ].filter(Boolean) as string[];

  return (
    <div className="p-5 max-w-[1400px] mx-auto space-y-8">
      <EmptyState
        variant="page"
        icon={<Brain size={26} strokeWidth={1.8} />}
        title="Ask the Room activates once there's data to ask."
        hint={
          <>
            Plain-English questions are run against the durable event bus
            using constrained SQL templates. Start the camera, let visitors
            interact, then come back here.
          </>
        }
        cta={{ href: "/live", label: "Open live & start recording" }}
      />

      {sample.length > 0 && (
        <div className="max-w-3xl mx-auto">
          <div className="eyebrow mb-3 text-center">
            Questions you&apos;ll be able to ask
          </div>
          <ul className="space-y-2">
            {sample.map((s, i) => (
              <li
                key={i}
                className="panel p-4 flex items-start gap-3 text-sm text-text-secondary"
              >
                <MessageSquareText
                  size={14}
                  className="text-accent shrink-0 mt-0.5"
                />
                <span>{s}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
