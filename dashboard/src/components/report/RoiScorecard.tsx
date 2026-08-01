"use client";

/**
 * realmspace — ROI 4-layer scorecard (UI).
 *
 * Renders the Reach → Engagement → Affinity → Pipeline framework from
 * docs/roi-framework.md. Data source order (honest, in line with AGENTS.md
 * "bus, not mocks"):
 *   1. The edge API's session outcome endpoint (durable bus, server-authoritative)
 *   2. The active session's local durable log (computeScorecard)
 *   3. A representative demo scorecard (empty log, pitch mode)
 * A "source" pill always states which one is showing.
 */

import { useMemo } from "react";
import { Activity, Gauge, HeartHandshake, TrendingUp } from "lucide-react";

import { Panel } from "@/components/ui/Panel";
import { Stat } from "@/components/ui/Stat";
import { Pill } from "@/components/ui/Pill";
import { readAll } from "@/lib/bus";
import { getTenantId } from "@/lib/tenant/context";
import { getEventContext } from "@/lib/event-context";
import { computeScorecard, type Scorecard } from "@/lib/roi/scorecard";
import { useSessionOutcome } from "@/hooks/useSessionOutcome";
import type { RealmEvent, ZoneNode } from "@/lib/contracts";

/** Demo scorecard so /report renders even before a real session has run. */
const DEMO: Scorecard = {
  reach: { uniqueVisitors: 1284, entries: 1416, passBy: 372, peakConcurrency: 47 },
  engagement: {
    avgDwellSec: 96,
    dwellWeightedAttention: 214500,
    engagementRate: 0.61,
    surfaceInteractions: 940,
    zoneParticipation: [
      { zoneId: "Mirror Room", visitors: 812, pct: 63.2 },
      { zoneId: "Bottle Wall", visitors: 640, pct: 49.8 },
      { zoneId: "Lounge", visitors: 402, pct: 31.3 },
    ],
  },
  affinity: { sentiment: 0.78, npsLift: 12, recallPct: 71 },
  pipeline: {
    leadsCaptured: 318,
    firstPartyCaptureRate: 0.41,
    costPerEngagedVisit: 3.06,
    costPerQualifiedLead: 37.7,
    pipelineMultiple: 4.2,
    roiRatio: 3.2,
  },
  benchmarkVerdict: "strong",
};

const verdictPill: Record<Scorecard["benchmarkVerdict"], { label: string; variant: "success" | "warn" | "info" | "neutral" }> = {
  exceptional: { label: "Exceptional · >5:1", variant: "success" },
  strong: { label: "Strong · 3–5:1", variant: "success" },
  below: { label: "Below benchmark · <3:1", variant: "warn" },
  unknown: { label: "Add cost + revenue to rate", variant: "neutral" },
};

const sourcePill = {
  remote: { label: "Live · edge API", variant: "success" as const },
  local: { label: "Local bus", variant: "info" as const },
  demo: { label: "Demo data", variant: "neutral" as const },
};

/** Zone weight heuristic used until session config carries explicit weights. */
function zonesFromContext(): ZoneNode[] {
  const ctx = getEventContext();
  return ctx.zones.map((z) => ({
    id: z.id,
    name: z.name,
    kind: "other",
    weight: z.type === "engagement" || z.type === "reveal" ? 3 : 1,
  }));
}

function fmt(n: number | null, digits = 0): string {
  if (n == null) return "—";
  return n.toLocaleString(undefined, { maximumFractionDigits: digits });
}

export function RoiScorecard() {
  const { outcome } = useSessionOutcome({
    zoneConfig: zonesFromContext(),
    activationCost: 12000,
    revenueInfluenced: 50400,
  });

  const localEvents = useMemo<RealmEvent[]>(() => {
    try {
      const ctx = getEventContext();
      return readAll(getTenantId(), ctx.sessionId) as RealmEvent[];
    } catch {
      return [];
    }
  }, []);

  const scorecard = useMemo<Scorecard>(() => {
    if (outcome) {
      return {
        reach: outcome.reach,
        engagement: {
          avgDwellSec: outcome.engagement.avgDwellSec,
          dwellWeightedAttention: outcome.engagement.dwellWeightedAttention,
          engagementRate: outcome.engagement.engagementRate,
          surfaceInteractions: outcome.engagement.surfaceInteractions,
          zoneParticipation: outcome.engagement.zoneParticipation,
        },
        affinity: outcome.affinity,
        pipeline: outcome.pipeline,
        benchmarkVerdict: outcome.benchmarkVerdict,
      };
    }
    if (!localEvents.length) return DEMO;
    return computeScorecard(localEvents, {
      zones: zonesFromContext(),
      engagedThresholdSec: 60,
      activationCost: 12000,
      revenueInfluenced: 50400,
    });
  }, [outcome, localEvents]);

  const source: "remote" | "local" | "demo" = outcome
    ? "remote"
    : localEvents.length
      ? "local"
      : "demo";

  const v = verdictPill[scorecard.benchmarkVerdict];
  const sp = sourcePill[source];
  const holdingTimeIndex = outcome?.engagement.holdingTimeIndex ?? null;
  const hygiene = outcome?.hygiene;

  return (
    <Panel
      title="ROI Scorecard · 4-layer framework"
      subtitle="Reach → Engagement → Affinity → Pipeline · industry-standard"
      action={
        <div className="flex items-center gap-2">
          <Pill variant={sp.variant}>{sp.label}</Pill>
          <Pill variant={v.variant}>{v.label}</Pill>
        </div>
      }
    >
      <div className="p-6 grid gap-6 md:grid-cols-2 xl:grid-cols-4">
        {/* Reach */}
        <LayerCard icon={<Activity size={14} />} name="Reach">
          <Stat label="Unique visitors" value={fmt(scorecard.reach.uniqueVisitors)} accent="white" size="md" />
          <MiniRow label="Entries" value={fmt(scorecard.reach.entries)} />
          <MiniRow label="Pass-by (skipped)" value={fmt(scorecard.reach.passBy)} />
          <MiniRow label="Peak concurrency" value={fmt(scorecard.reach.peakConcurrency)} />
        </LayerCard>

        {/* Engagement */}
        <LayerCard icon={<Gauge size={14} />} name="Engagement">
          <Stat
            label="Engagement rate"
            value={`${fmt(scorecard.engagement.engagementRate * 100, 1)}%`}
            accent="cyan"
            size="md"
          />
          <MiniRow label="Avg dwell" value={`${fmt(scorecard.engagement.avgDwellSec)}s`} />
          <MiniRow label="Dwell-weighted attn." value={fmt(scorecard.engagement.dwellWeightedAttention)} />
          <MiniRow label="Holding time index" value={holdingTimeIndex != null ? `${fmt(holdingTimeIndex, 2)}×` : "—"} />
        </LayerCard>

        {/* Affinity */}
        <LayerCard icon={<HeartHandshake size={14} />} name="Affinity">
          <Stat
            label="Sentiment"
            value={scorecard.affinity.sentiment != null ? `${fmt(scorecard.affinity.sentiment * 100)}%` : "—"}
            accent="violet"
            size="md"
          />
          <MiniRow label="NPS lift" value={scorecard.affinity.npsLift != null ? `+${fmt(scorecard.affinity.npsLift)}` : "—"} />
          <MiniRow label="Recall" value={scorecard.affinity.recallPct != null ? `${fmt(scorecard.affinity.recallPct)}%` : "—"} />
          <MiniRow label="Source" value="opt-in survey" />
        </LayerCard>

        {/* Pipeline */}
        <LayerCard icon={<TrendingUp size={14} />} name="Pipeline">
          <Stat
            label="ROI ratio"
            value={scorecard.pipeline.roiRatio != null ? `${fmt(scorecard.pipeline.roiRatio, 1)}:1` : "—"}
            accent="green"
            size="md"
          />
          <MiniRow label="Leads captured" value={fmt(scorecard.pipeline.leadsCaptured)} />
          <MiniRow label="Capture rate" value={`${fmt(scorecard.pipeline.firstPartyCaptureRate * 100, 1)}%`} />
          <MiniRow label="Cost / qualified lead" value={scorecard.pipeline.costPerQualifiedLead != null ? `$${fmt(scorecard.pipeline.costPerQualifiedLead, 2)}` : "—"} />
        </LayerCard>
      </div>

      {source === "remote" && hygiene && (
        <div className="px-6 pb-5 flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-border-subtle pt-4">
          <span className="eyebrow">Data quality</span>
          <span className="text-xs text-text-muted">
            {outcome?.source.eventsRead} events · {hygiene.dwellsIncluded} dwells counted
          </span>
          {hygiene.dwellsExcludedDropout > 0 && (
            <span className="text-xs text-text-muted">
              {hygiene.dwellsExcludedDropout} dropout dwell{hygiene.dwellsExcludedDropout > 1 ? "s" : ""} discounted
            </span>
          )}
          {hygiene.dwellsExcludedInsane > 0 && (
            <span className="text-xs text-text-muted">
              {hygiene.dwellsExcludedInsane} sensor-noise dwell{hygiene.dwellsExcludedInsane > 1 ? "s" : ""} dropped
            </span>
          )}
          {outcome?.engagement.holdingTimeByKind.length ? (
            <span className="text-xs text-text-muted">
              holding time vs. expected:{" "}
              {outcome.engagement.holdingTimeByKind
                .map((k) => `${k.kind} ${fmt(k.normalized, 2)}×`)
                .join(" · ")}
            </span>
          ) : null}
        </div>
      )}
    </Panel>
  );
}

function LayerCard({ icon, name, children }: { icon: React.ReactNode; name: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2 text-text-muted">
        <span className="opacity-70">{icon}</span>
        <span className="eyebrow">{name}</span>
      </div>
      <div className="flex flex-col gap-2">{children}</div>
    </div>
  );
}

function MiniRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-text-muted">{label}</span>
      <span className="tabular text-text-primary">{value}</span>
    </div>
  );
}
