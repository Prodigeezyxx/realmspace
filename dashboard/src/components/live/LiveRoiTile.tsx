"use client";

/**
 * realmspace — the four ROI layers, live.
 *
 * `roi-framework.md` §4 item 2: "the four layers as a live dashboard tile during
 * the activation, so operators optimise on day 2". The point is that an operator
 * does not have to wait for the report to find out the funnel is broken.
 *
 * It renders the *same* `Scorecard` the report does, from the same log. That is
 * the constraint that matters: if this tile could show a number the report would
 * not, an operator would spend a day optimising against a figure their client
 * never sees. So anything not yet computable reads "—" here exactly as it does
 * there, and for the same stated reason.
 */

import { Activity, Gauge, HeartHandshake, TrendingUp } from "lucide-react";

import { Panel } from "@/components/ui/Panel";
import { Pill } from "@/components/ui/Pill";
import type { LiveStats } from "@/lib/live/useLiveStats";
import type { Session } from "@/lib/session/types";

function fmt(n: number | null | undefined, digits = 0): string {
  if (n == null) return "—";
  return n.toLocaleString(undefined, { maximumFractionDigits: digits });
}

export function LiveRoiTile({
  stats,
  session,
}: {
  stats: LiveStats;
  session: Session;
}) {
  const { scorecard: card } = stats;
  const currency = session.measurement?.currency ?? "USD";
  const cost = scorecardCost(stats, currency);

  return (
    <Panel
      title="ROI so far · 4-layer framework"
      subtitle="Reach → Engagement → Affinity → Pipeline · from this session's log, updating live"
      action={
        <Pill variant={stats.hasData ? "success" : "neutral"}>
          {stats.hasData
            ? `${fmt(stats.eventCount)} events`
            : "waiting for the first event"}
        </Pill>
      }
    >
      {stats.hasData ? (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-5">
          <Layer icon={<Activity size={13} />} name="Reach">
            <Row label="Unique" value={fmt(card.reach.uniqueVisitors)} />
            <Row label="In room now" value={fmt(stats.peopleNow)} />
            <Row label="Footfall" value={fmt(card.reach.entries)} />
            <Row label="Passed by" value={fmt(card.reach.passBy)} />
          </Layer>

          <Layer icon={<Gauge size={13} />} name="Engagement">
            <Row
              label="Engaged"
              value={`${fmt(card.engagement.engagementRate * 100, 1)}%`}
            />
            <Row label="Avg dwell" value={`${fmt(card.engagement.avgDwellSec)}s`} />
            <Row
              label="Weighted attn."
              value={fmt(card.engagement.dwellWeightedAttention)}
            />
            <Row
              label="Touchpoints"
              value={fmt(card.engagement.surfaceInteractions)}
            />
          </Layer>

          <Layer icon={<HeartHandshake size={13} />} name="Affinity">
            <Row
              label="Sentiment"
              value={
                card.affinity.sentiment != null
                  ? `${fmt(card.affinity.sentiment * 100)}%`
                  : "—"
              }
              muted={card.affinity.sentiment == null}
            />
            <Row label="NPS lift" value="—" muted />
            <Row label="Recall" value="—" muted />
            <Row label="Needs" value="a survey" muted />
          </Layer>

          <Layer icon={<TrendingUp size={13} />} name="Pipeline">
            <Row label="Leads" value={fmt(card.pipeline.leadsCaptured)} />
            <Row
              label="Cost / engaged"
              value={cost}
              muted={card.pipeline.costPerEngagedVisit == null}
            />
            <Row
              label="ROI"
              value={
                card.pipeline.roiRatio != null
                  ? `${fmt(card.pipeline.roiRatio, 1)}:1`
                  : "—"
              }
              muted={card.pipeline.roiRatio == null}
            />
            <Row
              label="Capture rate"
              value={`${fmt(card.pipeline.firstPartyCaptureRate * 100, 1)}%`}
            />
          </Layer>
        </div>
      ) : (
        <p className="text-sm text-text-secondary leading-relaxed">
          Nothing has arrived for this session yet. Start the camera, or point
          perception at the bus, and these fill in as events land — this is the
          same computation the report runs, not a preview of it.
        </p>
      )}
    </Panel>
  );
}

function scorecardCost(stats: LiveStats, currency: string): string {
  const value = stats.scorecard.pipeline.costPerEngagedVisit;
  if (value == null) return "—";
  try {
    return value.toLocaleString(undefined, {
      style: "currency",
      currency,
      maximumFractionDigits: 2,
    });
  } catch {
    return `${fmt(value, 2)} ${currency}`;
  }
}

function Layer({
  icon,
  name,
  children,
}: {
  icon: React.ReactNode;
  name: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2 text-text-muted">
        <span className="opacity-70">{icon}</span>
        <span className="eyebrow">{name}</span>
      </div>
      <div className="flex flex-col gap-1.5">{children}</div>
    </div>
  );
}

/** `muted` marks a figure that is absent rather than zero — same rule as the report. */
function Row({
  label,
  value,
  muted,
}: {
  label: string;
  value: string;
  muted?: boolean;
}) {
  return (
    <div className="flex items-center justify-between text-sm gap-2">
      <span className="text-text-muted">{label}</span>
      <span className={muted ? "tabular text-text-muted" : "tabular text-text-primary"}>
        {value}
      </span>
    </div>
  );
}
