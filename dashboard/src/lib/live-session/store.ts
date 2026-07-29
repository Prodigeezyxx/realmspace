"use client";

import { useCallback, useSyncExternalStore } from "react";

import { processFrameTracks } from "@/lib/agent-engine";
import { emit as busEmit } from "@/lib/bus";
import type { HeatmapOutput } from "@/skills/heatmap";
import type { TwinAvatarDelta } from "@/skills/twin-sync";

import { spatialDeriver } from "./spatial-deriver";

import {
  getModelLoadStage,
  preloadDetectionModel,
  subscribeModelLoadStage,
} from "./model-cache";
import {
  buildHeatmapFromTracks,
  emitTwinFromTracks,
  tracksToAvatars,
} from "./twin-emit";
import type { DetectorStats } from "./types";

export type LiveDetectorStatus =
  | "idle"
  | "requesting"
  | "loading-model"
  | "running"
  | "denied"
  | "error";

export interface LiveEvent {
  id: string;
  ts: number;
  text: string;
  color: string;
}

interface State {
  status: LiveDetectorStatus;
  errorMsg: string | null;
  modelLoadStage: string;
  stats: DetectorStats | null;
  twinAvatars: TwinAvatarDelta[];
  heatmap: HeatmapOutput | null;
  liveEvents: LiveEvent[];
  peopleHistory: number[];
}

let state: State = {
  status: "idle",
  errorMsg: null,
  modelLoadStage: "idle",
  stats: null,
  twinAvatars: [],
  heatmap: null,
  liveEvents: [],
  peopleHistory: [],
};

const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot() {
  return state;
}

const lastIds = new Set<number>();
let lastHistoryTick = 0;
let lastAgentTick = 0;
let lastTwinEmit = 0;
let lastHeatmapEmit = 0;
let lastBusTick = 0;

function ingestStats(s: DetectorStats) {
  const currentIds = new Set(s.activeTracks.map((t) => t.id));
  let liveEvents = state.liveEvents;

  s.activeTracks.forEach((t) => {
    if (!lastIds.has(t.id)) {
      liveEvents = [
        {
          id: `enter_${t.id}_${Date.now()}`,
          ts: Date.now(),
          text: `${t.label} entered the frame`,
          color: t.color,
        },
        ...liveEvents,
      ].slice(0, 40);
      try {
        busEmit("perception.detection", {
          anonId: t.label,
          bbox: t.bbox,
          confidence: t.score,
        });
      } catch {
        /* bus must never break the detector loop */
      }
    }
  });

  // Heartbeat detections ~2/sec so the edge graph stays warm while tracks live
  const nowBus = Date.now();
  if (s.activeTracks.length && nowBus - lastBusTick > 500) {
    lastBusTick = nowBus;
    for (const t of s.activeTracks.slice(0, 8)) {
      try {
        busEmit("perception.detection", {
          anonId: t.label,
          bbox: t.bbox,
          confidence: t.score,
        });
      } catch {
        /* ignore */
      }
    }
  }

  lastIds.forEach((id) => {
    if (!currentIds.has(id)) {
      liveEvents = [
        {
          id: `exit_${id}_${Date.now()}`,
          ts: Date.now(),
          text: `P-${id.toString().padStart(3, "0")} left the frame`,
          color: "#86868b",
        },
        ...liveEvents,
      ].slice(0, 40);
    }
  });

  lastIds.clear();
  currentIds.forEach((id) => lastIds.add(id));

  const now = Date.now();

  // Spatial deriver: tracked persons + zone polygons → spatial.* bus events.
  // Transitions also surface in the live event feed.
  if (state.status === "running") {
    try {
      const transitions = spatialDeriver.update(
        s.tracks,
        s.frameWidth,
        s.frameHeight,
        now
      );
      transitions.forEach((tr, i) => {
        const text =
          tr.kind === "enter"
            ? `${tr.anonId} entered ${tr.zoneName}`
            : tr.kind === "exit"
              ? `${tr.anonId} left ${tr.zoneName}`
              : tr.kind === "dwell"
                ? `${tr.anonId} dwelled in ${tr.zoneName} (${tr.durationSec}s)`
                : `${tr.anonId} passed by ${tr.zoneName}`;
        const color =
          tr.kind === "enter"
            ? "#00d4aa"
            : tr.kind === "dwell"
              ? "#ffc83d"
              : tr.kind === "passby"
                ? "#b66bff"
                : "#86868b";
        liveEvents = [
          { id: `zone_${tr.kind}_${tr.anonId}_${now}_${i}`, ts: now, text, color },
          ...liveEvents,
        ].slice(0, 40);
      });
    } catch {
      /* deriver must never break the detector loop */
    }
  }
  let peopleHistory = state.peopleHistory;
  if (now - lastHistoryTick > 500) {
    lastHistoryTick = now;
    peopleHistory = [...peopleHistory.slice(-59), s.activeTracks.length];
  }

  if (
    now - lastAgentTick > 1000 &&
    s.activeTracks.length > 0 &&
    state.status === "running"
  ) {
    lastAgentTick = now;
    void processFrameTracks(
      s.activeTracks,
      now,
      s.frameWidth,
      s.frameHeight
    );
  }

  const fw = s.frameWidth;
  const fh = s.frameHeight;
  let twinAvatars = state.twinAvatars;
  let heatmap = state.heatmap;
  if (state.status === "running") {
    if (now - lastTwinEmit > 200) {
      lastTwinEmit = now;
      twinAvatars = tracksToAvatars(s.activeTracks, fw, fh);
      emitTwinFromTracks(s.activeTracks, fw, fh);
    }
    if (now - lastHeatmapEmit > 1000) {
      lastHeatmapEmit = now;
      heatmap = buildHeatmapFromTracks(s.activeTracks, fw, fh);
    }
  }

  state = {
    ...state,
    stats: s,
    twinAvatars,
    heatmap,
    liveEvents,
    peopleHistory,
  };
  emit();
}

export const liveSessionActions = {
  setStatus(status: LiveDetectorStatus, errorMsg: string | null = null) {
    state = { ...state, status, errorMsg };
    emit();
  },

  onStats(s: DetectorStats) {
    ingestStats(s);
  },

  resetSession() {
    // Close out open zone memberships as exits/dwells before wiping state, so
    // the final visits of the session are not lost (session bounds hygiene).
    try {
      spatialDeriver.flushAll("session_end");
    } catch {
      /* ignore */
    }
    lastIds.clear();
    lastHistoryTick = 0;
    lastAgentTick = 0;
    lastTwinEmit = 0;
    lastHeatmapEmit = 0;
    state = {
      ...state,
      stats: null,
      twinAvatars: [],
      heatmap: null,
      liveEvents: [],
      peopleHistory: [],
    };
    emit();
  },

  async ensureModelPreloaded() {
    state = { ...state, modelLoadStage: getModelLoadStage() };
    emit();
    await preloadDetectionModel();
    state = { ...state, modelLoadStage: getModelLoadStage() };
    emit();
  },
};

if (typeof window !== "undefined") {
  subscribeModelLoadStage(() => {
    state = { ...state, modelLoadStage: getModelLoadStage() };
    emit();
  });
}

export function useLiveSession<T>(selector: (s: State) => T): T {
  return useSyncExternalStore(
    subscribe,
    useCallback(() => selector(getSnapshot()), [selector]),
    useCallback(() => selector(getSnapshot()), [selector])
  );
}

export function useLiveSessionStore(): State {
  return useLiveSession((s) => s);
}
