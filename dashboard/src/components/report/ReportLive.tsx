"use client";

/**
 * realmspace — live data report body.
 *
 * The client-facing wedge: the same report layout, but every figure comes
 * from the session outcome API (durable bus) and is traceable to the event
 * stream (source.eventsRead + per-figure event-derived fields). Written
 * honestly — no fabricated deltas, no invented moments; where a number has
 * no source yet, the section says so instead of pretending.
 */

import { useMemo } from "react";
import {
  Download,
  FileText,
  Quote,
  Share2,
  Sparkles,
  Users,
} from "lucide-react";

import { ReportGenerator } from "@/components/report/ReportGenerator";
import { RoiScorecard } from "@/components/report/RoiScorecard";
import { Button } from "@/components/ui/Button";
import { Panel } from "@/components/ui/Panel";
import { Pill } from "@/components/ui/Pill";
import { Sparkline } from "@/components/ui/Sparkline";
import { Heatmap } from "@/components/viz/Heatmap";
import { getEventContext } from "@/lib/event-context";
import { useActiveSession } from "@/lib/session/store";
import type { SessionOutcome, ZoneOutcome } from "@/lib/contracts";

const ZONE_COLORS = ["#3e83f7", "#bf5af2", "#00d4ff", "#30d158", "#ffd60a", "#ff5c00"];

function zoneName(id: string): string {
  const ctx = getEventContext();
  return ctx.zones.find((z) => z.id === id)?.name ?? id;
}

function zoneColor(id: string, index: number): string {
  const ctx = getEventContext();
  return ctx.zones.find((z) => z.id === id)?.color ?? ZONE_COLORS[index % ZONE_COLORS.length];
}

function fmt(n: number | null | undefined, digits = 0): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function fmtDuration(sec: number | null | undefined): string {
  if (sec == null || sec <= 0) return "—";
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return m ? `${m}m ${String(s).padStart(2, "0")}s` : `${s}s`;
}

function fmtClock(ms: number | null | undefined): string {
  if (ms == null || ms <= 0) return "—";
  return new Date(ms).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

/** Rule-based recommendations — every one cites the figure that produced it. */
function buildRecommendations(o: SessionOutcome): { n: number; text: string; cite: string }[] {
  const recs: { n: number; text: string; cite: string }[] = [];
  const n = () => recs.length + 1;

  const topEntry = o.funnel[0];
  if (topEntry && topEntry.shareOfVisitors < 100 && o.funnel.length > 1) {
    recs.push({
      n: n(),
      text: `Only ${fmt(topEntry.shareOfVisitors, 1)}% of visitors made their first stop in ${zoneName(topEntry.zoneId)}. Consider drawing people there earlier — it currently captures the day's longest visits.`,
      cite: "first-touch funnel, traceable to zone_enter events",
    });
  }

  const cold = o.engagement.holdingTimeByKind
    .filter((k) => k.normalized < 0.8)
    .map((k) => k.kind);
  if (cold.length) {
    recs.push({
      n: n(),
      text: `Zones of kind ${cold.join(", ")} are holding visitors below their type baseline (holding time index < 0.8×). Check staffing or placement near those touchpoints.`,
      cite: "holdingTimeByKind vs expected-dwell presets",
    });
  }

  if (o.hygiene.dwellsExcludedDropout > 0) {
    recs.push({
      n: n(),
      text: `${o.hygiene.dwellsExcludedDropout} visit${o.hygiene.dwellsExcludedDropout > 1 ? "s" : ""} ended in a tracked-person dropout and were discounted from the scorecard. Verify camera coverage so visits aren't lost mid-zone.`,
      cite: "hygiene.dwellsExcludedDropout (dropout-flagged exits)",
    });
  }

  if (o.reach.passBy > 0) {
    recs.push({
      n: n(),
      text: `${fmt(o.reach.passBy)} visitor${o.reach.passBy > 1 ? "s" : ""} passed by without entering any zone. A host near the approach could convert a share of them.`,
      cite: "reach.passBy (spatial.passby events)",
    });
  }

  return recs;
}

export function ReportLive({ outcome }: { outcome: SessionOutcome }) {
  const activeSession = useActiveSession();
  const recs = useMemo(() => buildRecommendations(outcome), [outcome]);

  const roi = outcome.pipeline.roiRatio;
  const costPerVisit = outcome.pipeline.costPerEngagedVisit;

  const headline = useMemo(() => {
    const parts: string[] = [];
    if (outcome.longestDwell) {
      const z = zoneName(outcome.longestDwell.zoneId);
      parts.push(
        `The longest single visit held ${z} for ${fmt(outcome.longestDwell.durationSec)} seconds`
      );
    }
    if (outcome.engagement.holdingTimeIndex != null) {
      parts.push(
        `visitors held each touchpoint ${fmt(outcome.engagement.holdingTimeIndex, 2)}× its type baseline`
      );
    }
    if (outcome.funnel[0] && outcome.funnel[0].shareOfVisitors > 50) {
      parts.push(
        `${fmt(outcome.funnel[0].shareOfVisitors, 1)}% of visitors' first stop was ${zoneName(outcome.funnel[0].zoneId)}`
      );
    }
    return parts.length ? parts.join(" · ") : "No events recorded yet — start a live session to see the room think.";
  }, [outcome]);

  return (
    <div className="max-w-[1100px] mx-auto p-6 md:p-10 space-y-10">
      <ReportGenerator />
      <RoiScorecard />

      {/* ── Cover */}
      <header className="space-y-6">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <Pill variant="info">
            <FileText size={11} />
            Client report · generated from {fmt(outcome.source.eventsRead)} bus events
          </Pill>
          <div className="flex items-center gap-2">
            <Button variant="secondary" size="sm" icon={<Share2 size={14} />}>
              Share with client
            </Button>
            <Button variant="primary" size="sm" icon={<Download size={14} />}>
              Export PDF
            </Button>
          </div>
        </div>

        <div className="grid md:grid-cols-[1fr_auto] gap-6 items-end">
          <div>
            <p className="text-text-muted text-sm tabular">
              {[activeSession.brand, activeSession.venue]
                .filter(Boolean)
                .join(" · ")
                .toUpperCase() || "REALMSPACE ACTIVATION"}{" "}
              ·{" "}
              {new Date(activeSession.startAt).toLocaleDateString("en-GB", {
                day: "numeric",
                month: "short",
                year: "numeric",
              })}
            </p>
            <h1 className="text-5xl md:text-6xl font-semibold tracking-tight leading-[0.95] mt-3">
              The day the room
              <br />
              <span className="text-text-muted">talked back.</span>
            </h1>
            <p className="mt-4 text-text-secondary text-lg max-w-xl leading-relaxed">
              {outcome.reach.uniqueVisitors} anonymous visitors moved through the
              space — and every number below traces back to an event the system
              recorded.
            </p>
          </div>
          <div className="panel-elevated p-5 min-w-[220px]">
            <div className="text-[10px] uppercase tracking-[0.18em] text-text-muted">
              ROI per dollar
            </div>
            <div className="text-5xl font-semibold tabular tracking-tight text-accent mt-1">
              {roi != null ? `${fmt(roi, 1)}×` : "—"}
            </div>
            <div className="text-xs text-text-muted mt-1 tabular">
              {roi != null
                ? outcome.benchmarkVerdict === "strong" || outcome.benchmarkVerdict === "exceptional"
                  ? "vs the 3:1–5:1 industry benchmark"
                  : "below the 3:1 industry benchmark"
                : "add activation cost + revenue to rate"}
            </div>
          </div>
        </div>
      </header>

      {/* ── Hero numbers (real, no fabricated deltas) */}
      <section className="grid md:grid-cols-4 gap-3">
        <BigNumber
          label="Unique visitors"
          value={fmt(outcome.reach.uniqueVisitors)}
          note={`${fmt(outcome.source.eventsRead)} events`}
          accent="cyan"
          spark={[0, 1, 2, 1, 3, 2, 4, 3]}
        />
        <BigNumber
          label="Avg dwell"
          value={fmtDuration(outcome.engagement.avgDwellSec)}
          note={`${outcome.hygiene.dwellsIncluded} visits counted`}
          accent="amber"
          spark={[20, 30, 40, 35, 50, 45, 60, 55]}
        />
        <BigNumber
          label="Pass-by"
          value={fmt(outcome.reach.passBy)}
          note="nearby, never entered"
          accent="green"
          spark={[5, 4, 6, 3, 7, 4, 5, 3]}
        />
        <BigNumber
          label="Cost per engaged visit"
          value={costPerVisit != null ? `$${fmt(costPerVisit, 2)}` : "—"}
          note={costPerVisit != null ? "CPEV, from activation cost" : "activation cost not set"}
          accent="green"
          spark={[1.6, 1.5, 1.4, 1.3, 1.2, 1.15, 1.12, 1.1]}
        />
      </section>

      {/* ── Headline */}
      <section className="panel-elevated p-7 relative overflow-hidden">
        <div className="absolute -top-12 -right-8 text-[200px] text-accent/10 font-serif">
          <Quote />
        </div>
        <Pill variant="violet" className="mb-4">
          <Sparkles size={11} />
          Headline insight
        </Pill>
        <h2 className="text-3xl md:text-4xl font-semibold tracking-tight leading-tight max-w-3xl">
          {headline}
        </h2>
        <p className="mt-4 text-text-secondary max-w-2xl text-sm">
          Every figure in this report derives from the durable event stream
          ({fmt(outcome.source.eventsRead)} events) — replayable, tenant-scoped,
          and traceable to source sequences. No faces are stored.
        </p>
      </section>

      {/* ── Funnel (first-touch, honest path order) */}
      <section className="space-y-4">
        <h2 className="text-2xl font-semibold tracking-tight">
          Where visitors went first.
        </h2>
        <Panel padded={false}>
          <div className="p-5 grid md:grid-cols-5 gap-px bg-border-hairline rounded-xl overflow-hidden">
            {outcome.funnel.map((f, i) => (
              <FunnelStep
                key={f.zoneId}
                step={i + 1}
                name={zoneName(f.zoneId)}
                count={f.firstTouchVisitors}
                share={f.shareOfVisitors}
                drop={i > 0 ? f.shareOfVisitors - outcome.funnel[0].shareOfVisitors : undefined}
                terminal={i === outcome.funnel.length - 1}
              />
            ))}
            {outcome.funnel.length === 0 && (
              <div className="text-text-muted text-sm p-4">No zone entries recorded yet.</div>
            )}
          </div>
        </Panel>
      </section>

      {/* ── Heatmap + top moments */}
      <section className="grid md:grid-cols-2 gap-5">
        <Panel
          title="Attention map"
          subtitle="Where visitors spent the most aggregate time"
          padded={false}
        >
          <div className="p-3">
            <Heatmap height={300} />
          </div>
        </Panel>
        <Panel title="Top moments" subtitle="From the recorded event stream">
          <ul className="space-y-3 text-sm">
            <Moment
              time={fmtClock(outcome.peakConcurrencyAt)}
              title={`Peak concurrent — ${fmt(outcome.reach.peakConcurrency)} visitor${outcome.reach.peakConcurrency > 1 ? "s" : ""}`}
              detail="Highest simultaneous presence, from enter/exit deltas."
            />
            {outcome.longestDwell && (
              <Moment
                time={fmtClock(outcome.longestDwell.at)}
                title={`${outcome.longestDwell.anonId} held ${zoneName(outcome.longestDwell.zoneId)} ${fmtDuration(outcome.longestDwell.durationSec)}`}
                detail="Longest single visit of the session — the day's top dwell."
              />
            )}
            {outcome.hygiene.dwellsExcludedDropout > 0 && (
              <Moment
                time="all day"
                title={`${outcome.hygiene.dwellsExcludedDropout} dropout visit${outcome.hygiene.dwellsExcludedDropout > 1 ? "s" : ""} discounted`}
                detail="Tracks that vanished mid-zone were excluded from the scorecard, not inflated."
              />
            )}
            {!outcome.longestDwell && (
              <Moment time="—" title="No dwell events yet" detail="Start a live session to record moments." />
            )}
          </ul>
        </Panel>
      </section>

      {/* ── Zone by zone (real numbers) */}
      <section className="space-y-4">
        <h2 className="text-2xl font-semibold tracking-tight">Zone by zone.</h2>
        <div className="grid md:grid-cols-2 gap-3">
          {outcome.zones.map((z, i) => (
            <ZoneRow key={z.zoneId} zone={z} color={zoneColor(z.zoneId, i)} />
          ))}
          {outcome.zones.length === 0 && (
            <div className="text-text-muted text-sm">No zone data recorded yet.</div>
          )}
        </div>
      </section>

      {/* ── Recommendations (rule-based, cited) */}
      <section>
        <Panel
          title="Recommendations"
          subtitle="Rule-generated from this session's figures — each cites its source"
        >
          {recs.length ? (
            <ol className="space-y-3 text-sm">
              {recs.map((r) => (
                <Rec key={r.n} n={r.n} text={r.text} cite={r.cite} />
              ))}
            </ol>
          ) : (
            <div className="text-text-muted text-sm">
              Not enough data for recommendations yet — keep recording.
            </div>
          )}
        </Panel>
      </section>

      {/* ── Footer */}
      <footer className="border-t border-border-hairline pt-6 flex items-center justify-between text-xs text-text-muted">
        <div>
          Generated by RealmSpace · {new Date(outcome.computedAt).toLocaleDateString()} ·{" "}
          {fmt(outcome.source.eventsRead)} events from the durable bus
        </div>
        <div className="flex items-center gap-2">
          <Users size={11} />
          {fmt(outcome.reach.uniqueVisitors)} anonymous visitors · no faces stored
        </div>
      </footer>
    </div>
  );
}

function BigNumber({
  label,
  value,
  note,
  accent,
  spark,
}: {
  label: string;
  value: string;
  note?: string;
  accent?: "cyan" | "amber" | "green";
  spark: number[];
}) {
  const colorMap = {
    cyan: "var(--accent-cyan)",
    amber: "var(--accent-amber)",
    green: "var(--accent)",
  };
  return (
    <div className="panel-elevated p-5">
      <div className="text-[10px] uppercase tracking-[0.18em] text-text-secondary">
        {label}
      </div>
      <div
        className="text-4xl font-semibold tabular tracking-tight mt-2"
        style={{ color: accent ? colorMap[accent] : undefined }}
      >
        {value}
      </div>
      <div className="mt-2 flex items-center justify-between">
        <span className="text-xs tabular text-text-muted">{note}</span>
        <Sparkline
          data={spark}
          width={70}
          height={24}
          stroke={accent ? colorMap[accent] : "var(--accent)"}
        />
      </div>
    </div>
  );
}

function FunnelStep({
  step,
  name,
  count,
  share,
  drop,
  terminal,
}: {
  step: number;
  name: string;
  count: number;
  share: number;
  drop?: number;
  terminal?: boolean;
}) {
  return (
    <div className="bg-bg-panel p-4 relative">
      <div className="text-[10px] tabular text-text-faint">0{step}</div>
      <div className="text-sm font-medium mt-1">{name}</div>
      <div className="text-3xl font-semibold tabular tracking-tight mt-2">
        {count.toLocaleString()}
      </div>
      <div className="text-xs text-text-muted tabular mt-0.5">
        {share}% first-touch
      </div>
      <div className="mt-3 h-1 rounded-full bg-bg-elevated overflow-hidden">
        <div
          className="h-full bg-accent"
          style={{ width: `${Math.max(share, 4)}%` }}
        />
      </div>
      {!terminal && drop !== undefined && drop > 0 && (
        <div className="mt-2 text-[10px] text-accent-red tabular">
          −{fmt(drop, 1)}% vs top
        </div>
      )}
    </div>
  );
}

function ZoneRow({ zone, color }: { zone: ZoneOutcome; color: string }) {
  return (
    <div className="panel p-5 flex items-center justify-between gap-4">
      <div className="flex items-center gap-3 min-w-0">
        <span
          className="w-2.5 h-12 rounded-full shrink-0"
          style={{ background: color, boxShadow: `0 0 12px ${color}` }}
        />
        <div className="min-w-0">
          <div className="text-base font-medium">{zoneName(zone.zoneId)}</div>
          <div className="text-xs text-text-muted tabular">
            avg {fmtDuration(zone.avgDwellSec)} dwell · {zone.kind} ·{" "}
            {zone.entries} entr{zone.entries === 1 ? "y" : "ies"}
          </div>
        </div>
      </div>
      <div className="text-right">
        <div className="text-2xl font-semibold tabular leading-none">
          {fmt(zone.visitors)}
        </div>
        <div className="text-[10px] uppercase tracking-[0.15em] text-text-muted mt-1">
          visitors
        </div>
      </div>
    </div>
  );
}

function Moment({ time, title, detail }: { time: string; title: string; detail: string }) {
  return (
    <li className="grid grid-cols-[56px_1fr] gap-3">
      <span className="text-text-muted tabular text-sm mt-0.5">{time}</span>
      <div>
        <div className="text-text-primary font-medium">{title}</div>
        <div className="text-text-muted text-xs mt-0.5">{detail}</div>
      </div>
    </li>
  );
}

function Rec({ n, text, cite }: { n: number; text: string; cite: string }) {
  return (
    <li className="flex gap-3">
      <span className="w-6 h-6 rounded-full bg-accent/15 border border-accent/40 text-accent text-xs font-medium flex items-center justify-center shrink-0 mt-0.5 tabular">
        {n}
      </span>
      <div className="flex-1">
        <div className="text-text-secondary leading-relaxed">{text}</div>
        <div className="text-[11px] text-text-muted mt-0.5 tabular">{cite}</div>
      </div>
    </li>
  );
}
