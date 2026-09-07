"use client";

/**
 * Client-side session store.
 *
 * Uses `useSyncExternalStore` so it is SSR-safe and avoids the React 19
 * lint rule against `setState` inside effects. Persists to localStorage.
 *
 * The demo session (Lagos Showroom) is always present and is the default
 * active session on first load.
 */

import { useCallback, useEffect, useSyncExternalStore } from "react";

import { DEMO_SESSION } from "@/lib/mock/session";

import type { Session, SessionDraft, SessionStatus } from "./types";

const LS_KEY = "realmspace.store.v1";

interface PersistShape {
  sessions: Session[];
  activeId: string | null;
}

interface State extends PersistShape {
  hydrated: boolean;
}

// ── Singleton state ───────────────────────────────────────────────────────

let state: State = {
  sessions: [DEMO_SESSION],
  activeId: DEMO_SESSION.id,
  hydrated: false,
};

const listeners = new Set<() => void>();

function emit() {
  for (const l of listeners) l();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): State {
  return state;
}

// Same snapshot on server every render to satisfy useSyncExternalStore
const SERVER_STATE: State = {
  sessions: [DEMO_SESSION],
  activeId: DEMO_SESSION.id,
  hydrated: false,
};
function getServerSnapshot(): State {
  return SERVER_STATE;
}

function setState(updater: (s: State) => State) {
  state = updater(state);
  persist();
  emit();
}

function persist() {
  if (typeof window === "undefined") return;
  // Don't persist the demo session — it lives in code and is re-seeded
  // on every load. Persist user-created sessions only.
  const payload: PersistShape = {
    sessions: state.sessions.filter((s) => !s.isDemo),
    activeId: state.activeId,
  };
  try {
    window.localStorage.setItem(LS_KEY, JSON.stringify(payload));
  } catch {
    /* quota / private mode — ignore */
  }
}

let hasHydrated = false;
function hydrate() {
  if (hasHydrated || typeof window === "undefined") return;
  hasHydrated = true;
  try {
    const raw = window.localStorage.getItem(LS_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as PersistShape;
      const userSessions = (parsed.sessions ?? []).filter(Boolean);
      const allSessions = [DEMO_SESSION, ...userSessions];
      const activeId =
        parsed.activeId && allSessions.some((s) => s.id === parsed.activeId)
          ? parsed.activeId
          : DEMO_SESSION.id;
      state = { sessions: allSessions, activeId, hydrated: true };
    } else {
      state = { ...state, hydrated: true };
    }
  } catch {
    state = { ...state, hydrated: true };
  }
  emit();
}

// ── Actions ───────────────────────────────────────────────────────────────

function uid(prefix = "ses") {
  return `${prefix}_${Math.random().toString(36).slice(2, 9)}${Date.now().toString(36).slice(-4)}`;
}

function createSession(draft: SessionDraft, opts?: { activate?: boolean }) {
  const id = uid();
  const now = new Date().toISOString();
  // If the start time is in the past or right now, mark as live; else scheduled.
  const startMs = Date.parse(draft.startAt);
  const isLiveNow = !Number.isNaN(startMs) && startMs <= Date.now();
  const status: SessionStatus = isLiveNow ? "live" : "scheduled";
  const next: Session = {
    ...draft,
    id,
    isDemo: false,
    status,
    createdAt: now,
    startedAt: isLiveNow ? now : undefined,
  };
  setState((s) => ({
    ...s,
    sessions: [next, ...s.sessions],
    activeId: opts?.activate === false ? s.activeId : id,
  }));
  return next;
}

function updateSession(id: string, patch: Partial<Session>) {
  setState((s) => ({
    ...s,
    sessions: s.sessions.map((sess) =>
      sess.id === id ? { ...sess, ...patch } : sess
    ),
  }));
}

function deleteSession(id: string) {
  setState((s) => {
    const sessions = s.sessions.filter((sess) => sess.id !== id);
    const activeId =
      s.activeId === id ? sessions[0]?.id ?? DEMO_SESSION.id : s.activeId;
    return { ...s, sessions, activeId };
  });
}

function setActive(id: string) {
  setState((s) => ({ ...s, activeId: id }));
}

function startSession(id: string) {
  const now = new Date().toISOString();
  updateSession(id, { status: "live", startedAt: now });
}

function pauseSession(id: string) {
  updateSession(id, { status: "paused" });
}

function endSession(id: string) {
  const now = new Date().toISOString();
  updateSession(id, { status: "completed", endedAt: now, endAt: now });
}

export const sessionActions = {
  createSession,
  updateSession,
  deleteSession,
  setActive,
  startSession,
  pauseSession,
  endSession,
};

// ── Hooks ────────────────────────────────────────────────────────────────

function useStore<T>(selector: (s: State) => T): T {
  useEffect(() => {
    // Keep the server snapshot for the first client render. Apply persisted
    // sessions after React has committed hydration to avoid a text mismatch.
    const id = window.setTimeout(hydrate, 0);
    return () => window.clearTimeout(id);
  }, []);
  return useSyncExternalStore(
    subscribe,
    useCallback(() => selector(getSnapshot()), [selector]),
    useCallback(() => selector(getServerSnapshot()), [selector])
  );
}

export function useSessions(): Session[] {
  return useStore((s) => s.sessions);
}

export function useActiveSessionId(): string | null {
  return useStore((s) => s.activeId);
}

export function useActiveSession(): Session {
  return useStore((s) => {
    const found = s.sessions.find((sess) => sess.id === s.activeId);
    return found ?? s.sessions[0] ?? DEMO_SESSION;
  });
}

export function useSessionById(id: string | null | undefined): Session | undefined {
  return useStore((s) =>
    id ? s.sessions.find((sess) => sess.id === id) : undefined
  );
}

export function useHydrated(): boolean {
  return useStore((s) => s.hydrated);
}
