"use client";

/**
 * Client-side session store.
 *
 * Uses `useSyncExternalStore` so it is SSR-safe and avoids the React 19
 * lint rule against `setState` inside effects. Persists to localStorage.
 *
 * The demo session (Lagos Showroom) is always present and is the default
 * active session on first load.
 *
 * ## Partitioned by tenant
 *
 * The storage key carries the tenant, and it has to. This was a single global
 * key, so every organisation signing in on the same machine shared one list of
 * activations: a newly signed-up operator's very first screen showed another
 * client's sessions — names, venues, zone and camera counts, footfall targets.
 * Found by the Phase 6 acceptance's isolation pass, on a browser that had
 * previously been the demo tenant.
 *
 * No server data ever crossed: events, the graph and every figure on a report
 * come from the backend, where the tenant is derived from a verified credential
 * and Postgres RLS fails closed. What leaked was this browser's own list, which
 * is exactly the class of thing `bus/log.ts` already partitions per
 * `(tenant, session)`.
 *
 * The verified tenant arrives *after* first render (`tenant/context.ts` says so
 * in as many words, which is why it is subscribable), so the store re-reads
 * when it lands rather than hydrating once and keeping whatever it guessed.
 */

import { useCallback, useSyncExternalStore } from "react";

import { DEMO_SESSION } from "@/lib/mock/session";
import { getTenantId, subscribeTenantId } from "@/lib/tenant/context";

import type { Session, SessionDraft, SessionStatus } from "./types";

const LS_PREFIX = "realmspace.store.v1";

/**
 * Where this tenant's sessions live.
 *
 * The unsuffixed key is deliberately not reused for the default tenant. It
 * holds whatever the shared store accumulated before this partitioning existed
 * — sessions belonging to whoever used the browser — and adopting it for one
 * organisation would hand that pile to them.
 */
function storageKey(): string {
  return `${LS_PREFIX}:${getTenantId()}`;
}

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
    window.localStorage.setItem(storageKey(), JSON.stringify(payload));
  } catch {
    /* quota / private mode — ignore */
  }
}

let hydratedFor: string | null = null;
function hydrate() {
  if (typeof window === "undefined") return;
  // Keyed on the tenant rather than a boolean: the first call runs against the
  // guessed tenant, and the verified one arriving later has to re-read.
  if (hydratedFor === getTenantId()) return;
  hydratedFor = getTenantId();
  state = {
    sessions: [DEMO_SESSION],
    activeId: DEMO_SESSION.id,
    hydrated: false,
  };
  try {
    const raw = window.localStorage.getItem(storageKey());
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

// Hydrate as soon as this module is imported on the client, and again when the
// backend says which organisation this actually is. Without the second the
// first load of any tenant but the stored one reads somebody else's list.
if (typeof window !== "undefined") {
  hydrate();
  subscribeTenantId(hydrate);
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
