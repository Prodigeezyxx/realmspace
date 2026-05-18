"use client";

import { FileText, Loader2 } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Panel } from "@/components/ui/Panel";
import { runSessionReport } from "@/lib/agent-engine";
import { liveCounts, zones } from "@/lib/mock/session";
import type { ReportGenOutput } from "@/skills/report-gen";

export function ReportGenerator() {
  const [markdown, setMarkdown] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function generate() {
    setLoading(true);
    const results = await runSessionReport({
      summary: {
        totalVisitors: liveCounts.peopleToday,
        peakTime: "14:32",
        topZones: zones.slice(0, 3).map((z) => ({
          name: z.name,
          dwellSec: 180 + Math.random() * 120,
        })),
        funnelConversionPct: 34,
        sponsorExposureSec: 4200,
      },
    });
    const out = results[0]?.skillChain["report-gen"] as ReportGenOutput | undefined;
    setMarkdown(out?.markdown ?? "No report generated.");
    setLoading(false);
  }

  return (
    <Panel title="Generate report" subtitle="Report agent · local template (no API)">
      <Button
        variant="primary"
        size="sm"
        icon={loading ? <Loader2 size={14} className="animate-spin" /> : <FileText size={14} />}
        onClick={() => void generate()}
        disabled={loading}
      >
        {loading ? "Generating…" : "Generate from session data"}
      </Button>
      {markdown && (
        <pre className="mt-4 text-xs text-text-secondary whitespace-pre-wrap font-sans leading-relaxed max-h-[320px] overflow-y-auto border border-border-hairline rounded-lg p-4 bg-bg-canvas">
          {markdown}
        </pre>
      )}
    </Panel>
  );
}
