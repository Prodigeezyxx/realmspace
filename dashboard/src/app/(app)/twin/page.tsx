"use client";

import {
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
import { Panel } from "@/components/ui/Panel";
import { Pill } from "@/components/ui/Pill";
import { peopleTracks } from "@/lib/mock/people";
import { surfaces, zones } from "@/lib/mock/session";
import { cn, formatDuration } from "@/lib/utils";

const TwinScene = dynamic(
  () => import("@/components/twin/TwinScene").then((m) => m.TwinScene),
  { ssr: false, loading: () => <SceneLoading /> }
);

const SESSION_DURATION = 620; // seconds covered by mock tracks
const SPEEDS = [0.5, 1, 2, 4];

export default function TwinPage() {
  const [time, setTime] = useState(180);
  const [playing, setPlaying] = useState(true);
  const [speed, setSpeed] = useState(1);
  const [showHeatmap, setShowHeatmap] = useState(true);
  const [selectedPerson, setSelectedPerson] = useState<string | null>(null);

  useEffect(() => {
    if (!playing) return;
    const id = setInterval(() => {
      setTime((t) => {
        const next = t + 0.1 * speed;
        return next > SESSION_DURATION ? 0 : next;
      });
    }, 100);
    return () => clearInterval(id);
  }, [playing, speed]);

  const activeTracks = useMemo(
    () =>
      peopleTracks.filter((p) => {
        const start = p.waypoints[0][2];
        const end = p.waypoints[p.waypoints.length - 1][2];
        return time >= start && time <= end;
      }),
    [time]
  );

  return (
    <div className="p-5 max-w-[1600px] mx-auto space-y-4">
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <Pill variant="violet" className="mb-2">
            <Sparkles size={11} />
            Digital twin · replay
          </Pill>
          <h1 className="text-2xl font-semibold tracking-tight">
            Pavilion No. 7 — 3D replay
          </h1>
          <p className="text-sm text-text-secondary mt-1">
            Anonymous avatars on a scale model of the activation. Scrub the
            timeline. Click a visitor to follow their path.
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
              />
              <div className="absolute top-3 left-3 flex items-center gap-2">
                <Pill variant="live">
                  <span className="live-dot" />
                  Replay · {speed}×
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
                  className="w-11 h-11 rounded-full bg-accent-blue flex items-center justify-center shadow-[var(--glow-blue)] hover:bg-accent-blue-bright transition-colors"
                  onClick={() => setPlaying((v) => !v)}
                  title={playing ? "Pause" : "Play"}
                >
                  {playing ? (
                    <Pause size={16} className="text-white" />
                  ) : (
                    <Play size={16} className="text-white ml-0.5" />
                  )}
                </button>
              </div>

              <div className="flex-1 flex flex-col gap-1">
                <input
                  type="range"
                  min={0}
                  max={SESSION_DURATION}
                  step={0.5}
                  value={time}
                  onChange={(e) => setTime(parseFloat(e.target.value))}
                  className="w-full accent-accent-blue"
                />
                <div className="flex justify-between text-[10px] tabular text-text-muted">
                  <span>{formatDuration(time)}</span>
                  <span>{formatDuration(SESSION_DURATION)}</span>
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
                        ? "bg-accent-blue/10 border-accent-blue/40 text-accent-blue"
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
            subtitle={`${activeTracks.length} active · ${peopleTracks.length} total`}
            action={<Users size={14} className="text-text-muted" />}
          >
            <ul className="space-y-1.5">
              {peopleTracks.map((p) => {
                const inScene = activeTracks.some((a) => a.id === p.id);
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
                          boxShadow: inScene ? `0 0 10px ${p.color}` : undefined,
                          opacity: inScene ? 1 : 0.3,
                        }}
                      />
                      <div className="flex-1 min-w-0">
                        <div className="text-sm tabular font-medium">{p.id}</div>
                        <div className="text-[10px] text-text-muted tabular">
                          {formatDuration(p.totalDwellSeconds)} · {p.zonesVisited.length} zones · {p.surfacesTriggered.length} surfaces
                        </div>
                      </div>
                      <span className="text-[10px] tabular text-accent-cyan">
                        {Math.round(p.attentionScore * 100)}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
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
        <span className="w-2 h-2 rounded-full bg-accent-blue animate-bounce [animation-delay:-0.3s]" />
        <span className="w-2 h-2 rounded-full bg-accent-cyan animate-bounce [animation-delay:-0.15s]" />
        <span className="w-2 h-2 rounded-full bg-accent-violet animate-bounce" />
      </div>
      <span className="text-[11px] tabular tracking-[0.18em] uppercase">
        loading twin…
      </span>
    </div>
  );
}
