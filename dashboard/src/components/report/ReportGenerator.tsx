"use client";

/**
 * realmspace — the narrative summary, generated from the session's own figures.
 *
 * The button said "Generate from session data" and read `lib/mock/session` —
 * `liveCounts.peopleToday`, a hardcoded `peakTime: "14:32"`, a
 * `funnelConversionPct: 34` nobody computed, and zone dwell from
 * `Math.random()`. It produced a different report each time you pressed it,
 * none of them about the session in front of you.
 *
 * It now feeds `skills/report-gen.ts` the real scorecard. That skill is a
 * template, not an LLM, which is the correct tool while the AI provider decision
 * is still open (roadmap decision #2) — a template can only restate figures it
 * was given, so it cannot invent a finding.
 */

import { FileText, Loader2 } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Panel } from "@/components/ui/Panel";
import { runSessionReport } from "@/lib/agent-engine";
import { buildZoneRows } from "@/lib/report/derive";
import type { SessionReport } from "@/lib/report/useSessionReport";
import type { ReportGenOutput } from "@/skills/report-gen";

export function ReportGenerator({ report }: { report: SessionReport }) {
  const [markdown, setMarkdown] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function generate() {
    setLoading(true);

    const { scorecard, events, config } = report;
    const zoneRows = buildZoneRows(
      events,
      (config?.zones ?? []).map((z) => ({ id: z.id, name: z.name }))
    );

    // Peak hour, from the log rather than from a constant.
    const byHour = new Map<number, Set<string>>();
    for (const e of events) {
      if (e.type !== "spatial.zone_enter") continue;
      const anonId = (e.payload as { anonId?: string }).anonId;
      if (!anonId) continue;
      const h = new Date(e.occurredAt).getHours();
      if (!byHour.has(h)) byHour.set(h, new Set());
      byHour.get(h)!.add(anonId);
    }
    const peakHour = [...byHour.entries()].sort(
      (a, b) => b[1].size - a[1].size
    )[0]?.[0];

    // The funnel's end-to-end conversion: how many of the visitors who reached
    // the first zone also reached the last one.
    const ordered = [...zoneRows].sort((a, b) => b.reachPct - a.reachPct);
    const funnelConversionPct = ordered.length
      ? Math.round(ordered[ordered.length - 1].reachPct)
      : 0;

    const results = await runSessionReport({
      summary: {
        totalVisitors: scorecard.reach.uniqueVisitors,
        peakTime:
          peakHour != null ? `${String(peakHour).padStart(2, "0")}:00` : "—",
        topZones: zoneRows.slice(0, 3).map((z) => ({
          name: z.name,
          dwellSec: z.avgDwellSec,
        })),
        funnelConversionPct,
        // Aggregate dwell in zones weighted for ROI — the closest honest stand-in
        // for sponsor exposure until a producer emits surface-level dwell.
        sponsorExposureSec: Math.round(
          scorecard.engagement.dwellWeightedAttention
        ),
      },
    });

    const out = results[0]?.skillChain["report-gen"] as ReportGenOutput | undefined;
    setMarkdown(out?.markdown ?? "No report generated.");
    setLoading(false);
  }

  const empty = report.events.length === 0;

  return (
    // `data-print-hide` on the whole panel, not just the button: an unpressed
    // "Generate summary" in a client's PDF is a control they cannot use, and a
    // pressed one would print the summary a second time.
    <Panel
      title="Generate summary"
      subtitle="Report agent · local template, no API — restates the computed figures"
      data-print-hide
    >
      <Button
        variant="primary"
        size="sm"
        icon={
          loading ? (
            <Loader2 size={14} className="animate-spin" />
          ) : (
            <FileText size={14} />
          )
        }
        onClick={() => void generate()}
        disabled={loading || empty}
      >
        {loading ? "Generating…" : "Generate from session data"}
      </Button>
      {empty && (
        <p className="mt-3 text-xs text-text-muted">
          Nothing to summarise — this session has no events.
        </p>
      )}
      {markdown && (
        <pre className="mt-4 text-xs text-text-secondary whitespace-pre-wrap font-sans leading-relaxed max-h-[320px] overflow-y-auto border border-border-hairline rounded-lg p-4 bg-bg-canvas">
          {markdown}
        </pre>
      )}
    </Panel>
  );
}
