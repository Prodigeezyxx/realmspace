/**
 * Stack: SessionSummary → { markdown, sections[] }
 * Template-based — no Claude API.
 */
import type { SkillModule, SkillRunInput } from "./types";

export interface SessionSummary {
  totalVisitors: number;
  peakTime: string;
  topZones: { name: string; dwellSec: number }[];
  funnelConversionPct: number;
  sponsorExposureSec: number;
}

export interface ReportSection {
  id: string;
  title: string;
  body: string;
}

export interface ReportGenOutput {
  markdown: string;
  sections: ReportSection[];
}

export const reportGenSkill: SkillModule<ReportGenOutput> = {
  id: "report-gen",
  stack: "trigger.payload.summary → markdown report",
  async run(input: SkillRunInput) {
    const summary = input.trigger.payload.summary as SessionSummary;
    const ctx = input.session;

    const top = summary.topZones
      .map((z, i) => `${i + 1}. **${z.name}** — ${Math.round(z.dwellSec / 60)}m avg dwell`)
      .join("\n");

    const sections: ReportSection[] = [
      {
        id: "exec",
        title: "Executive Summary",
        body: `${ctx.eventName} at ${ctx.venue} drew **${summary.totalVisitors}** unique visitors with peak traffic at ${summary.peakTime}.`,
      },
      {
        id: "flow",
        title: "Attendance & Flow",
        body: `Peak hour: ${summary.peakTime}. Funnel conversion: **${summary.funnelConversionPct}%**.`,
      },
      {
        id: "zones",
        title: "Zone Performance",
        body: top || "No zone dwell data recorded.",
      },
      {
        id: "sponsor",
        title: "Sponsor ROI",
        body: `Estimated sponsor exposure: **${Math.round(summary.sponsorExposureSec / 60)}** minutes aggregate dwell in sponsor zones.`,
      },
      {
        id: "recs",
        title: "Recommendations",
        body:
          "1. Shift staff to the highest-dwell zone during peak.\n2. Shorten queue paths near entry if conversion lags.\n3. Replay twin timeline for the top 3 dwell spikes.",
      },
    ];

    const markdown = sections
      .map((s) => `## ${s.title}\n\n${s.body}`)
      .join("\n\n");

    return { markdown, sections };
  },
};
