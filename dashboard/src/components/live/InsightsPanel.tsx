"use client";

/**
 * What the insight agent has said about this room, and what it said it from.
 *
 * `floats-agent`'s spec, which this track adopted verbatim: *"shown on `/live`;
 * click-through opens the underlying events"*, with the acceptance clause
 * *"insights on `/live` trace to source events"*. The click-through is the
 * point, not a nicety — an insight nobody can check reads as authority, which is
 * exactly what the three hardcoded strings this replaces did:
 *
 *   "Visitors who try the Scent Quiz dwell 2.4× longer in the Lounge."
 *   "Bottle Wall captures 86% of gazes for visitors within 1m."
 *   "Entry Arch is dropping 38% of visitors within 30s — queue signage unclear."
 *
 * None of it was measured, and the last one asserted a *cause*. They were shown
 * in demo mode under a subtitle promising "a fresh round every 10 minutes" from
 * an agent that did not exist.
 */

import { ChevronRight, Sparkles } from "lucide-react";
import { useState } from "react";

import { EmptyState } from "@/components/ui/EmptyState";
import { Panel } from "@/components/ui/Panel";
import { busEmail, busUrl, ensureToken, isRemoteBusEnabled } from "@/lib/bus";
import type { LiveInsight } from "@/lib/live/derive";

interface Source {
  seq: number;
  eventId?: string;
  type?: string;
  occurredAt?: string;
  payload?: Record<string, unknown>;
  missing: boolean;
}

export function InsightsPanel({ insights }: { insights: LiveInsight[] }) {
  return (
    <Panel
      title="Insights"
      subtitle={
        insights.length > 0
          ? `Every ${insights[0].windowMinutes} minutes, from the event log`
          : "Generated from the event log once a window has closed"
      }
      action={<Sparkles size={14} className="text-accent-violet" />}
    >
      {insights.length === 0 ? (
        <EmptyState
          icon={<Sparkles size={18} />}
          title="No insights yet."
          hint="The first one is written when the session's first window closes. A window with nothing in it is not summarised."
        />
      ) : (
        <ul className="space-y-3 text-sm">
          {insights.map((insight) => (
            <InsightRow key={insight.seq} insight={insight} />
          ))}
        </ul>
      )}
    </Panel>
  );
}

function InsightRow({ insight }: { insight: LiveInsight }) {
  const [open, setOpen] = useState(false);
  const [sources, setSources] = useState<Source[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (!next || sources) return;
    const loaded = await fetchSources(insight.seq);
    if (typeof loaded === "string") setError(loaded);
    else setSources(loaded);
  }

  return (
    <li className="flex gap-3">
      <span className="mt-1.5 h-1.5 w-1.5 rounded-full shrink-0 bg-accent-violet" />
      <div className="flex-1 min-w-0">
        <div className="text-text-primary leading-snug">{insight.text}</div>

        <div className="flex items-center gap-2 mt-1 text-[10px] tabular text-text-muted">
          <span>{new Date(insight.at).toLocaleTimeString()}</span>
          <span>·</span>
          {/*
            The basis, always — the same rule /ask follows. With no AI provider
            configured the sentence is composed from the measurements, and every
            number in it is still measured.
          */}
          <span>
            {insight.basis === "deterministic"
              ? "composed without a model"
              : `written by ${insight.basis}`}
          </span>
          {insight.truncated && (
            <>
              <span>·</span>
              <span className="text-accent-amber">
                window outran the digest — partial
              </span>
            </>
          )}
        </div>

        {insight.refs.length > 0 && (
          <button
            onClick={() => void toggle()}
            className="mt-1.5 inline-flex items-center gap-1 text-[11px] text-text-secondary hover:text-text-primary transition-colors"
          >
            <ChevronRight
              size={11}
              className={`transition-transform ${open ? "rotate-90" : ""}`}
            />
            {insight.refs.length} source{insight.refs.length === 1 ? "" : "s"}
          </button>
        )}

        {open && (
          <div className="mt-2 space-y-1 border-l border-border-hairline pl-3">
            {error && <div className="text-[11px] text-text-muted">{error}</div>}
            {!error && sources === null && (
              <div className="text-[11px] text-text-muted animate-pulse">
                Opening the events…
              </div>
            )}
            {sources?.map((source) => (
              <div key={source.seq} className="text-[11px] tabular">
                {source.missing ? (
                  // Reported rather than dropped: an insight quietly showing
                  // four of its five sources is a weaker claim presented as the
                  // original one.
                  <span className="text-accent-amber">
                    seq {source.seq} — no longer on the log
                  </span>
                ) : (
                  <span className="text-text-secondary">
                    <span className="font-mono text-text-muted">
                      #{source.seq} {source.type}
                    </span>{" "}
                    {describe(source.payload)}
                  </span>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </li>
  );
}

function describe(payload: Record<string, unknown> | undefined): string {
  if (!payload) return "";
  const who = payload.anon_id ? String(payload.anon_id) : "";
  const where =
    (payload.zone_name as string) ??
    (payload.surface_label as string) ??
    (payload.zone_id as string) ??
    "";
  const duration =
    typeof payload.duration === "number"
      ? ` · ${Math.round(payload.duration)}s`
      : "";
  return [who, where].filter(Boolean).join(" · ") + duration;
}

async function fetchSources(seq: number): Promise<Source[] | string> {
  if (!isRemoteBusEnabled()) {
    return "The sources live on the durable log, which only the backend has.";
  }
  const token = await ensureToken(busEmail());
  if (!token) return "Could not authenticate with the bus.";
  try {
    const res = await fetch(`${busUrl()}/v1/insights/${seq}/sources`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) return `The bus returned ${res.status}.`;
    const body = (await res.json()) as { sources: Source[] };
    return body.sources ?? [];
  } catch {
    return "The bus is unreachable.";
  }
}
