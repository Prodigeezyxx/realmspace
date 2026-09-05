"use client";

/**
 * A client's copy of the report, opened from a link with no account.
 *
 * ## Outside the app shell on purpose
 *
 * This route sits beside `(app)` rather than inside it, so it gets no nav rail,
 * no status bar, no session switcher and no first-run gate. Every one of those
 * assumes a signed-in operator with a tenant, and none of them is anything to
 * show somebody who was sent a link. What they get is the report and nothing
 * else, which is also the honest shape of what the link grants.
 *
 * ## The token is a query parameter
 *
 * `?t=…` rather than `/shared/<token>`, because `next.config` is
 * `output: export` — a dynamic segment would need `generateStaticParams`, and
 * the tokens do not exist at build time.
 *
 * A token in a query string reaches server logs and referrer headers.
 * `routers/live.py` already says this about the WebSocket token and mitigates it
 * with a short life rather than pretending otherwise; the same applies here, and
 * the default thirty-day expiry is the mitigation.
 */

import { FileText, Info } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useMemo } from "react";

import { RoiScorecard } from "@/components/report/RoiScorecard";
import { EmptyState } from "@/components/ui/EmptyState";
import { Panel } from "@/components/ui/Panel";
import { buildFunnel, buildZoneRows } from "@/lib/report/derive";
import { useSharedReport } from "@/lib/report/useSharedReport";
import { formatDuration } from "@/lib/utils";

export default function SharedReportPage() {
  // `useSearchParams` needs a Suspense boundary under static export.
  return (
    <Suspense fallback={<Centered title="Opening the report…" />}>
      <SharedReport />
    </Suspense>
  );
}

function Centered({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="min-h-screen flex items-center justify-center p-6">
      <EmptyState variant="page" icon={<Info size={20} />} title={title} hint={hint} />
    </div>
  );
}

function SharedReport() {
  const token = useSearchParams().get("t");
  const { status, report, config } = useSharedReport(token);

  const zoneMeta = useMemo(
    () =>
      (config?.zones ?? []).map((z) => ({
        id: z.id,
        name: z.name,
        funnelOrder: z.funnelOrder ?? undefined,
      })),
    [config]
  );

  const funnel = useMemo(
    () => (report ? buildFunnel(report.events, zoneMeta) : []),
    [report, zoneMeta]
  );
  const zoneRows = useMemo(
    () => (report ? buildZoneRows(report.events, zoneMeta) : []),
    [report, zoneMeta]
  );

  if (status === "loading") return <Centered title="Opening the report…" />;

  if (status === "offline") {
    return (
      <Centered
        title="No backend is configured"
        hint="This build has no bus URL, so there is nothing to read a shared report from."
      />
    );
  }

  if (status === "invalid" || !report) {
    // One message for expired, revoked and never-existed alike — the backend
    // refuses to distinguish them, and inventing a distinction here would undo
    // that.
    return (
      <Centered
        title="This link is no longer valid"
        hint="It may have expired or been revoked. Ask whoever sent it for a new one."
      />
    );
  }

  return (
    <div className="min-h-screen canvas-vignette">
      <header className="border-b border-border-hairline">
        <div className="max-w-[1100px] mx-auto px-6 md:px-10 py-6 flex items-baseline justify-between gap-4 flex-wrap">
          <div>
            <div className="text-[10px] uppercase tracking-[0.16em] text-text-muted font-medium">
              realmspace · shared report
            </div>
            <h1 className="text-2xl font-semibold tracking-tight mt-1">
              {config?.campaign || config?.sessionId}
            </h1>
            {config?.venue && (
              <p className="text-sm text-text-secondary mt-0.5">{config.venue}</p>
            )}
          </div>
          <p className="text-xs text-text-muted max-w-sm leading-relaxed">
            A read-only copy. Figures are computed from this activation&rsquo;s own
            event log by the same code the operator sees, and contact details are
            not included.
          </p>
        </div>
      </header>

      <div className="max-w-[1100px] mx-auto p-6 md:p-10 space-y-6">
        {report.status === "empty" ? (
          <EmptyState
            variant="page"
            icon={<FileText size={20} />}
            title="No activity was recorded"
            hint="This activation is configured, and its log is genuinely empty."
          />
        ) : (
          <>
            <RoiScorecard report={report} />

            {report.missing.length > 0 && (
              <Panel
                title="What is not shown"
                subtitle="Stated rather than rendered as a zero"
              >
                <ul className="space-y-2 text-sm text-text-secondary">
                  {report.missing.map((m) => (
                    <li key={m} className="flex gap-2 leading-relaxed">
                      <span className="text-text-faint">·</span>
                      <span>{m}</span>
                    </li>
                  ))}
                </ul>
              </Panel>
            )}

            {funnel.length > 0 && (
              <Panel
                title="Funnel"
                subtitle="In the order the activation was designed, never sorted by traffic"
              >
                <ul className="space-y-2 text-sm">
                  {funnel.map((step) => (
                    <li
                      key={step.zoneId}
                      className="flex items-baseline justify-between gap-4 border-t border-border-hairline pt-2 first:border-0 first:pt-0"
                    >
                      <span>{step.name}</span>
                      <span className="tabular-nums text-text-secondary">
                        {step.visitors}
                      </span>
                    </li>
                  ))}
                </ul>
              </Panel>
            )}

            {zoneRows.length > 0 && (
              <Panel title="Zones" subtitle="Visitors and average dwell">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-text-muted text-[11px] uppercase tracking-[0.14em]">
                      <th className="text-left font-medium pb-2">Zone</th>
                      <th className="text-right font-medium pb-2">Visitors</th>
                      <th className="text-right font-medium pb-2">Avg dwell</th>
                      <th className="text-right font-medium pb-2">Reach</th>
                    </tr>
                  </thead>
                  <tbody>
                    {zoneRows.map((row) => (
                      <tr key={row.zoneId} className="border-t border-border-hairline">
                        <td className="py-2">{row.name}</td>
                        <td className="py-2 text-right tabular-nums">{row.visitors}</td>
                        <td className="py-2 text-right tabular-nums">
                          {formatDuration(row.avgDwellSec)}
                        </td>
                        <td className="py-2 text-right tabular-nums text-text-secondary">
                          {row.reachPct}%
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Panel>
            )}
          </>
        )}
      </div>
    </div>
  );
}
