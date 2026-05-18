"use client";

import { useCallback, useSyncExternalStore } from "react";

import { processFrameTracks } from "@/lib/agent-engine";

import {
  getModelLoadStage,
  preloadDetectionModel,
  subscribeModelLoadStage,
} from "./model-cache";
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
  liveEvents: LiveEvent[];
  peopleHistory: number[];
}

let state: State = {
  status: "idle",
  errorMsg: null,
  modelLoadStage: "idle",
  stats: null,
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
    void processFrameTracks(s.activeTracks, now, 1280, 720);
  }

  state = { ...state, stats: s, liveEvents, peopleHistory };
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
    state = {
      ...state,
      stats: null,
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
