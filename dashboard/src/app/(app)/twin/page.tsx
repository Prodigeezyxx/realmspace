"use client";

import dynamic from "next/dynamic";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Pill } from "@/components/ui/Pill";
import { useTwinLive } from "@/hooks/useTwinLive";
import { useRecordedSessions, useTwinReplay } from "@/hooks/useTwinReplay";
import { peopleTracks } from "@/lib/mock/people";
import { surfaces, zones } from "@/lib/mock/session";
import { useLiveSession } from "@/lib/live-session/store";
import { useActiveSession } from "@/lib/session/store";
import { getTenantId } from "@/lib/tenant/context";
import { positionsAtFrom } from "@/lib/twin/replay-tracks";
import { cn, formatDuration } from "@/lib/utils";

const TwinScene = dynamic(
  () => import("@/components/twin/TwinScene").then((m) => m.TwinScene),
  { ssr: false, loading: () => <SceneLoading /> }
);

const DEMO_DURATION = 620;
const SPEEDS = [0.5, 1, 2, 4];

type InspectorTab = "people" | "zones" | "touchpoints";

export default function TwinPage() {
  const activeSession = useActiveSession();
  const detectorRunning = useLiveSession((s) => s.status === "running");
  const { avatars, heatmap } = useTwinLive();
  const tenantId = getTenantId();
  const recordedSessions = useRecordedSessions(tenantId);
  const [replaySessionId, setReplaySessionId] = useState<string | null>(null);
  const effectiveReplayId = !activeSession.isDemo
    ? replaySessionId ??
      (recordedSessions.some((s) => s.sessionId === activeSession.id)
        ? activeSession.id
        : recordedSessions[0]?.sessionId ?? null)
    : null;
  const replay = useTwinReplay(effectiveReplayId);
  const hasRecordedData = replay.status === "ready" && replay.eventCount > 0;
  const mode: "live" | "recorded" | "demo" | "empty" = detectorRunning
    ? "live"
    : activeSession.isDemo
      ? "demo"
      : hasRecordedData
        ? "recorded"
        : "empty";

  const [time, setTime] = useState(180);
  const [playing, setPlaying] = useState(true);
  const [speed, setSpeed] = useState(1);
  const [showHeatmap, setShowHeatmap] = useState(true);
  const [showZones, setShowZones] = useState(true);
  const [selectedPerson, setSelectedPerson] = useState<string | null>(null);
  const [tab, setTab] = useState<InspectorTab>("people");

  const tracks = mode === "recorded" ? replay.tracks : peopleTracks;
  const duration = mode === "recorded" ? Math.max(replay.durationSec, 1) : DEMO_DURATION;
  const displayTime = Math.min(time, duration);
  const activeTracks = useMemo(
    () => positionsAtFrom(tracks, displayTime),
    [tracks, displayTime]
  );

  useEffect(() => {
    if (!playing || mode === "live" || mode === "empty") return;
    const id = setInterval(() => {
      setTime((current) => {
        const next = current + 0.1 * speed;
        return next > duration ? 0 : next;
      });
    }, 100);
    return () => clearInterval(id);
  }, [playing, speed, duration, mode]);

  if (mode === "empty") return <TwinEmptyState />;

  const sceneVisitors = mode === "live" ? avatars.length : activeTracks.length;
  const totalVisitors = mode === "live" ? avatars.length : tracks.length;

  return (
    <div className="min-h-[calc(100dvh-72px)] flex flex-col">
      <header className="px-4 md:px-6 py-4 border-b border-border-hairline bg-bg-canvas flex flex-wrap items-center justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="page-kicker">Twin</span>
            <DataSourcePill mode={mode} source={replay.source} />
          </div>
          <h1 className="headline-medium mt-2 truncate">
            {mode === "live" ? "Live spatial view" : activeSession.name}
          </h1>
          <p className="text-xs text-text-muted mt-1">
            {mode === "live"
              ? "Tracks update from the live detector."
              : mode === "recorded"
                ? `${replay.eventCount.toLocaleString()} recorded events · ${replay.tracks.length} visitor tracks`
                : "Sample tracks for product review. This is not session data."}
          </p>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          {recordedSessions.length > 0 && mode !== "live" && !activeSession.isDemo && (
            <label className="sr-only" htmlFor="replay-session">Recorded session</label>
          )}
          {recordedSessions.length > 0 && mode !== "live" && !activeSession.isDemo && (
            <select
              id="replay-session"
              value={effectiveReplayId ?? ""}
              onChange={(event) => setReplaySessionId(event.target.value || null)}
              className="h-10 max-w-[260px] rounded-[14px] border border-border-hairline bg-bg-panel px-3 text-xs text-text-secondary outline-none focus:border-accent"
            >
              {recordedSessions.map((session) => (
                <option key={session.sessionId} value={session.sessionId}>
                  {session.sessionId} · {session.eventCount.toLocaleString()} events
                </option>
              ))}
            </select>
          )}
          <Button
            variant={showHeatmap ? "primary" : "secondary"}
            size="sm"
            onClick={() => setShowHeatmap((value) => !value)}
            aria-pressed={showHeatmap}
          >
            <span className="material-symbol material-symbol-sm">gradient</span>
            Heatmap
          </Button>
          <Button
            variant={showZones ? "primary" : "secondary"}
            size="sm"
            onClick={() => setShowZones((value) => !value)}
            aria-pressed={showZones}
          >
            <span className="material-symbol material-symbol-sm">layers</span>
            Zones
          </Button>
        </div>
      </header>

      <div className="flex-1 min-h-0 grid xl:grid-cols-[minmax(0,1fr)_330px] bg-bg-viewport">
        <section className="min-w-0 flex flex-col min-h-[620px] xl:min-h-0" aria-label="3D replay">
          <div className="relative flex-1 min-h-[520px]">
            <TwinScene
              time={displayTime}
              showHeatmap={showHeatmap}
              showZones={showZones}
              selectedPerson={selectedPerson}
              liveMode={mode === "live"}
              liveAvatars={avatars}
              liveHeatmap={heatmap}
              tracks={tracks}
            />

            <div className="absolute top-4 left-4 flex items-center gap-2">
              <Pill variant={mode === "live" ? "live" : "neutral"}>
                {mode === "live" && <span className="live-dot" />}
                {mode === "live" ? "Live" : `T+${formatDuration(displayTime)}`}
              </Pill>
              <Pill variant="neutral">{sceneVisitors} in view</Pill>
            </div>
            <div className="absolute top-4 right-4 hidden md:flex items-center gap-2 rounded-full border border-white/10 bg-black/50 px-3 py-2 text-[10px] text-white/65 backdrop-blur-md">
              Drag to orbit · scroll to zoom
            </div>
          </div>

          {mode !== "live" && (
            <Transport
              time={displayTime}
              duration={duration}
              playing={playing}
              speed={speed}
              onTime={setTime}
              onPlaying={setPlaying}
              onSpeed={setSpeed}
            />
          )}
        </section>

        <aside className="bg-bg-panel border-t xl:border-t-0 xl:border-l border-border-hairline min-h-0 flex flex-col" aria-label="Twin inspector">
          <div className="grid grid-cols-3 border-b border-border-hairline p-2 gap-1">
            {(["people", "zones", "touchpoints"] as const).map((item) => (
              <button
                key={item}
                onClick={() => setTab(item)}
                className={cn(
                  "h-10 rounded-[13px] text-[11px] font-bold capitalize transition-colors",
                  tab === item ? "bg-primary-container text-on-primary-container" : "text-text-muted hover:bg-bg-elevated"
                )}
              >
                {item}
              </button>
            ))}
          </div>
          <div className="p-4 border-b border-border-hairline grid grid-cols-2 gap-3">
            <InspectorStat label="In view" value={sceneVisitors} />
            <InspectorStat label="Total tracks" value={totalVisitors} />
          </div>
          <div className="flex-1 min-h-0 overflow-y-auto p-3">
            {tab === "people" && (
              <PeopleInspector
                tracks={tracks}
                activeIds={new Set(activeTracks.map((track) => track.id))}
                selected={selectedPerson}
                onSelect={setSelectedPerson}
              />
            )}
            {tab === "zones" && <ZonesInspector />}
            {tab === "touchpoints" && <TouchpointsInspector />}
          </div>
        </aside>
      </div>
    </div>
  );
}

function DataSourcePill({ mode, source }: { mode: "live" | "recorded" | "demo"; source: string }) {
  if (mode === "live") return <Pill variant="live">Live detector</Pill>;
  if (mode === "recorded") return <Pill variant="info">Recorded events · {source}</Pill>;
  return <Pill variant="violet">Demo dataset</Pill>;
}

function Transport({
  time,
  duration,
  playing,
  speed,
  onTime,
  onPlaying,
  onSpeed,
}: {
  time: number;
  duration: number;
  playing: boolean;
  speed: number;
  onTime: (value: number) => void;
  onPlaying: (value: boolean) => void;
  onSpeed: (value: number) => void;
}) {
  return (
    <div className="bg-bg-panel border-t border-border-hairline px-3 md:px-5 py-3 flex items-center gap-3 md:gap-5">
      <button
        className="w-10 h-10 rounded-[14px] border border-border-hairline bg-bg-raised grid place-items-center hover:border-border-strong"
        onClick={() => onTime(0)}
        title="Go to start"
        aria-label="Go to start"
      >
        <span className="material-symbol material-symbol-sm">skip_previous</span>
      </button>
      <button
        className="w-11 h-11 rounded-[16px] bg-accent text-[var(--md-sys-color-on-primary)] grid place-items-center shadow-[var(--glow-green)]"
        onClick={() => onPlaying(!playing)}
        title={playing ? "Pause replay" : "Play replay"}
        aria-label={playing ? "Pause replay" : "Play replay"}
      >
        <span className="material-symbol material-symbol-filled">{playing ? "pause" : "play_arrow"}</span>
      </button>
      <div className="flex-1 min-w-[120px]">
        <input
          aria-label="Replay time"
          type="range"
          min={0}
          max={duration}
          step={0.5}
          value={time}
          onChange={(event) => onTime(Number(event.target.value))}
          className="w-full"
        />
        <div className="flex justify-between text-[10px] font-mono text-text-muted">
          <span>{formatDuration(time)}</span><span>{formatDuration(duration)}</span>
        </div>
      </div>
      <div className="hidden sm:flex items-center gap-1">
        {SPEEDS.map((value) => (
          <button
            key={value}
            onClick={() => onSpeed(value)}
            className={cn(
              "h-9 min-w-10 px-2 rounded-[12px] border text-xs font-mono",
              speed === value ? "border-accent bg-accent/10 text-accent" : "border-border-hairline text-text-muted hover:border-border-strong"
            )}
          >
            {value}×
          </button>
        ))}
      </div>
    </div>
  );
}

function PeopleInspector({
  tracks,
  activeIds,
  selected,
  onSelect,
}: {
  tracks: typeof peopleTracks;
  activeIds: Set<string>;
  selected: string | null;
  onSelect: (id: string | null) => void;
}) {
  return (
    <ul className="space-y-1">
      {tracks.map((person) => {
        const active = activeIds.has(person.id);
        return (
          <li key={person.id}>
            <button
              onClick={() => onSelect(selected === person.id ? null : person.id)}
              className={cn(
                "w-full min-h-[58px] rounded-[14px] px-3 flex items-center gap-3 text-left border transition-colors",
                selected === person.id ? "bg-primary-container border-accent/30" : "border-transparent hover:bg-bg-elevated"
              )}
            >
              <span className="w-2.5 h-2.5 rounded-full" style={{ background: person.color, opacity: active ? 1 : 0.28 }} />
              <span className="flex-1 min-w-0">
                <span className="block text-sm font-bold font-mono">{person.id}</span>
                <span className="block text-[10px] text-text-muted mt-0.5">{formatDuration(person.totalDwellSeconds)} · {person.zonesVisited.length} zones</span>
              </span>
              <span className={cn("text-[10px] uppercase tracking-[.1em]", active ? "text-accent" : "text-text-faint")}>{active ? "in view" : "off view"}</span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function ZonesInspector() {
  return (
    <ul className="space-y-1">
      {zones.map((zone) => (
        <li key={zone.id} className="min-h-[54px] px-3 flex items-center gap-3 border-b border-border-hairline last:border-0">
          <span className="w-2.5 h-2.5 rounded-full" style={{ background: zone.color }} />
          <span className="flex-1 text-sm font-semibold">{zone.name}</span>
          <span className="text-[10px] uppercase tracking-[.1em] text-text-muted">{zone.type}</span>
        </li>
      ))}
    </ul>
  );
}

function TouchpointsInspector() {
  return (
    <ul className="space-y-1">
      {surfaces.map((surface) => (
        <li key={surface.id} className="min-h-[54px] px-3 flex items-center gap-3 border-b border-border-hairline last:border-0">
          <span className={cn("w-2.5 h-2.5 rounded-full", surface.active ? "bg-accent-cyan" : "bg-text-faint")} />
          <span className="flex-1 text-sm font-semibold">{surface.label}</span>
          <span className="text-[10px] font-mono text-text-muted">{surface.triggerCount}</span>
        </li>
      ))}
    </ul>
  );
}

function InspectorStat({ label, value }: { label: string; value: number }) {
  return <div><div className="text-[10px] uppercase tracking-[.1em] text-text-muted">{label}</div><div className="data-value text-2xl tabular mt-1">{value}</div></div>;
}

function SceneLoading() {
  return (
    <div className="absolute inset-0 grid place-items-center bg-bg-viewport text-text-muted">
      <div className="flex items-center gap-3 text-xs font-mono"><span className="alert-dot" />Loading 3D view…</div>
    </div>
  );
}

function TwinEmptyState() {
  const active = useActiveSession();
  return (
    <div className="realm-page space-y-8">
      <EmptyState
        variant="page"
        icon={<span className="material-symbol material-symbol-lg">view_in_ar</span>}
        title="No replay is available for this session."
        hint="Start the live detector to record anonymous tracks. The replay will appear here after the first events are stored."
        cta={{ href: "/live", label: "Open live session" }}
      />
      <div className="panel p-0 overflow-hidden max-w-3xl mx-auto">
        <div className="px-5 py-4 border-b border-border-hairline">
          <div className="text-sm font-bold">Configured layout</div>
          <div className="text-xs text-text-muted mt-1">{active.zones.length} zones · {active.touchpoints.length} touchpoints</div>
        </div>
        <div className="grid sm:grid-cols-2 gap-px bg-border-hairline">
          <div className="bg-bg-panel p-5">
            <div className="text-[10px] uppercase tracking-[.1em] text-text-muted mb-3">Zones</div>
            {active.zones.map((zone) => <div key={zone.id} className="py-2 flex items-center gap-2 text-sm"><span className="w-2 h-2 rounded-full" style={{ background: zone.color }} />{zone.name}</div>)}
          </div>
          <div className="bg-bg-panel p-5">
            <div className="text-[10px] uppercase tracking-[.1em] text-text-muted mb-3">Touchpoints</div>
            {active.touchpoints.map((point) => <div key={point.id} className="py-2 text-sm">{point.name}</div>)}
          </div>
        </div>
      </div>
    </div>
  );
}
