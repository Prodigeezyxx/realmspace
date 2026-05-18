"use client";

import { useCallback, useSyncExternalStore } from "react";

import { processFrameTracks } from "@/lib/agent-engine";
import type { HeatmapOutput } from "@/skills/heatmap";
import type { TwinAvatarDelta } from "@/skills/twin-sync";

import {
  getModelLoadStage,
  preloadDetectionModel,
  subscribeModelLoadStage,
} from "./model-cache";
import {
  buildHeatmapFromTracks,
  emitTwinFromTracks,
  SENSOR_H,
  SENSOR_W,
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
    }
  });

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
    void processFrameTracks(s.activeTracks, now, SENSOR_W, SENSOR_H);
  }

  let twinAvatars = state.twinAvatars;
  let heatmap = state.heatmap;
  if (state.status === "running") {
    if (now - lastTwinEmit > 200) {
      lastTwinEmit = now;
      twinAvatars = tracksToAvatars(s.activeTracks);
      emitTwinFromTracks(s.activeTracks);
    }
    if (now - lastHeatmapEmit > 1000) {
      lastHeatmapEmit = now;
      heatmap = buildHeatmapFromTracks(s.activeTracks);
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
