/**
 * realmspace — durable append-only event log (client-side implementation).
 *
 * This is the browser/prototype implementation of the bus described in
 * docs/event-bus-spec.md: append-only, idempotent (by eventId), replayable
 * (from a seq cursor), tenant/session scoped. It persists to localStorage so
 * the log survives reloads — the client analog of the Postgres `event_log`.
 *
 * The public surface (append / read / replay / subscribe / cursors) is
 * deliberately identical in spirit to what the FastAPI backend will expose, so
 * swapping the storage engine later needs no caller changes.
 */

import type {
  RealmEvent,
  RealmEventInput,
  RealmEventPayload,
  RealmEventType,
} from "@/lib/contracts";

const STORAGE_PREFIX = "rs:eventlog:";
const CURSOR_PREFIX = "rs:cursor:";
const MAX_PERSISTED = 5000; // ring cap for the browser log

type Subscriber = (e: RealmEvent) => void;

interface Partition {
  events: RealmEvent[];
  seq: number;
  seenIds: Set<string>;
}

const partitions = new Map<string, Partition>();
const subscribers = new Set<Subscriber>();

function key(tenantId: string, sessionId: string) {
  return `${tenantId}::${sessionId}`;
}
function storageKey(tenantId: string, sessionId: string) {
  return `${STORAGE_PREFIX}${key(tenantId, sessionId)}`;
}

function hasWindow() {
  return typeof window !== "undefined";
}

function loadPartition(tenantId: string, sessionId: string): Partition {
  const k = key(tenantId, sessionId);
  const cached = partitions.get(k);
  if (cached) return cached;

  let events: RealmEvent[] = [];
  if (hasWindow()) {
    try {
      const raw = window.localStorage.getItem(storageKey(tenantId, sessionId));
      if (raw) events = JSON.parse(raw) as RealmEvent[];
    } catch {
      events = [];
    }
  }
  const seq = events.reduce((m, e) => Math.max(m, e.seq), 0);
  const part: Partition = {
    events,
    seq,
    seenIds: new Set(events.map((e) => e.eventId)),
  };
  partitions.set(k, part);
  return part;
}

function persist(tenantId: string, sessionId: string, part: Partition) {
  if (!hasWindow()) return;
  try {
    const trimmed =
      part.events.length > MAX_PERSISTED
        ? part.events.slice(part.events.length - MAX_PERSISTED)
        : part.events;
    window.localStorage.setItem(
      storageKey(tenantId, sessionId),
      JSON.stringify(trimmed)
    );
  } catch {
    /* storage full / unavailable — non-fatal for the prototype */
  }
}

function genId(): string {
  if (hasWindow() && "crypto" in window && "randomUUID" in window.crypto) {
    return window.crypto.randomUUID();
  }
  return `ev_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
}

/**
 * Append an event to the log. Idempotent: if `eventId` was already appended to
 * this partition, this is a no-op and returns the existing event.
 */
export function append<P = RealmEventPayload>(
  input: RealmEventInput<P>
): RealmEvent<P> {
  const part = loadPartition(input.tenantId, input.sessionId);
  const eventId = input.eventId ?? genId();

  if (part.seenIds.has(eventId)) {
    return part.events.find((e) => e.eventId === eventId) as RealmEvent<P>;
  }

  const now = Date.now();
  const event: RealmEvent<P> = {
    seq: ++part.seq,
    eventId,
    tenantId: input.tenantId,
    sessionId: input.sessionId,
    type: input.type,
    payload: input.payload,
    occurredAt: input.occurredAt ?? now,
    recordedAt: now,
  };

  part.events.push(event as RealmEvent);
  part.seenIds.add(eventId);
  persist(input.tenantId, input.sessionId, part);

  // fan out to live subscribers (idempotent-safe: only fires on real append)
  subscribers.forEach((s) => {
    try {
      s(event as RealmEvent);
    } catch {
      /* subscriber errors never break the producer */
    }
  });

  return event;
}

/** Read events after a seq cursor (0 = from the beginning), optionally filtered. */
export function read(
  tenantId: string,
  sessionId: string,
  opts: { afterSeq?: number; types?: RealmEventType[] } = {}
): RealmEvent[] {
  const part = loadPartition(tenantId, sessionId);
  const after = opts.afterSeq ?? 0;
  let out = part.events.filter((e) => e.seq > after);
  if (opts.types && opts.types.length) {
    const set = new Set(opts.types);
    out = out.filter((e) => set.has(e.type));
  }
  return out;
}

/** All events for a partition (convenience). */
export function readAll(tenantId: string, sessionId: string): RealmEvent[] {
  return loadPartition(tenantId, sessionId).events.slice();
}

/** Partitions recorded on this device for a tenant (for replay pickers). */
export function listLocalSessions(tenantId: string) {
  if (!hasWindow()) return [];
  const prefix = `${STORAGE_PREFIX}${tenantId}::`;
  const out: { sessionId: string; eventCount: number; firstAt: number; lastAt: number }[] = [];
  for (let i = 0; i < window.localStorage.length; i++) {
    const k = window.localStorage.key(i);
    if (!k?.startsWith(prefix)) continue;
    const events = loadPartition(tenantId, k.slice(prefix.length)).events;
    if (events.length) {
      out.push({
        sessionId: k.slice(prefix.length),
        eventCount: events.length,
        firstAt: Math.min(...events.map((e) => e.occurredAt)),
        lastAt: Math.max(...events.map((e) => e.occurredAt)),
      });
    }
  }
  return out;
}

/** Current head seq for a partition. */
export function headSeq(tenantId: string, sessionId: string): number {
  return loadPartition(tenantId, sessionId).seq;
}

/**
 * Replay a partition from a cursor through a handler, then return the new head
 * seq. This is exactly how a durable consumer catches up after being offline.
 */
export function replay(
  tenantId: string,
  sessionId: string,
  handler: (e: RealmEvent) => void,
  fromSeq = 0
): number {
  const events = read(tenantId, sessionId, { afterSeq: fromSeq });
  for (const e of events) {
    try {
      handler(e);
    } catch {
      /* consumer errors don't halt replay of the rest */
    }
  }
  return headSeq(tenantId, sessionId);
}

/* ── named consumer cursors (durable catch-up, mirrors consumer_cursor) ── */

function cursorKey(consumer: string, tenantId: string, sessionId: string) {
  return `${CURSOR_PREFIX}${consumer}::${key(tenantId, sessionId)}`;
}

export function getCursor(
  consumer: string,
  tenantId: string,
  sessionId: string
): number {
  if (!hasWindow()) return 0;
  const raw = window.localStorage.getItem(cursorKey(consumer, tenantId, sessionId));
  return raw ? Number(raw) || 0 : 0;
}

export function setCursor(
  consumer: string,
  tenantId: string,
  sessionId: string,
  seq: number
) {
  if (!hasWindow()) return;
  window.localStorage.setItem(
    cursorKey(consumer, tenantId, sessionId),
    String(seq)
  );
}

/**
 * Drain new events for a named consumer since its stored cursor, then advance
 * the cursor. Idempotent + replay-safe.
 */
export function drain(
  consumer: string,
  tenantId: string,
  sessionId: string,
  handler: (e: RealmEvent) => void
): number {
  const from = getCursor(consumer, tenantId, sessionId);
  const head = replay(tenantId, sessionId, handler, from);
  setCursor(consumer, tenantId, sessionId, head);
  return head;
}

/** Subscribe to every future append across all partitions. Returns unsubscribe. */
export function subscribe(fn: Subscriber): () => void {
  subscribers.add(fn);
  return () => subscribers.delete(fn);
}

/** Test/dev helper: clear a partition (and its in-memory cache). */
export function clearPartition(tenantId: string, sessionId: string) {
  partitions.delete(key(tenantId, sessionId));
  if (hasWindow()) {
    window.localStorage.removeItem(storageKey(tenantId, sessionId));
  }
}
