"use client";

/**
 * realmspace — ROI 4-layer scorecard (UI).
 *
 * Renders the Reach → Engagement → Affinity → Pipeline framework from
 * docs/roi-framework.md, computed by @/lib/roi/scorecard. Reads the active
 * session's durable event log; falls back to a representative demo scorecard
 * when the log is empty so the report always renders for the pitch.
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

function fmt(n: number | null, digits = 0): string {
  if (n == null) return "—";
  return n.toLocaleString(undefined, { maximumFractionDigits: digits });
}

export function RoiScorecard() {
  const scorecard = useMemo<Scorecard>(() => {
    try {
      const ctx = getEventContext();
      const events = readAll(getTenantId(), ctx.sessionId) as RealmEvent[];
      if (!events.length) return DEMO;
      const zones: ZoneNode[] = ctx.zones.map((z) => ({
        id: z.id,
        name: z.name,
        kind: "other",
        weight: z.type === "engagement" || z.type === "reveal" ? 3 : 1,
      }));
      return computeScorecard(events, {
        zones,
        engagedThresholdSec: 60,
        // economics come from session goals later; demo values keep it live
        activationCost: 12000,
        revenueInfluenced: 50400,
      });
    } catch {
      return DEMO;
    }
  }, []);

  const v = verdictPill[scorecard.benchmarkVerdict];

  return (
    <Panel
      title="ROI Scorecard · 4-layer framework"
      subtitle="Reach → Engagement → Affinity → Pipeline · industry-standard"
      action={<Pill variant={v.variant}>{v.label}</Pill>}
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
          <MiniRow label="Surface interactions" value={fmt(scorecard.engagement.surfaceInteractions)} />
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
