"use client";

/**
 * realmspace — ROI 4-layer scorecard (UI).
 *
 * Renders Reach → Engagement → Affinity → Pipeline from docs/roi-framework.md,
 * computed by `@/lib/roi/scorecard` over the session's real event log.
 *
 * ## What this component used to do, and why it had to stop
 *
 * It held a `DEMO` scorecard and returned it whenever the log was empty **or
 * anything at all threw**, and on a real session it still passed
 * `activationCost: 12000, revenueInfluenced: 50400` — invented figures, so the
 * headline "3.2:1" was invented too, regardless of what the cameras saw. A
 * client reading that page could not have told which numbers were theirs.
 *
 * `roi-framework.md` §3 is unambiguous about why that is not a shortcut but a
 * defect: "We never inflate. Over-claiming kills the trust that is our moat."
 *
 * So: no fallbacks, no defaults that masquerade as findings. Every number comes
 * from the event log or the session's configured parameters. Anything that
 * cannot be computed says so, and says what is missing — because "we don't know
 * yet" and "the answer is zero" are different sentences and a report that
 * renders them identically is lying by omission.
 */

import { Activity, Gauge, HeartHandshake, Info, TrendingUp } from "lucide-react";

import { Panel } from "@/components/ui/Panel";
import { Stat } from "@/components/ui/Stat";
import { Pill } from "@/components/ui/Pill";
import type { Scorecard } from "@/lib/roi/scorecard";
import type { SessionReport } from "@/lib/report/useSessionReport";

const verdictPill: Record<
  Scorecard["benchmarkVerdict"],
  { label: string; variant: "success" | "warn" | "info" | "neutral" }
> = {
  exceptional: { label: "Exceptional · >5:1", variant: "success" },
  strong: { label: "Strong · 3–5:1", variant: "success" },
  below: { label: "Below benchmark · <3:1", variant: "warn" },
  unknown: { label: "Not enough to rate", variant: "neutral" },
};

function fmt(n: number | null | undefined, digits = 0): string {
  if (n == null) return "—";
  return n.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function money(n: number | null, currency: string, digits = 2): string {
  if (n == null) return "—";
  try {
    return n.toLocaleString(undefined, {
      style: "currency",
      currency,
      maximumFractionDigits: digits,
    });
  } catch {
    // An unrecognised currency code must not take the report down with it.
    return `${fmt(n, digits)} ${currency}`;
  }
}

export function RoiScorecard({ report }: { report: SessionReport }) {
  const { scorecard, config } = report;
  const v = verdictPill[scorecard.benchmarkVerdict];
  const currency = config?.currency ?? "USD";

  return (
    <Panel
      title="ROI Scorecard · 4-layer framework"
      subtitle="Reach → Engagement → Affinity → Pipeline · computed from this session's event log"
      action={<Pill variant={v.variant}>{v.label}</Pill>}
    >
      <div className="p-6 grid gap-6 md:grid-cols-2 xl:grid-cols-4">
        <LayerCard icon={<Activity size={14} />} name="Reach">
          <Stat
            label="Unique visitors"
            value={fmt(scorecard.reach.uniqueVisitors)}
            accent="white"
            size="md"
          />
          <MiniRow
            label="Footfall (entries)"
            value={fmt(scorecard.reach.entries)}
            missing={
              scorecard.reach.entries == null
                ? "No zone is marked as the entry, so entries cannot be counted."
                : undefined
            }
          />
          {/* No "missing" marker: pass-by is measured now, so a zero here is a
              finding — nobody came close to a zone and declined it — rather
              than an absent signal. Dimming it would say the opposite. */}
          <MiniRow
            label="Pass-by (skipped)"
            value={fmt(scorecard.reach.passBy)}
          />
          <MiniRow
            label="Peak in zones"
            value={fmt(scorecard.reach.peakZoneConcurrency)}
          />
        </LayerCard>

        <LayerCard icon={<Gauge size={14} />} name="Engagement">
          <Stat
            label="Engagement rate"
            value={`${fmt(scorecard.engagement.engagementRate * 100, 1)}%`}
            accent="cyan"
            size="md"
          />
          <MiniRow
            label={`Avg dwell (engaged > ${fmt(
              config?.engagedThresholdSeconds ?? 60
            )}s)`}
            value={`${fmt(scorecard.engagement.avgDwellSec)}s`}
          />
          <MiniRow
            label="Dwell-weighted attn."
            value={fmt(scorecard.engagement.dwellWeightedAttention)}
          />
          <MiniRow
            label="Surface interactions"
            value={fmt(scorecard.engagement.surfaceInteractions)}
            missing={
              scorecard.engagement.surfaceInteractions === 0
                ? "No touchpoint is reporting interactions yet."
                : undefined
            }
          />
        </LayerCard>

        <LayerCard icon={<HeartHandshake size={14} />} name="Affinity">
          <Stat
            label="Sentiment"
            value={
              scorecard.affinity.sentiment != null
                ? `${fmt(scorecard.affinity.sentiment * 100)}%`
                : "—"
            }
            accent="violet"
            size="md"
          />
          <MiniRow
            label="NPS lift"
            value={
              scorecard.affinity.npsLift != null
                ? `+${fmt(scorecard.affinity.npsLift)}`
                : "—"
            }
          />
          <MiniRow
            label="Recall"
            value={
              scorecard.affinity.recallPct != null
                ? `${fmt(scorecard.affinity.recallPct)}%`
                : "—"
            }
          />
          <MiniRow
            label="Source"
            value="opt-in survey"
            missing="Affinity needs a survey. Nothing here is inferred from behaviour."
          />
        </LayerCard>

        <LayerCard icon={<TrendingUp size={14} />} name="Pipeline">
          <Stat
            label="ROI ratio"
            value={
              scorecard.pipeline.roiRatio != null
                ? `${fmt(scorecard.pipeline.roiRatio, 1)}:1`
                : "—"
            }
            accent="green"
            size="md"
          />
          <MiniRow
            label="Cost / engaged visit"
            value={money(scorecard.pipeline.costPerEngagedVisit, currency)}
            missing={
              config?.activationCost == null
                ? "Set the activation cost on the session to compute this."
                : undefined
            }
          />
          <MiniRow
            label="Leads captured"
            value={fmt(scorecard.pipeline.leadsCaptured)}
          />
          <MiniRow
            label="Cost / qualified lead"
            value={money(scorecard.pipeline.costPerQualifiedLead, currency)}
          />
        </LayerCard>
      </div>

      {(config?.revenueInfluenced != null || config?.qualifiedLeads != null) && (
        <div className="px-6 pb-5 -mt-1">
          <p className="text-xs text-text-muted flex items-start gap-2 leading-relaxed">
            <Info size={13} className="mt-0.5 shrink-0" />
            <span>
              Influenced revenue{" "}
              <span className="tabular text-text-secondary">
                {money(config.revenueInfluenced, currency, 0)}
              </span>{" "}
              and qualified leads{" "}
              <span className="tabular text-text-secondary">
                {fmt(config.qualifiedLeads)}
              </span>{" "}
              were <strong>supplied by the client</strong>, not measured by
              realmspace. Everything else on this card is computed from the event
              log. CRM-attributed revenue arrives in a later phase.
            </span>
          </p>
        </div>
      )}

      {scorecard.pipeline.roiRatio == null && (
        <div className="px-6 pb-5 -mt-1">
          <p className="text-xs text-text-muted flex items-start gap-2 leading-relaxed">
            <Info size={13} className="mt-0.5 shrink-0" />
            <span>
              No ROI ratio yet:{" "}
              {config?.activationCost == null
                ? "the activation cost has not been set"
                : "influenced revenue has not been provided"}
              . The ratio is deliberately blank rather than estimated.
            </span>
          </p>
        </div>
      )}
    </Panel>
  );
}

function LayerCard({
  icon,
  name,
  children,
}: {
  icon: React.ReactNode;
  name: string;
  children: React.ReactNode;
}) {
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

/**
 * `missing` marks a figure that is absent or not-yet-measurable rather than
 * genuinely zero. It dims the value and explains on hover, so a reader is never
 * left to assume a blank means nothing happened.
 */
function MiniRow({
  label,
  value,
  missing,
}: {
  label: string;
  value: string;
  missing?: string;
}) {
  return (
    <div className="flex items-center justify-between text-sm gap-3">
      <span className="text-text-muted">{label}</span>
      <span
        className={missing ? "tabular text-text-muted" : "tabular text-text-primary"}
        title={missing}
      >
        {value}
        {missing && <span className="ml-1 opacity-60">*</span>}
      </span>
    </div>
  );
}
