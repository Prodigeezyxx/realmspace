"use client";

/**
 * realmspace — the 24h client report.
 *
 * This page is the deliverable the product is sold on, so it obeys one rule
 * without exception (`docs/roi-framework.md` §3): **every figure on it traces to
 * an event in the log or to a parameter the operator set.**
 *
 * It did not used to. It rendered only for the demo session, and its numbers
 * were written by hand — "1,287 visitors", "4.2× ROI", "Peak concurrent — 31",
 * four invented recommendations. A client could not have told which figures were
 * theirs, which is exactly the trust the report exists to earn.
 *
 * Now: one code path for every session, all numbers computed, and anything that
 * cannot be computed says so and says what is missing. A blank is a statement,
 * not a gap.
 */

import {
  CalendarClock,
  Download,
  FileText,
  Info,
  Quote,
  Sparkles,
  Target,
  Users,
} from "lucide-react";
import { useMemo } from "react";

import { BenchmarkCard } from "@/components/report/BenchmarkCard";
import { OperatorNote } from "@/components/report/OperatorNote";
import { ShareWithClient } from "@/components/report/ShareWithClient";
import { ReportGenerator } from "@/components/report/ReportGenerator";
import { RoiScorecard } from "@/components/report/RoiScorecard";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Panel } from "@/components/ui/Panel";
import { Pill } from "@/components/ui/Pill";
import { Sparkline } from "@/components/ui/Sparkline";
import { Heatmap } from "@/components/viz/Heatmap";
import {
  buildFunnel,
  buildMoments,
  buildRecommendations,
  buildGazeRows,
  buildSurfaceRows,
  buildZoneRows,
  hourlyAvgDwell,
  hourlyLeads,
  hourlyVisitors,
} from "@/lib/report/derive";
import { useBenchmark } from "@/lib/report/useBenchmark";
import { useSessionReport, type SessionReport } from "@/lib/report/useSessionReport";
import { useActiveSession } from "@/lib/session/store";
import { formatDuration } from "@/lib/utils";
import type { Session } from "@/lib/session/types";

export default function ReportPage() {
  const session = useActiveSession();
  const report = useSessionReport(session);

  if (report.status === "loading") {
    return (
      <div className="max-w-[1100px] mx-auto p-6 md:p-10">
        <EmptyState
          icon={<FileText size={20} />}
          variant="page"
          title="Reading the session log…"
          hint="Pulling this session's events from the bus."
        />
      </div>
    );
  }

  if (report.status !== "ready") {
    return <NothingToReport session={session} report={report} />;
  }

  return <Report session={session} report={report} />;
}

/**
 * The two ways a report can have nothing in it, kept apart.
 *
 * "Nobody came" and "nothing was ever measuring" look identical on screen if you
 * let them, and confusing the second for the first is how a client is told they
 * had a quiet day when in fact the cameras were scoring against no zones at all.
 */
function NothingToReport({
  session,
  report,
}: {
  session: Session;
  report: SessionReport;
}) {
  const unconfigured = report.status === "unconfigured";
  return (
    <div className="max-w-[1100px] mx-auto p-6 md:p-10 space-y-6">
      <EmptyState
        icon={<Info size={20} />}
        title={
          unconfigured
            ? "This session was never set up for measurement"
            : `No activity recorded for ${session.name}`
        }
        variant="page"
        hint={
          unconfigured
            ? "There are no zones on this session, so nothing could have been measured — an empty report here does not mean an empty room."
            : "The session is configured correctly and the log is genuinely empty. Nobody was detected in any zone."
        }
      />
      {report.missing.length > 0 && (
        <Panel title="What is missing" subtitle="Fix these and the report fills in">
          <ul className="space-y-2 text-sm text-text-secondary">
            {report.missing.map((m) => (
              <li key={m} className="flex gap-2 leading-relaxed">
                <span className="text-text-faint">·</span>
                {m}
              </li>
            ))}
          </ul>
        </Panel>
      )}
    </div>
  );
}

function Report({ session, report }: { session: Session; report: SessionReport }) {
  // Reads three earlier activations' logs, so it settles after this page has
  // already rendered and carries its own loading state rather than holding the
  // report back behind it.
  const benchmark = useBenchmark(session, report.scorecard);
  const { scorecard, config, events } = report;

  const zoneMeta = useMemo(
    () =>
      (config?.zones ?? session.zones).map((z) => ({
        id: z.id,
        name: z.name,
        color: "color" in z ? (z.color as string | undefined) : undefined,
        funnelOrder:
          "funnelOrder" in z ? (z.funnelOrder as number | null) : undefined,
      })),
    [config, session.zones]
  );

  const funnel = useMemo(() => buildFunnel(events, zoneMeta), [events, zoneMeta]);
  const zoneRows = useMemo(() => buildZoneRows(events, zoneMeta), [events, zoneMeta]);
  /**
   * Touchpoint names, from the backend if it has them and from this session
   * otherwise.
   *
   * The fallback is a bug fix, not demo scaffolding: this read `config?.touchpoints`
   * alone, so a session configured *locally* — which is every session before it
   * is published, and the demo permanently — had its touchpoints ignored and the
   * report listed raw ids. `useSessionReport.zonesFor` already resolves zones
   * this way; surfaces were simply missed.
   *
   * The wizard calls the field `name` and the backend calls it `label`; mapped
   * here rather than at the row builder, which should not have to know there are
   * two spellings.
   */
  const touchpoints = useMemo(
    () =>
      config?.touchpoints ??
      session.touchpoints.map((t) => ({ id: t.id, label: t.name })),
    [config, session.touchpoints]
  );
  const surfaces = useMemo(
    () => buildSurfaceRows(events, touchpoints),
    [events, touchpoints]
  );
  const gazeRows = useMemo(() => buildGazeRows(events, zoneMeta), [events, zoneMeta]);
  const moments = useMemo(() => buildMoments(events, zoneMeta), [events, zoneMeta]);
  const traffic = useMemo(() => hourlyVisitors(events), [events]);
  const dwellSeries = useMemo(() => hourlyAvgDwell(events), [events]);
  const leadSeries = useMemo(() => hourlyLeads(events), [events]);
  const recommendations = useMemo(
    () =>
      buildRecommendations(funnel, zoneRows, scorecard.engagement.engagementRate),
    [funnel, zoneRows, scorecard.engagement.engagementRate]
  );

  const currency = config?.currency ?? "USD";
  const roi = scorecard.pipeline.roiRatio;
  const cpev = scorecard.pipeline.costPerEngagedVisit;
  const top = zoneRows[0];

  return (
    <div className="max-w-[1100px] mx-auto p-6 md:p-10 space-y-10">
      {report.missing.length > 0 && (
        <div className="panel p-4 text-xs text-text-secondary leading-relaxed">
          <div className="flex items-start gap-2">
            <Info size={13} className="mt-0.5 shrink-0 text-text-muted" />
            <div className="space-y-1">
              {report.missing.map((m) => (
                <div key={m}>{m}</div>
              ))}
            </div>
          </div>
        </div>
      )}

      <ReportGenerator report={report} />

      <RoiScorecard report={report} />

      <BenchmarkCard benchmark={benchmark} />

      {/* ── Cover */}
      <header className="space-y-6">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <Pill variant="info">
            <FileText size={11} />
            Client report
          </Pill>
          {/* Both of these had no `onClick` at all until 2026-08-21 — primary
              actions on the client deliverable that did nothing.

              `data-print-hide` and not Tailwind's `print:hidden`: checked in the
              browser, and the build emitted no `@media print` block for that
              variant at all, so the class was inert and the buttons printed. One
              mechanism, and one that has been looked at. */}
          <div className="flex items-center gap-2" data-print-hide>
            <ShareWithClient />
            <Button
              variant="primary"
              size="sm"
              icon={<Download size={14} />}
              onClick={() => window.print()}
            >
              Export PDF
            </Button>
          </div>
        </div>

        <div className="grid md:grid-cols-[1fr_auto] gap-6 items-end">
          <div>
            <p className="text-text-muted text-sm tabular uppercase">
              {[session.client, session.venue, session.city]
                .filter(Boolean)
                .join(" · ")}
              {session.startAt
                ? ` · ${new Date(session.startAt).toLocaleDateString()}`
                : ""}
            </p>
            <h1 className="text-5xl md:text-6xl font-semibold tracking-tight leading-[0.95] mt-3">
              {session.name}
              <span className="text-accent">.</span>
            </h1>
            <p className="mt-4 text-text-secondary text-lg max-w-xl leading-relaxed">
              {scorecard.reach.uniqueVisitors.toLocaleString()} anonymous visitors,{" "}
              {formatDuration(scorecard.engagement.avgDwellSec)} average dwell,{" "}
              {(scorecard.engagement.engagementRate * 100).toFixed(0)}% of them
              engaged past the {config?.engagedThresholdSeconds ?? 60}s threshold
              {top ? ` — with ${top.name} holding attention longest` : ""}.
            </p>
          </div>
          <div className="panel-elevated p-5 min-w-[220px]">
            <div className="text-[10px] uppercase tracking-[0.18em] text-text-muted">
              {roi != null ? "ROI per unit spent" : "Cost per engaged visit"}
            </div>
            <div className="text-5xl font-semibold tabular tracking-tight text-accent mt-1">
              {roi != null
                ? `${roi.toFixed(1)}×`
                : cpev != null
                  ? cpev.toLocaleString(undefined, {
                      style: "currency",
                      currency,
                      maximumFractionDigits: 2,
                    })
                  : "—"}
            </div>
            <div className="text-xs text-text-muted mt-1 tabular">
              {roi != null
                ? "vs. the 3–5× industry benchmark"
                : cpev != null
                  ? "ROI ratio needs influenced revenue"
                  : "set an activation cost to compute"}
            </div>
          </div>
        </div>
      </header>

      {/* ── Hero numbers.
          No "+18% vs Yday" deltas: there is no previous day in the log to
          compare against, and a comparison against nothing is the kind of
          number this rewrite exists to remove. */}
      <section className="grid md:grid-cols-4 gap-3">
        <BigNumber
          label="Unique visitors"
          value={scorecard.reach.uniqueVisitors.toLocaleString()}
          spark={traffic}
        />
        <BigNumber
          label="Avg dwell"
          value={formatDuration(scorecard.engagement.avgDwellSec)}
          accent="cyan"
          spark={dwellSeries}
        />
        <BigNumber
          label="Leads captured"
          value={scorecard.pipeline.leadsCaptured.toLocaleString()}
          accent="amber"
          spark={leadSeries}
          note={
            scorecard.pipeline.leadsCaptured === 0
              ? "No consent capture surface is reporting yet"
              : `${(scorecard.pipeline.firstPartyCaptureRate * 100).toFixed(1)}% of engaged visitors`
          }
        />
        <BigNumber
          label="Cost / engaged visit"
          value={
            cpev != null
              ? cpev.toLocaleString(undefined, {
                  style: "currency",
                  currency,
                  maximumFractionDigits: 2,
                })
              : "—"
          }
          accent="green"
          note={cpev == null ? "Activation cost not set" : undefined}
        />
      </section>

      {/* ── Headline */}
      {top && (
        <section className="panel-elevated p-7 relative overflow-hidden">
          <div className="absolute -top-12 -right-8 text-[200px] text-accent/10 font-serif">
            <Quote />
          </div>
          <Pill variant="violet" className="mb-4">
            <Sparkles size={11} />
            Headline
          </Pill>
          <h2 className="text-3xl md:text-4xl font-semibold tracking-tight leading-tight max-w-3xl">
            {top.name} held visitors{" "}
            <span className="text-accent">
              {formatDuration(top.avgDwellSec)}
            </span>{" "}
            on average, reaching{" "}
            <span className="text-accent">{top.reachPct}%</span> of everyone who
            came.
          </h2>
          <p className="mt-4 text-text-secondary max-w-2xl">
            Computed from {events.length.toLocaleString()} events in this
            session&apos;s log. Every figure on this page can be traced back to
            one of them.
          </p>
        </section>
      )}

      {/* ── Funnel */}
      {funnel.length > 0 ? (
        <section className="space-y-4">
          <h2 className="text-2xl font-semibold tracking-tight">
            The journey, as it was designed.
          </h2>
          <Panel padded={false}>
            <div
              className="p-5 grid gap-px bg-border-hairline rounded-xl overflow-hidden"
              style={{
                gridTemplateColumns: `repeat(${Math.min(funnel.length, 5)}, minmax(0, 1fr))`,
              }}
            >
              {funnel.map((s, i) => (
                <FunnelStepCard
                  key={s.zoneId}
                  step={i + 1}
                  name={s.name}
                  count={s.visitors}
                  share={s.share}
                  drop={i === 0 ? undefined : s.drop}
                  terminal={i === funnel.length - 1}
                />
              ))}
            </div>
          </Panel>
        </section>
      ) : (
        <Panel
          title="Funnel"
          subtitle="Needs a funnel order on the session's zones"
        >
          <p className="text-sm text-text-secondary leading-relaxed">
            No zone carries a funnel position, so there is no designed journey to
            measure against. Set the order in the session wizard and the funnel
            appears here — ordered as designed, not sorted by traffic, which would
            make every activation look like a success.
          </p>
        </Panel>
      )}

      {/* ── Looked at, never entered.
          Its own panel and deliberately not part of the ROI scorecard: folding
          gaze into Engagement would change figures against definitions clients
          have already agreed per activation, which roi-framework.md §3 rules
          out. Hidden when empty, because a deployment running the detect-only
          model emits no gaze at all and an empty panel would read as a fault. */}
      {gazeRows.length > 0 && (
        <Panel
          title="Looked at, never entered"
          subtitle="Attention from people standing somewhere else — the gap between noticing and walking in"
        >
          <div className="space-y-3">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-text-muted text-[11px] uppercase tracking-[0.14em]">
                  <th className="text-left font-medium pb-2">Zone</th>
                  <th className="text-right font-medium pb-2">Looked</th>
                  <th className="text-right font-medium pb-2">Never entered</th>
                  <th className="text-right font-medium pb-2">Attention</th>
                </tr>
              </thead>
              <tbody>
                {gazeRows.map((row) => (
                  <tr key={row.zoneId} className="border-t border-border-hairline">
                    <td className="py-2">{row.name}</td>
                    <td className="py-2 text-right tabular-nums">{row.watchers}</td>
                    <td className="py-2 text-right tabular-nums font-medium">
                      {row.watchersWhoNeverEntered}
                    </td>
                    <td className="py-2 text-right tabular-nums text-text-secondary">
                      {Math.round(row.attentionSec)}s
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="text-xs text-text-muted leading-relaxed">
              A held look, measured from body pose at the camera — a facing
              direction, not an eye-tracked gaze, so it says which way somebody
              was turned and not what they focused on. Glances are excluded:
              a look counts only once it is held. Somebody who looked and then
              walked in is counted here and by the funnel both, which is why the
              middle column is the one to read.
            </p>
          </div>
        </Panel>
      )}

      {/* ── Attention map + moments.
          The heatmap is the pre-existing `viz/Heatmap` component, unchanged:
          it aggregates positions onto the booth floor plan and shows its own
          empty state on a session with no recorded positions. Left exactly as
          it was rather than reworked, because it predates this phase's work and
          changing a feature nobody asked to change is its own kind of defect. */}
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
        <Panel
          title="Notable moments"
          subtitle="Each one is a maximum in the log, not a highlight reel"
        >
          {moments.length ? (
            <ul className="space-y-3 text-sm">
              {moments.map((m) => (
                <li key={m.title} className="flex gap-4">
                  <span className="tabular text-text-muted w-12 shrink-0">
                    {new Date(m.at).toLocaleTimeString(undefined, {
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </span>
                  <span className="min-w-0">
                    <span className="block font-medium">{m.title}</span>
                    <span className="block text-text-muted text-xs mt-0.5">
                      {m.detail}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-text-secondary leading-relaxed">
              Nothing stood out — no dwell, surface use or traffic peak in this
              session reached a level worth calling a moment.
            </p>
          )}
        </Panel>
      </section>

      {/* ── By zone */}
      {zoneRows.length > 0 && (
        <section className="space-y-4">
          <h2 className="text-2xl font-semibold tracking-tight">Zone by zone.</h2>
          <div className="grid md:grid-cols-2 gap-3">
            {zoneRows.map((z) => (
              <div
                key={z.zoneId}
                className="panel p-5 flex items-center justify-between gap-4"
              >
                <div className="flex items-center gap-3 min-w-0">
                  <span
                    className="w-2.5 h-12 rounded-full shrink-0"
                    style={{
                      background: z.color ?? "var(--accent)",
                      boxShadow: `0 0 12px ${z.color ?? "var(--accent)"}`,
                    }}
                  />
                  <div className="min-w-0">
                    <div className="text-base font-medium">{z.name}</div>
                    <div className="text-xs text-text-muted tabular">
                      avg {formatDuration(z.avgDwellSec)} dwell · reached by{" "}
                      {z.reachPct}%
                    </div>
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-2xl font-semibold tabular leading-none">
                    {z.visitors.toLocaleString()}
                  </div>
                  <div className="text-[10px] uppercase tracking-[0.15em] text-text-muted mt-1">
                    visitors
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* ── Surfaces */}
      <section>
        <Panel
          title="Touchpoint use"
          subtitle="Interactions per surface — not exposure seconds, which nothing measures yet"
        >
          {surfaces.length ? (
            <ul className="space-y-3 text-sm">
              {surfaces.map((s) => (
                <li key={s.surfaceId}>
                  <div className="flex items-center justify-between">
                    <span>{s.label}</span>
                    <span className="tabular text-text-muted">
                      {s.interactions.toLocaleString()}
                    </span>
                  </div>
                  <div className="mt-1.5 h-1 rounded-full bg-bg-elevated overflow-hidden">
                    <div
                      className="h-full bg-accent"
                      style={{ width: `${s.share}%` }}
                    />
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-text-secondary leading-relaxed">
              No touchpoint is emitting <code>surface.interaction</code> yet, so
              there is nothing to report. This is an absent signal, not a zero.
            </p>
          )}
        </Panel>
      </section>

      {/* ── Recommendations */}
      {recommendations.length > 0 && (
        <section>
          <Panel
            title="What the data suggests"
            subtitle="Rule-derived from the figures above — each one states its source"
          >
            <ol className="space-y-3 text-sm">
              {recommendations.map((r, i) => (
                <li key={r} className="flex gap-3">
                  <span className="tabular text-text-faint">0{i + 1}</span>
                  <span className="text-text-secondary leading-relaxed">{r}</span>
                </li>
              ))}
            </ol>
          </Panel>
        </section>
      )}

      {/* ── Operator note */}
      <section>
        <OperatorNote key={session.id} session={session} />
      </section>

      {/* ── Footer */}
      <footer className="border-t border-border-hairline pt-6 flex items-center justify-between text-xs text-text-muted flex-wrap gap-2">
        <div className="inline-flex items-center gap-1.5">
          <CalendarClock size={11} />
          Generated by realmspace · {new Date().toLocaleDateString()}
        </div>
        <div className="flex items-center gap-2">
          <Users size={11} />
          {scorecard.reach.uniqueVisitors.toLocaleString()} anonymous visitors ·
          no faces stored
        </div>
      </footer>
    </div>
  );
}

function BigNumber({
  label,
  value,
  spark,
  accent,
  note,
}: {
  label: string;
  value: string;
  spark?: number[];
  accent?: "cyan" | "amber" | "green";
  note?: string;
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
      <div className="mt-2 flex items-center justify-between gap-2 min-h-[24px]">
        <span className="text-xs text-text-muted leading-tight">{note}</span>
        {spark && spark.length > 1 && (
          <Sparkline
            data={spark}
            width={70}
            height={24}
            stroke={accent ? colorMap[accent] : "var(--accent)"}
          />
        )}
      </div>
    </div>
  );
}

function FunnelStepCard({
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
      <div className="text-[10px] tabular text-text-faint">
        0{step}
        {terminal && <Target size={10} className="inline ml-1 -mt-0.5" />}
      </div>
      <div className="text-sm font-medium mt-1">{name}</div>
      <div className="text-3xl font-semibold tabular tracking-tight mt-2">
        {count.toLocaleString()}
      </div>
      <div className="text-xs text-text-muted tabular mt-1">{share}% of entry</div>
      {drop != null && drop > 0 && (
        <div className="text-[10px] text-accent-red tabular mt-1">
          −{drop}% from previous
        </div>
      )}
    </div>
  );
}
