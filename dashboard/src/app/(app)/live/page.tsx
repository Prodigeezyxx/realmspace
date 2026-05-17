import {
  Activity,
  ArrowUpRight,
  Sparkles,
  Timer,
  Users,
  Zap,
} from "lucide-react";

import { EventTimeline } from "@/components/viz/EventTimeline";
import { Heatmap } from "@/components/viz/Heatmap";
import { LiveVideo } from "@/components/viz/LiveVideo";
import { TrafficChart } from "@/components/viz/TrafficChart";
import { ZoneList } from "@/components/viz/ZoneList";
import { Panel } from "@/components/ui/Panel";
import { Pill } from "@/components/ui/Pill";
import { Sparkline } from "@/components/ui/Sparkline";
import { Stat } from "@/components/ui/Stat";
import {
  attentionSeries,
  dwellSeries,
  liveCounts,
  triggerSeries,
} from "@/lib/mock/session";
import { formatDuration, formatNumber } from "@/lib/utils";

export default function LivePage() {
  return (
    <div className="p-5 space-y-5 max-w-[1600px] mx-auto">
      {/* ── Top KPI strip */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <KpiTile
          icon={<Users size={14} />}
          label="People now"
          value={liveCounts.peopleNow.toString()}
          accent="cyan"
          series={[8, 11, 9, 12, 14, 13, 15, 14]}
          delta={{ value: "+2 in last 5m", direction: "up" }}
        />
        <KpiTile
          icon={<Activity size={14} />}
          label="Today"
          value={formatNumber(liveCounts.peopleToday)}
          series={[]}
          delta={{
            value: `+${Math.round(liveCounts.peopleVsYesterday * 100)}% vs yesterday`,
            direction: "up",
          }}
        />
        <KpiTile
          icon={<Timer size={14} />}
          label="Avg dwell"
          value={formatDuration(liveCounts.avgDwellSeconds)}
          series={dwellSeries.slice(-12)}
          delta={{ value: "+12% wk", direction: "up" }}
        />
        <KpiTile
          icon={<Zap size={14} />}
          label="Triggers fired"
          value={formatNumber(liveCounts.triggers)}
          accent="amber"
          series={triggerSeries.slice(-12)}
          delta={{ value: "+18 last hour", direction: "up" }}
        />
      </div>

      {/* ── Main grid */}
      <div className="grid grid-cols-12 gap-5">
        {/* Live camera + heatmap */}
        <div className="col-span-12 xl:col-span-8 space-y-5">
          <Panel
            title="Camera 01 · Pavilion No. 7"
            subtitle="Anonymous detection + tracking · YOLOv8 + ByteTrack · on-device"
            action={
              <div className="flex items-center gap-2">
                <Pill variant="live"><span className="live-dot" />Live</Pill>
                <Pill variant="neutral">22 fps</Pill>
              </div>
            }
            padded={false}
          >
            <div className="aspect-[16/9] p-3">
              <LiveVideo />
            </div>
          </Panel>

          <div className="grid md:grid-cols-2 gap-5">
            <Panel
              title="Attention heatmap"
              subtitle="Aggregated dwell density across the booth"
              padded={false}
            >
              <div className="p-3">
                <Heatmap height={220} />
              </div>
            </Panel>

            <Panel
              title="Traffic · last 60 min"
              subtitle={`Peak ${liveCounts.peakConcurrent} concurrent visitors today`}
            >
              <TrafficChart />
            </Panel>
          </div>

          {/* Event log */}
          <Panel
            title="Live event log"
            subtitle="Every detection becomes a graph node"
            action={<Pill variant="info">{`${liveCounts.insights} insights today`}</Pill>}
            padded={false}
          >
            <div className="p-2 max-h-[360px] overflow-y-auto">
              <EventTimeline />
            </div>
          </Panel>
        </div>

        {/* Side column */}
        <div className="col-span-12 xl:col-span-4 space-y-5">
          <Panel title="Zones · live" subtitle="Visitors currently in each zone">
            <ZoneList />
          </Panel>

          <Panel
            title="Attention score"
            subtitle="Avg engagement across active visitors"
          >
            <div className="flex items-center gap-5">
              <Stat
                label="Now"
                value={`${Math.round(attentionSeries[attentionSeries.length - 1] * 100)}`}
                unit="%"
                accent="cyan"
                size="xl"
              />
              <div className="flex-1">
                <Sparkline
                  data={attentionSeries.slice(-30)}
                  width={220}
                  height={56}
                  stroke="var(--accent-cyan)"
                  fill="rgba(0,212,255,0.08)"
                  showLast
                />
                <div className="mt-1 text-[10px] tabular text-text-muted flex justify-between">
                  <span>-30m</span>
                  <span>now</span>
                </div>
              </div>
            </div>
          </Panel>

          <Panel
            title="Insights"
            subtitle="Generated every 10 minutes by the AI"
            action={<Sparkles size={14} className="text-accent-violet" />}
          >
            <ul className="space-y-3 text-sm">
              <Insight
                text="Visitors who try the Scent Quiz dwell 2.4× longer in the Lounge."
                ts="3m ago"
              />
              <Insight
                text="Bottle Wall captures 86% of gazes for visitors within 1m."
                ts="11m ago"
              />
              <Insight
                text="Entry Arch is dropping 38% of visitors within 30s — queue signage unclear."
                ts="22m ago"
                warn
              />
            </ul>
          </Panel>

          <Panel title="Session" subtitle="Pavilion No. 7 · Lagos">
            <dl className="grid grid-cols-2 gap-y-2 text-sm">
              <dt className="text-text-muted">Day</dt>
              <dd className="tabular text-right">1 of 3</dd>
              <dt className="text-text-muted">Camera</dt>
              <dd className="tabular text-right">Logitech C920</dd>
              <dt className="text-text-muted">Edge</dt>
              <dd className="tabular text-right">MacBook · M2 Pro</dd>
              <dt className="text-text-muted">Storage</dt>
              <dd className="tabular text-right">412 MB · local</dd>
              <dt className="text-text-muted">Frames purged</dt>
              <dd className="tabular text-right text-accent-green">98.4%</dd>
            </dl>
          </Panel>
        </div>
      </div>
    </div>
  );
}

function KpiTile({
  icon,
  label,
  value,
  delta,
  series,
  accent,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  delta?: { value: string; direction: "up" | "down" };
  series?: number[];
  accent?: "cyan" | "amber" | "blue" | "green" | "red" | "violet";
}) {
  const strokeMap = {
    cyan: "var(--accent-cyan)",
    amber: "var(--accent-amber)",
    blue: "var(--accent-blue)",
    green: "var(--accent-green)",
    red: "var(--accent-red)",
    violet: "var(--accent-violet)",
  };
  const stroke = strokeMap[accent ?? "blue"];
  return (
    <div className="panel-elevated px-5 py-4 flex flex-col gap-3 group hover:border-border-subtle transition-colors">
      <div className="flex items-center justify-between text-text-secondary">
        <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.16em]">
          <span className="text-text-muted">{icon}</span>
          {label}
        </div>
        <ArrowUpRight
          size={14}
          className="text-text-muted group-hover:text-text-primary transition-colors"
        />
      </div>
      <div className="flex items-end justify-between gap-3">
        <div
          className="text-4xl font-semibold tabular tracking-tight"
          style={{ color: accent ? stroke : undefined }}
        >
          {value}
        </div>
        {series && series.length > 1 && (
          <Sparkline data={series} width={100} height={28} stroke={stroke} />
        )}
      </div>
      {delta && (
        <div className="text-[11px] tabular text-text-secondary flex items-center gap-1">
          <span
            className={
              delta.direction === "up" ? "text-accent-green" : "text-accent-red"
            }
          >
            {delta.direction === "up" ? "▲" : "▼"}
          </span>
          {delta.value}
        </div>
      )}
    </div>
  );
}

function Insight({
  text,
  ts,
  warn,
}: {
  text: string;
  ts: string;
  warn?: boolean;
}) {
  return (
    <li className="flex gap-3">
      <span
        className={`mt-1.5 h-1.5 w-1.5 rounded-full shrink-0 ${
          warn ? "bg-accent-amber" : "bg-accent-violet"
        }`}
      />
      <div className="flex-1">
        <div className="text-text-primary leading-snug">{text}</div>
        <div className="text-[10px] tabular text-text-muted mt-0.5">{ts}</div>
      </div>
    </li>
  );
}
