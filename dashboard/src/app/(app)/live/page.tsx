"use client";

import {
  Activity,
  ArrowUpRight,
  Eye,
  Sparkles,
  Timer,
  Users,
  Zap,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { EventTimeline } from "@/components/viz/EventTimeline";
import { Heatmap } from "@/components/viz/Heatmap";
import { TrafficChart } from "@/components/viz/TrafficChart";
import {
  WebcamDetector,
  type DetectorStats,
} from "@/components/viz/WebcamDetector";
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
import type { Track } from "@/lib/tracker";

interface LiveEvent {
  id: string;
  ts: number;
  text: string;
  color: string;
}

export default function LivePage() {
  // ── Real detector state ───────────────────────────────────────────────
  const [stats, setStats] = useState<DetectorStats | null>(null);
  const lastIdsRef = useRef<Set<number>>(new Set());
  const [liveEvents, setLiveEvents] = useState<LiveEvent[]>([]);
  const [peopleHistory, setPeopleHistory] = useState<number[]>([]);
  const lastHistoryTickRef = useRef<number>(0);

  const handleStats = useCallback((s: DetectorStats) => {
    setStats(s);

    const currentIds = new Set(s.activeTracks.map((t) => t.id));
    const previousIds = lastIdsRef.current;

    // Detect new entries
    s.activeTracks.forEach((t) => {
      if (!previousIds.has(t.id)) {
        setLiveEvents((cur) => [
          {
            id: `enter_${t.id}_${Date.now()}`,
            ts: Date.now(),
            text: `${t.label} entered the frame`,
            color: t.color,
          },
          ...cur,
        ].slice(0, 30));
      }
    });

    // Detect exits
    previousIds.forEach((id) => {
      if (!currentIds.has(id)) {
        setLiveEvents((cur) => [
          {
            id: `exit_${id}_${Date.now()}`,
            ts: Date.now(),
            text: `P-${id.toString().padStart(3, "0")} left the frame`,
            color: "#86868b",
          },
          ...cur,
        ].slice(0, 30));
      }
    });

    lastIdsRef.current = currentIds;

    // Sample people-count history every 500ms for the sparkline
    const now = Date.now();
    if (now - lastHistoryTickRef.current > 500) {
      lastHistoryTickRef.current = now;
      setPeopleHistory((cur) => [...cur.slice(-59), s.activeTracks.length]);
    }
  }, []);

  // Reset events on full page mount
  useEffect(() => {
    setLiveEvents([]);
    setPeopleHistory([]);
  }, []);

  const realPeopleNow = stats?.activeTracks.length ?? 0;
  const realTotalSeen = stats?.totalSeen ?? 0;
  const realFps = stats?.fps ?? 0;
  const sessionStartedAt = stats?.sessionStartedAt;
  const sessionDurationSec = sessionStartedAt
    ? Math.max(0, Math.floor((Date.now() - sessionStartedAt) / 1000))
    : 0;
  const isDetectorRunning = !!stats?.sessionStartedAt;

  return (
    <div className="p-5 space-y-5 max-w-[1600px] mx-auto">
      {/* ── Top KPI strip — these go LIVE the moment you start the detector */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <KpiTile
          icon={<Users size={14} />}
          label="People now"
          value={isDetectorRunning ? realPeopleNow.toString() : liveCounts.peopleNow.toString()}
          accent="brand"
          series={isDetectorRunning ? peopleHistory : [8, 11, 9, 12, 14, 13, 15, 14]}
          delta={
            isDetectorRunning
              ? {
                  value: `${realFps.toFixed(1)} fps live`,
                  direction: realPeopleNow > 0 ? "up" : "flat",
                }
              : { value: "+2 in last 5m", direction: "up" }
          }
        />
        <KpiTile
          icon={<Activity size={14} />}
          label={isDetectorRunning ? "Unique today" : "Today"}
          value={
            isDetectorRunning
              ? realTotalSeen.toString()
              : formatNumber(liveCounts.peopleToday)
          }
          series={isDetectorRunning ? peopleHistory : []}
          delta={
            isDetectorRunning
              ? { value: "this session", direction: "up" }
              : {
                  value: `+${Math.round(liveCounts.peopleVsYesterday * 100)}% vs yesterday`,
                  direction: "up",
                }
          }
        />
        <KpiTile
          icon={<Timer size={14} />}
          label={isDetectorRunning ? "Session length" : "Avg dwell"}
          value={
            isDetectorRunning
              ? formatDuration(sessionDurationSec)
              : formatDuration(liveCounts.avgDwellSeconds)
          }
          series={dwellSeries.slice(-12)}
          delta={
            isDetectorRunning
              ? { value: "live", direction: "up" }
              : { value: "+12% wk", direction: "up" }
          }
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
            title="Camera 01 · this device"
            subtitle="Real-time on-device detection · COCO-SSD + centroid tracker · no frames stored"
            action={
              <div className="flex items-center gap-2">
                <Pill variant={isDetectorRunning ? "live" : "neutral"}>
                  <span className={isDetectorRunning ? "live-dot" : "h-2 w-2 rounded-full bg-text-muted inline-block"} />
                  {isDetectorRunning ? "Live" : "Standby"}
                </Pill>
                {isDetectorRunning && (
                  <Pill variant="neutral" className="tabular">
                    {realFps.toFixed(1)} fps
                  </Pill>
                )}
              </div>
            }
            padded={false}
          >
            <div className="aspect-[16/9] p-3">
              <WebcamDetector
                classFilter={["person"]}
                minConfidence={0.5}
                onStats={handleStats}
              />
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
              title={isDetectorRunning ? "People · this session" : "Traffic · last 60 min"}
              subtitle={
                isDetectorRunning
                  ? `${realTotalSeen} unique IDs so far`
                  : `Peak ${liveCounts.peakConcurrent} concurrent visitors today`
              }
            >
              {isDetectorRunning ? (
                <LiveTrafficChart history={peopleHistory} />
              ) : (
                <TrafficChart />
              )}
            </Panel>
          </div>

          {/* Event log */}
          <Panel
            title={isDetectorRunning ? "Live event log · this session" : "Live event log"}
            subtitle={
              isDetectorRunning
                ? "Generated from real detections"
                : "Every detection becomes a graph node"
            }
            action={<Pill variant="info">{`${liveCounts.insights} insights today`}</Pill>}
            padded={false}
          >
            <div className="p-2 max-h-[360px] overflow-y-auto">
              {isDetectorRunning && liveEvents.length > 0 ? (
                <LiveEventList events={liveEvents} />
              ) : (
                <EventTimeline />
              )}
            </div>
          </Panel>
        </div>

        {/* Side column */}
        <div className="col-span-12 xl:col-span-4 space-y-5">
          {isDetectorRunning && stats && stats.activeTracks.length > 0 && (
            <Panel
              title="Tracked subjects · live"
              subtitle={`${stats.activeTracks.length} in frame`}
              action={<Eye size={14} className="text-text-muted" />}
            >
              <ul className="space-y-1.5">
                {stats.activeTracks.map((t) => (
                  <TrackRow key={t.id} track={t} />
                ))}
              </ul>
            </Panel>
          )}

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
                accent="brand"
                size="xl"
              />
              <div className="flex-1">
                <Sparkline
                  data={attentionSeries.slice(-30)}
                  width={220}
                  height={56}
                  stroke="var(--accent)"
                  fill="rgba(66,250,161,0.10)"
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

          <Panel title="Engine" subtitle="What's running this">
            <dl className="grid grid-cols-2 gap-y-2 text-sm">
              <dt className="text-text-muted">Detector</dt>
              <dd className="tabular text-right">COCO-SSD · mobilenet_v2</dd>
              <dt className="text-text-muted">Tracker</dt>
              <dd className="tabular text-right">Centroid · in-browser</dd>
              <dt className="text-text-muted">Backend</dt>
              <dd className="tabular text-right">WebGL · this tab</dd>
              <dt className="text-text-muted">Inference</dt>
              <dd className="tabular text-right">{stats?.modelMs ?? "—"} ms / frame</dd>
              <dt className="text-text-muted">Frames stored</dt>
              <dd className="tabular text-right text-accent">0</dd>
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
  delta?: { value: string; direction: "up" | "down" | "flat" };
  series?: number[];
  accent?:
    | "brand"
    | "blue"
    | "cyan"
    | "amber"
    | "green"
    | "red"
    | "violet";
}) {
  const strokeMap = {
    brand: "var(--accent)",
    cyan: "var(--accent-cyan)",
    amber: "var(--accent-amber)",
    blue: "var(--accent-blue)",
    green: "var(--accent)",
    red: "var(--accent-red)",
    violet: "var(--accent-violet)",
  };
  const stroke = strokeMap[accent ?? "brand"];
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
              delta.direction === "up"
                ? "text-accent"
                : delta.direction === "down"
                  ? "text-accent-red"
                  : "text-text-muted"
            }
          >
            {delta.direction === "up" ? "▲" : delta.direction === "down" ? "▼" : "•"}
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

function TrackRow({ track }: { track: Track }) {
  const lifespanSec = (Date.now() - track.firstSeen) / 1000;
  return (
    <li className="flex items-center gap-3 px-2.5 py-2 rounded-md hover:bg-bg-elevated transition-colors">
      <span
        className="w-2.5 h-2.5 rounded-full shrink-0"
        style={{
          background: track.color,
          boxShadow: `0 0 0 3px ${track.color}22`,
        }}
      />
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium tabular">{track.label}</div>
        <div className="text-[10px] text-text-muted tabular">
          {track.class} · {Math.round(track.score * 100)}% · {formatDuration(lifespanSec)} in frame
        </div>
      </div>
      <span className="text-[10px] tabular text-accent font-medium">
        live
      </span>
    </li>
  );
}

function LiveEventList({ events }: { events: LiveEvent[] }) {
  return (
    <ul className="font-mono text-xs space-y-px">
      {events.map((e) => (
        <li
          key={e.id}
          className="grid grid-cols-[64px_8px_1fr] items-center gap-3 px-3 py-2 hover:bg-bg-elevated rounded-md"
        >
          <span className="text-text-faint tabular">
            {new Date(e.ts).toLocaleTimeString("en-US", {
              hour: "2-digit",
              minute: "2-digit",
              second: "2-digit",
              hour12: false,
            })}
          </span>
          <span
            className="w-2 h-2 rounded-full"
            style={{ background: e.color }}
          />
          <span className="text-text-primary truncate">{e.text}</span>
        </li>
      ))}
    </ul>
  );
}

function LiveTrafficChart({ history }: { history: number[] }) {
  if (history.length < 2) {
    return (
      <div className="h-44 flex items-center justify-center text-xs text-text-muted">
        Sampling the room…
      </div>
    );
  }
  const max = Math.max(2, ...history);
  const step = 100 / Math.max(1, history.length - 1);
  const path = history
    .map((v, i) => `${i === 0 ? "M" : "L"} ${i * step} ${100 - (v / max) * 90}`)
    .join(" ");
  return (
    <div className="h-44 relative">
      <svg
        viewBox="0 0 100 100"
        preserveAspectRatio="none"
        className="w-full h-full"
      >
        <defs>
          <linearGradient id="liveFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#42faa1" stopOpacity={0.28} />
            <stop offset="100%" stopColor="#42faa1" stopOpacity={0} />
          </linearGradient>
        </defs>
        <path d={`${path} L 100 100 L 0 100 Z`} fill="url(#liveFill)" />
        <path d={path} stroke="#42faa1" strokeWidth="0.6" fill="none" />
      </svg>
      <div className="absolute top-2 left-2 text-[10px] tabular text-text-muted">
        people in frame
      </div>
      <div className="absolute bottom-2 right-2 text-[10px] tabular text-accent font-medium">
        current: {history[history.length - 1]}
      </div>
    </div>
  );
}
