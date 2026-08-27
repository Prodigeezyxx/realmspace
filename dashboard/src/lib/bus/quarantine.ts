/**
 * realmspace — the events this browser refused, and why.
 *
 * The client-side counterpart of the backend's `dead_letter` table. When an
 * event arrives that `contracts/validate.ts` says no reader can use, it is kept
 * out of the durable log and recorded here instead — because the alternatives
 * are both worse than a refusal:
 *
 *   - accepting it puts a phantom visitor in Reach and `NaN` in every dwell
 *     figure, which is what shipped before this existed;
 *   - dropping it silently means a client's report is short of events and
 *     nothing anywhere says so.
 *
 * ## The payload is deliberately not stored
 *
 * Only the envelope and the reasons. `consent.captured` and half the taxonomy
 * are PII-classified in `contracts/events.ts`, and localStorage is a place the
 * Article 17 erasure job (`backend/app/consumers/erasure.py`) cannot reach — so
 * a refused payload written here would be a copy of somebody's name that no
 * withdrawal could ever remove. The reasons name the *fields*, never the values.
 *
 * The real event is still on the server's log, which is where anybody
 * investigating one should look; `/ops` shows the two queues side by side.
 */

import type { RealmEventType } from "@/lib/contracts";

const STORAGE_PREFIX = "rs:quarantine:";
/** Enough to see a pattern, not enough to fill a browser's storage quota. */
const MAX_PER_PARTITION = 100;

export interface RefusedEvent {
  eventId: string;
  /** The server's seq, where there was one. 0 for a locally-produced event. */
  seq: number;
  type: RealmEventType;
  /** Field-level, never value-level — see the note above. */
  reasons: string[];
  /** When the event says it happened, ms epoch. */
  occurredAt: number;
  /** When this browser refused it, ms epoch. */
  refusedAt: number;
}

type Listener = () => void;

const listeners = new Set<Listener>();
/**
 * Bumped on every change, and the whole of what a React reader subscribes to.
 *
 * `useSyncExternalStore` needs a snapshot it can compare by identity, and the
 * list itself is rebuilt from localStorage on every read — so the counter is
 * the snapshot and the list is derived from it. Same shape as
 * `hooks/useIsHydrated.ts`, and for the same reason: no setState in an effect.
 */
let version = 0;

function changed() {
  version++;
  listeners.forEach((fn) => {
    try {
      fn();
    } catch {
      /* a listener's error is not the producer's problem */
    }
  });
}

/** The subscribable snapshot. See `version` above. */
export function getRefusalVersion(): number {
  return version;
}

/** Last list handed out, so an unchanged store returns an unchanged reference. */
let snapshot: { tenantId: string; version: number; value: RefusedSession[] } | null =
  null;

export interface RefusedSession {
  sessionId: string;
  refused: RefusedEvent[];
}

const NO_REFUSALS: RefusedSession[] = [];

/**
 * `listRefusedSessions`, memoised on `(tenantId, version)`.
 *
 * `useSyncExternalStore` re-renders whenever the snapshot differs by identity,
 * and building the list from localStorage returns a new array every time — so
 * the raw reader would loop forever. This is the reference-stable form of it.
 */
export function refusalSnapshot(tenantId: string): RefusedSession[] {
  if (!tenantId) return NO_REFUSALS;
  if (snapshot && snapshot.tenantId === tenantId && snapshot.version === version) {
    return snapshot.value;
  }
  const value = listRefusedSessions(tenantId);
  snapshot = { tenantId, version, value };
  return value;
}

function hasWindow() {
  return typeof window !== "undefined";
}

function storageKey(tenantId: string, sessionId: string) {
  return `${STORAGE_PREFIX}${tenantId}::${sessionId}`;
}

/** Everything this browser has refused for one activation, oldest first. */
export function readRefused(tenantId: string, sessionId: string): RefusedEvent[] {
  if (!hasWindow()) return [];
  try {
    const raw = window.localStorage.getItem(storageKey(tenantId, sessionId));
    return raw ? (JSON.parse(raw) as RefusedEvent[]) : [];
  } catch {
    return [];
  }
}

/**
 * Record a refusal. Idempotent on `eventId`, for the same reason the log is: a
 * reconnect replays from a cursor and a backfill overlaps the socket, so the
 * same bad event arrives more than once and must not be counted twice.
 */
export function recordRefusal(
  tenantId: string,
  sessionId: string,
  entry: RefusedEvent
): void {
  if (!hasWindow()) return;
  const existing = readRefused(tenantId, sessionId);
  if (existing.some((e) => e.eventId === entry.eventId)) return;

  const next = [...existing, entry].slice(-MAX_PER_PARTITION);
  try {
    window.localStorage.setItem(storageKey(tenantId, sessionId), JSON.stringify(next));
  } catch {
    /* storage full — a refusal must never break the read that found it */
  }
  changed();
}

/** How many, and of what. What `/report` and `/ops` both need to say something. */
export function summariseRefusals(
  tenantId: string,
  sessionId: string
): { count: number; byType: { type: RealmEventType; count: number }[] } {
  const all = readRefused(tenantId, sessionId);
  const counts = new Map<RealmEventType, number>();
  for (const e of all) counts.set(e.type, (counts.get(e.type) ?? 0) + 1);
  return {
    count: all.length,
    byType: [...counts.entries()]
      .map(([type, count]) => ({ type, count }))
      .sort((a, b) => b.count - a.count),
  };
}

/**
 * Every activation this browser has refused something for, most recent first.
 *
 * `/ops` is tenant-scoped, not session-scoped — the backend's dead-letter queue
 * beside it is read the same way — so the quarantine has to be enumerable
 * rather than asked about one session at a time.
 */
export function listRefusedSessions(tenantId: string): RefusedSession[] {
  if (!hasWindow()) return [];
  const prefix = `${STORAGE_PREFIX}${tenantId}::`;
  const out: RefusedSession[] = [];
  try {
    for (let i = 0; i < window.localStorage.length; i++) {
      const k = window.localStorage.key(i);
      if (!k || !k.startsWith(prefix)) continue;
      const sessionId = k.slice(prefix.length);
      const refused = readRefused(tenantId, sessionId);
      if (refused.length) out.push({ sessionId, refused });
    }
  } catch {
    return [];
  }
  return out.sort(
    (a, b) =>
      Math.max(...b.refused.map((e) => e.refusedAt)) -
      Math.max(...a.refused.map((e) => e.refusedAt))
  );
}

/** Subscribe to refusals across all partitions. Returns unsubscribe. */
export function subscribeRefusals(fn: Listener): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** Test/dev helper: forget one partition's refusals. */
export function clearRefusals(tenantId: string, sessionId: string): void {
  if (!hasWindow()) return;
  window.localStorage.removeItem(storageKey(tenantId, sessionId));
  changed();
}
