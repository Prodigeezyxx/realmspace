"use client";

import {
  Box,
  Eye,
  EyeOff,
  Layers,
  Pause,
  Play,
  Rewind,
  Sparkles,
  Users,
  X,
} from "lucide-react";
import dynamic from "next/dynamic";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Panel } from "@/components/ui/Panel";
import { Pill } from "@/components/ui/Pill";
import { surfaces, zones } from "@/lib/mock/session";
import { useLiveSession } from "@/lib/live-session/store";
import { useActiveSession } from "@/lib/session/store";
import { useTwinLive } from "@/hooks/useTwinLive";
import { useReplay } from "@/lib/twin/useReplay";
import { cn, formatDuration } from "@/lib/utils";

const TwinScene = dynamic(
  () => import("@/components/twin/TwinScene").then((m) => m.TwinScene),
  { ssr: false, loading: () => <SceneLoading /> }
);

const SPEEDS = [0.5, 1, 2, 4];

export default function TwinPage() {
  const activeSession = useActiveSession();
  const detectorRunning = useLiveSession((s) => s.status === "running");
  const { avatars, heatmap } = useTwinLive();
  const useLiveTwin = detectorRunning;

  /*
   * The session's own recorded paths, replacing five hardcoded mock tracks and
   * the 620-second constant that was their length. See lib/twin/replay.ts for
   * why a path built from zone membership is marked as inferred rather than
   * drawn like a measured one.
   */
  const { status, replay, eventCount } = useReplay(activeSession);
  const duration = replay.durationSec;
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [speed, setSpeed] = useState(1);
  const [showHeatmap, setShowHeatmap] = useState(true);
  const [selectedPerson, setSelectedPerson] = useState<string | null>(null);

  useEffect(() => {
    if (!playing || detectorRunning || duration <= 0) return;
    const id = setInterval(() => {
      setTime((t) => {
        const next = t + 0.1 * speed;
        return next > duration ? 0 : next;
      });
    }, 100);
    return () => clearInterval(id);
  }, [playing, speed, detectorRunning, duration]);

  /** Whoever the replay says was in the room at this moment. */
  const activeTracks = useMemo(
    () => replay.positionsAt(time),
    [replay, time]
  );

  // Gated on *events*, not on which session it is. The old test was
  // `!isDemo && !detectorRunning`, which meant a real recorded session — the
  // thing the twin exists to replay — could never be shown at all.
  if (status === "loading") return <TwinLoading />;
  if (status === "empty" && !detectorRunning) return <TwinEmptyState />;

  return (
    <div className="p-5 max-w-[1600px] mx-auto space-y-4">
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <Pill variant={useLiveTwin ? "live" : "violet"} className="mb-2">
            <Sparkles size={11} />
            {useLiveTwin ? "Digital twin · live" : "Digital twin · replay"}
          </Pill>
          <h1 className="text-2xl font-semibold tracking-tight">
            {useLiveTwin ? "Live spatial twin" : "Pavilion No. 7 — 3D replay"}
          </h1>
          <p className="text-sm text-text-secondary mt-1">
            {useLiveTwin
              ? "Mirrors webcam tracks and agent heatmap while Live runs — switch tabs freely."
              : "Anonymous avatars on a scale model of the activation. Scrub the timeline."}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant={showHeatmap ? "primary" : "secondary"}
            size="sm"
            icon={
              showHeatmap ? <Eye size={14} /> : <EyeOff size={14} />
            }
            onClick={() => setShowHeatmap((v) => !v)}
          >
            Heatmap
          </Button>
          <Button variant="secondary" size="sm" icon={<Layers size={14} />}>
            Layers
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-12 gap-4">
        {/* 3D scene */}
        <div className="col-span-12 lg:col-span-9">
          <div className="panel-elevated overflow-hidden">
            <div className="aspect-[16/10] relative">
              <TwinScene
                time={time}
                showHeatmap={showHeatmap}
                selectedPerson={selectedPerson}
                liveMode={useLiveTwin}
                liveAvatars={avatars}
                liveHeatmap={heatmap}
                replay={replay}
              />
              <div className="absolute top-3 left-3 flex items-center gap-2">
                <Pill variant="live">
                  <span className="live-dot" />
                  {useLiveTwin ? "Live feed" : `Replay · ${speed}×`}
                </Pill>
                <Pill variant="neutral">
                  T+{formatDuration(time)}
                </Pill>
              </div>
              <div className="absolute top-3 right-3 flex items-center gap-2 text-[10px] tabular text-text-secondary">
                <span>{activeTracks.length} visitor{activeTracks.length === 1 ? "" : "s"} in scene</span>
                <span className="text-text-faint">·</span>
                <span>orbit · pinch to zoom</span>
              </div>
            </div>

            {/* Scrubber + transport */}
            <div className="border-t border-border-hairline px-4 py-3 flex items-center gap-4">
              <div className="flex items-center gap-1.5">
                <button
                  className="w-9 h-9 rounded-full bg-bg-panel border border-border-subtle flex items-center justify-center hover:bg-bg-elevated transition-colors"
                  onClick={() => setTime(0)}
                  title="Rewind"
                >
                  <Rewind size={14} className="text-text-secondary" />
                </button>
                <button
                  className="w-11 h-11 rounded-full bg-accent flex items-center justify-center shadow-[var(--glow-green)] hover:bg-accent-bright transition-colors"
                  onClick={() => setPlaying((v) => !v)}
                  title={playing ? "Pause" : "Play"}
                >
                  {playing ? (
                    <Pause size={16} className="text-text-inverse" />
                  ) : (
                    <Play size={16} className="text-text-inverse ml-0.5" />
                  )}
                </button>
              </div>

              <div className="flex-1 flex flex-col gap-1">
                <input
                  type="range"
                  min={0}
                  max={Math.max(duration, 1)}
                  step={0.5}
                  value={time}
                  onChange={(e) => setTime(parseFloat(e.target.value))}
                  className="w-full"
                />
                <div className="flex justify-between text-[10px] tabular text-text-muted">
                  <span>{formatDuration(time)}</span>
                  <span>{formatDuration(duration)}</span>
                </div>
              </div>

              <div className="flex items-center gap-1 text-xs">
                {SPEEDS.map((s) => (
                  <button
                    key={s}
                    onClick={() => setSpeed(s)}
                    className={cn(
                      "h-8 px-2.5 rounded-md tabular border transition-colors",
                      speed === s
                        ? "bg-accent/10 border-accent/40 text-accent"
                        : "bg-transparent border-border-subtle text-text-secondary hover:border-border-strong"
                    )}
                  >
                    {s}×
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* Side panel */}
        <div className="col-span-12 lg:col-span-3 space-y-4">
          <Panel
            title="Visitors in scene"
            subtitle={`${activeTracks.length} in the room · ${replay.tracks.length} across the session · ${eventCount.toLocaleString()} events`}
            action={<Users size={14} className="text-text-muted" />}
          >
            {/* Only the people present at this moment. The old list showed five
                mock visitors with hand-written dwell totals; a real session has
                hundreds, and the useful question while scrubbing is who is in
                the room now. */}
            <ul className="space-y-1.5 max-h-[280px] overflow-y-auto">
              {activeTracks.map((p) => {
                const selected = selectedPerson === p.id;
                return (
                  <li key={p.id}>
                    <button
                      className={cn(
                        "w-full flex items-center gap-3 px-2.5 py-2 rounded-md text-left transition-colors",
                        selected
                          ? "bg-bg-elevated border border-border-subtle"
                          : "hover:bg-bg-elevated border border-transparent"
                      )}
                      onClick={() =>
                        setSelectedPerson((cur) => (cur === p.id ? null : p.id))
                      }
                    >
                      <span
                        className="w-2.5 h-2.5 rounded-full shrink-0"
                        style={{
                          background: p.color,
                          boxShadow: `0 0 10px ${p.color}`,
                        }}
                      />
                      <div className="flex-1 min-w-0">
                        <div className="text-sm tabular font-medium">{p.id}</div>
                        <div className="text-[10px] text-text-muted tabular">
                          {p.inferred ? "zone-level position" : "tracked position"}
                        </div>
                      </div>
                      {p.moving && (
                        <span className="text-[10px] tabular text-accent-cyan">
                          moving
                        </span>
                      )}
                    </button>
                  </li>
                );
              })}
              {!activeTracks.length && (
                <li className="text-xs text-text-muted px-2.5 py-2">
                  Nobody in the room at this moment.
                </li>
              )}
            </ul>
            {replay.inferredCount > 0 && (
              <p className="mt-3 text-[10px] text-text-muted leading-relaxed">
                {replay.inferredCount} of {replay.tracks.length} paths are
                zone-level: the log records which zone the person was in, not
                where they stood in it. Those avatars sit at the zone&apos;s centre
                and the line between zones is not a route anybody walked.
              </p>
            )}
            {selectedPerson && (
              <button
                onClick={() => setSelectedPerson(null)}
                className="mt-3 w-full text-xs text-text-muted inline-flex items-center justify-center gap-1.5 py-2 rounded-md border border-border-hairline hover:border-border-subtle hover:text-text-primary transition-colors"
              >
                <X size={12} />
                Clear selection
              </button>
            )}
          </Panel>

          <Panel title="Zones in twin" padded={false}>
            <ul className="px-3 py-2 space-y-1">
              {zones.map((z) => (
                <li key={z.id} className="flex items-center gap-2.5 py-1 text-xs">
                  <span
                    className="w-2 h-2 rounded-full"
                    style={{ background: z.color }}
                  />
                  <span className="flex-1 truncate">{z.name}</span>
                  <span className="text-text-muted text-[10px] uppercase tracking-[0.12em]">
                    {z.type}
                  </span>
                </li>
              ))}
            </ul>
          </Panel>

          <Panel title="Interactive surfaces" padded={false}>
            <ul className="px-3 py-2 space-y-1">
              {surfaces.map((s) => (
                <li
                  key={s.id}
                  className="flex items-center gap-2.5 py-1 text-xs"
                >
                  <span
                    className={`w-2 h-2 rounded-full ${
                      s.active ? "bg-accent-cyan" : "bg-text-faint"
                    }`}
                    style={
                      s.active
                        ? { boxShadow: "0 0 10px var(--accent-cyan)" }
                        : undefined
                    }
                  />
                  <span className="flex-1 truncate">{s.label}</span>
                  <span className="text-text-muted tabular text-[10px]">
                    {s.triggerCount}
                  </span>
                </li>
              ))}
            </ul>
          </Panel>
        </div>
      </div>
    </div>
  );
}

function SceneLoading() {
  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-text-muted">
      <div className="flex gap-1.5">
        <span className="w-2 h-2 rounded-full bg-accent animate-bounce [animation-delay:-0.3s]" />
        <span className="w-2 h-2 rounded-full bg-accent-cyan animate-bounce [animation-delay:-0.15s]" />
        <span className="w-2 h-2 rounded-full bg-accent-violet animate-bounce" />
      </div>
      <span className="text-[11px] tabular tracking-[0.18em] uppercase">
        loading twin…
      </span>
    </div>
  );
}

function TwinLoading() {
  return (
    <div className="p-5 max-w-[1600px] mx-auto">
      <EmptyState
        variant="page"
        icon={<Box size={20} />}
        title="Reading the session's paths…"
        hint="Pulling this session's events so the twin can replay them."
      />
    </div>
  );
}

function TwinEmptyState() {
  const active = useActiveSession();
  return (
    <div className="p-5 max-w-[1400px] mx-auto space-y-6">
      <EmptyState
        variant="page"
        icon={<Box size={26} strokeWidth={1.8} />}
        title="Your twin is ready, the data isn't — yet."
        hint={
          <>
            The 3D replay rebuilds itself from anonymous position tracks. Once
            cameras start recording, you&apos;ll be able to scrub the timeline,
            isolate visitors, and watch your space through time.
          </>
        }
        cta={{ href: "/live", label: "Open live & start the sensor" }}
      />

      {/* Show their configured layout as a calm "ready and waiting" preview */}
      <div className="grid md:grid-cols-2 gap-5 mt-12">
        <Panel
          title="Zones in this session"
          subtitle={`${active.zones.length} configured`}
          padded={false}
        >
          <ul className="px-3 py-2 space-y-1">
            {active.zones.map((z) => (
              <li key={z.id} className="flex items-center gap-2.5 py-1.5 text-sm">
                <span
                  className="w-2 h-2 rounded-full shrink-0"
                  style={{ background: z.color }}
                />
                <span className="flex-1 truncate">{z.name}</span>
                <span className="text-text-muted text-[10px] uppercase tracking-[0.12em]">
                  {z.type}
                </span>
              </li>
            ))}
            {active.zones.length === 0 && (
              <li className="py-4 px-3 text-center text-sm text-text-muted">
                No zones configured.
              </li>
            )}
          </ul>
        </Panel>

        <Panel
          title="Touchpoints in this session"
          subtitle={`${active.touchpoints.length} configured`}
          padded={false}
        >
          <ul className="px-3 py-2 space-y-1">
            {active.touchpoints.map((t) => {
              const zone = active.zones.find((z) => z.id === t.zoneId);
              return (
                <li
                  key={t.id}
                  className="flex items-center gap-2.5 py-1.5 text-sm"
                >
                  <span
                    className="w-2 h-2 rounded-full shrink-0"
                    style={{
                      background: zone?.color ?? "#42faa1",
                      boxShadow: `0 0 10px ${zone?.color ?? "#42faa1"}`,
                    }}
                  />
                  <span className="flex-1 truncate">{t.name}</span>
                  <span className="text-text-muted text-[10px] uppercase tracking-[0.12em]">
                    {zone?.name ?? "—"}
                  </span>
                </li>
              );
            })}
            {active.touchpoints.length === 0 && (
              <li className="py-4 px-3 text-center text-sm text-text-muted">
                No touchpoints configured.
              </li>
            )}
          </ul>
        </Panel>
      </div>
    </div>
  );
}
