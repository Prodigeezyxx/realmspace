"use client";

/**
 * realmspace — live ROI tile (day-2 optimisation surface).
 *
 * A compact, always-refreshing view of the four-layer scorecard for the
 * active session, straight from the edge API. The operator keeps this on
 * screen while the room runs; the ROI ratio is the number the day's
 * decisions chase.
 */

import { Sparkles, TrendingUp } from "lucide-react";

import { Panel } from "@/components/ui/Panel";
import { Pill } from "@/components/ui/Pill";
import { getEventContext } from "@/lib/event-context";
import { useSessionOutcome } from "@/hooks/useSessionOutcome";
import { formatDuration } from "@/lib/utils";

function zonesFromContext() {
  return getEventContext().zones.map((z) => ({
    id: z.id,
    kind: z.type,
    weight: z.type === "engagement" || z.type === "reveal" ? 3 : 1,
  }));
}

function verdictLabel(verdict: string): { label: string; variant: "success" | "warn" | "neutral" } {
  switch (verdict) {
    case "exceptional":
      return { label: ">5:1", variant: "success" };
    case "strong":
      return { label: "3–5:1", variant: "success" };
    case "below":
      return { label: "<3:1", variant: "warn" };
    default:
      return { label: "pending", variant: "neutral" };
  }
}

export function LiveRoiTile() {
  const { outcome, state } = useSessionOutcome({
    zoneConfig: zonesFromContext(),
    activationCost: 12000,
    revenueInfluenced: 50400,
    pollMs: 15000,
  });

  if (state === "offline") {
    return (
      <Panel title="Live ROI" subtitle="Scorecard · 4-layer framework">
        <div className="px-6 py-8 text-center text-sm text-text-muted">
          The ROI tile needs the edge API. Start the backend with
          NEXT_PUBLIC_BUS_URL set.
        </div>
      </Panel>
    );
  }

  if (state === "empty" || !outcome) {
    return (
      <Panel title="Live ROI" subtitle="Scorecard · 4-layer framework">
        <div className="px-6 py-8 text-center text-sm text-text-muted">
          The scorecard fills in as events stream onto the bus — first dwells
          appear within a minute of a live session.
        </div>
      </Panel>
    );
  }

  const roi = outcome.pipeline.roiRatio;
  const v = verdictLabel(outcome.benchmarkVerdict);

  return (
    <Panel
      title="Live ROI"
      subtitle="Scorecard · refreshing from the edge API"
      action={<Pill variant={v.variant}>{v.label}</Pill>}
    >
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="text-[10px] uppercase tracking-[0.18em] text-text-muted">
            ROI per dollar
          </div>
          <div className="text-4xl font-semibold tabular tracking-tight text-accent mt-1">
            {roi != null ? `${roi.toFixed(1)}×` : "—"}
          </div>
          <div className="text-xs text-text-muted mt-1 tabular">
            {roi != null
              ? "vs 3:1–5:1 industry benchmark"
              : "add activation cost + revenue"}
          </div>
        </div>
        <div className="flex flex-col gap-1.5 text-right">
          <Metric label="Engaged" value={`${Math.round(outcome.engagement.engagementRate * 100)}%`} />
          <Metric label="Avg dwell" value={formatDuration(outcome.engagement.avgDwellSec)} />
          <Metric
            label="Holding idx"
            value={outcome.engagement.holdingTimeIndex != null ? `${outcome.engagement.holdingTimeIndex.toFixed(2)}×` : "—"}
          />
        </div>
      </div>

      <div className="mt-4 pt-3 border-t border-border-hairline flex items-center justify-between text-[11px] tabular text-text-muted">
        <span className="flex items-center gap-1.5">
          <TrendingUp size={12} />
          {outcome.reach.uniqueVisitors} visitors · {outcome.source.eventsRead} events
        </span>
        <span className="flex items-center gap-1.5">
          <Sparkles size={12} className="text-accent-violet" />
          refreshes 15s
        </span>
      </div>
    </Panel>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-end gap-2">
      <span className="text-[10px] uppercase tracking-[0.14em] text-text-muted">
        {label}
      </span>
      <span className="text-sm font-medium tabular text-text-primary">{value}</span>
    </div>
  );
}
