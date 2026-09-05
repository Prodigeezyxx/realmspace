/**
 * realmspace — translation between the backend's wire payloads and this app's.
 *
 * ## Why this file has to exist
 *
 * The event *envelope* needs no translation: the backend already emits the
 * canonical `RealmEvent` from `contracts/events.ts` — camelCase keys, ms-epoch
 * timestamps — precisely so the browser would not need a mapper.
 *
 * The **payload** is a different story, and the mismatch is silent. The payloads
 * are pinned in `docs/event-bus-spec.md` §3 in snake_case, because they are a
 * contract shared with the Python producers (`perception/realmspace.py` runs
 * against either backend) and with the other track. This app's contract is
 * camelCase. So a `spatial.dwell` arrives as:
 *
 *     { anon_id: "P-1", zone_id: "z_a", duration: 92.4, ... }
 *
 * and `lib/roi/scorecard.ts` reads `p.anonId`, `p.zoneId`, `p.durationSec`.
 * Every one of those is `undefined`. Nothing throws. The scorecard adds
 * `undefined` to a running total, gets `NaN`, and the report renders a dash
 * where a number should be — or worse, `persons.add(undefined)` counts one
 * phantom visitor and the Reach layer is quietly wrong.
 *
 * That is the whole reason for this module: the failure it prevents produces
 * plausible-looking numbers rather than an error, and plausible-looking wrong
 * numbers are the one thing `roi-framework.md` §3 says we must never ship.
 *
 * ## Which side bends
 *
 * The spec bends for nobody: it is the artifact shared across two backends and
 * a Python producer. This app's contract declares itself canonical for the
 * browser. So the translation happens here, at the boundary, once — rather than
 * each reader remembering to check both spellings.
 */

import type { RealmEvent, RealmEventInput, RealmEventType } from "@/lib/contracts";

/** `{a_b: 1}` → `{aB: 1}`, recursively, through arrays and nested objects. */
function camelizeKeys(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(camelizeKeys);
  if (value === null || typeof value !== "object") return value;

  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
    const camel = k.replace(/_([a-z0-9])/g, (_, c: string) => c.toUpperCase());
    out[camel] = camelizeKeys(v);
  }
  return out;
}

/** The inverse. `{aB: 1}` → `{a_b: 1}`. */
function snakeizeKeys(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(snakeizeKeys);
  if (value === null || typeof value !== "object") return value;

  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
    const snake = k.replace(/[A-Z]/g, (c) => `_${c.toLowerCase()}`);
    out[snake] = snakeizeKeys(v);
  }
  return out;
}

/**
 * Renames the generic conversion cannot derive, per event type.
 *
 * Kept to the two that genuinely differ in *name* rather than in case. Anything
 * else — including a `spatial.*` type nobody has written yet — is handled by the
 * case conversion above and needs no entry here, which keeps the taxonomy
 * additive (`event-bus-spec.md` §3) instead of requiring a code change per type.
 */
const INBOUND_RENAMES: Partial<Record<RealmEventType, Record<string, string>>> = {
  // The spec calls it `duration` (seconds); the contract calls it `durationSec`.
  // `scorecard.ts` reads the latter — this rename is what makes dwell count.
  "spatial.dwell": { duration: "durationSec" },
  // `spatial.gaze` carries the same field under the same spec name, for the
  // same reason — §3 pins `duration` for every span. Without this a look
  // arrives with `durationSec` undefined and the report's attention figures go
  // the way dwell's did before that rename existed.
  "spatial.gaze": { duration: "durationSec" },
  // The spec pins `members` (event-bus-spec.md §3); this app's contract has
  // always declared `memberAnonIds`, which is the clearer name and matches
  // `anonId` everywhere else. The two were written years apart and nothing had
  // ever put an event between them, because `spatial.group` had no producer
  // until Phase 6 — so the mismatch was invisible: the payload would have
  // arrived with `members` set and `memberAnonIds` undefined, and any reader
  // would have rendered a group with no members rather than raising.
  "spatial.group": { members: "memberAnonIds" },
};

const OUTBOUND_RENAMES: Partial<Record<RealmEventType, Record<string, string>>> = {
  "spatial.dwell": { durationSec: "duration" },
  "spatial.gaze": { durationSec: "duration" },
  "spatial.group": { memberAnonIds: "members" },
};

function applyRenames(
  payload: Record<string, unknown>,
  renames: Record<string, string> | undefined
): Record<string, unknown> {
  if (!renames) return payload;
  const out = { ...payload };
  for (const [from, to] of Object.entries(renames)) {
    if (from in out) {
      out[to] = out[from];
      delete out[from];
    }
  }
  return out;
}

/**
 * A payload as it arrives from the bus → this app's shape.
 *
 * `person_id` is folded into `anonId` because `event-bus-spec.md` §3 accepts
 * both spellings from producers ("`anon_id` is canonical, `person_id` is also
 * accepted") — so a reader in this app would otherwise have to accept both too,
 * forever, and the first one to forget gets a silent undefined.
 */
export function payloadFromWire(
  type: RealmEventType,
  payload: unknown
): Record<string, unknown> {
  if (payload === null || typeof payload !== "object") return {};

  let out = camelizeKeys(payload) as Record<string, unknown>;
  out = applyRenames(out, INBOUND_RENAMES[type]);

  if (out.personId !== undefined && out.anonId === undefined) {
    out.anonId = out.personId;
  }
  return out;
}

/** This app's payload shape → what the bus expects. The inverse of the above. */
export function payloadToWire(
  type: RealmEventType,
  payload: unknown
): Record<string, unknown> {
  if (payload === null || typeof payload !== "object") return {};
  const renamed = applyRenames(
    payload as Record<string, unknown>,
    OUTBOUND_RENAMES[type]
  );
  return snakeizeKeys(renamed) as Record<string, unknown>;
}

/** What the backend sends on the wire: the canonical envelope, payload untranslated. */
export interface WireEvent {
  seq: number;
  eventId: string;
  tenantId: string;
  sessionId: string;
  type: RealmEventType;
  payload: unknown;
  occurredAt: number;
  recordedAt: number;
}

/**
 * A wire event → the input the local durable log appends.
 *
 * `seq` is deliberately dropped. The local log assigns its own sequence, and the
 * two counters are genuinely different things: the remote `seq` orders a
 * tenant's whole log server-side, the local one orders what this browser has
 * seen. Carrying the remote number into the local log would make `headSeq()`
 * jump around as events arrive from two directions. The remote cursor is
 * tracked separately, by `remote.ts`, which is the only thing that needs it.
 *
 * `eventId` is preserved, and that is what makes the whole mirror idempotent:
 * an event this browser produced, posted, and then received back over the socket
 * dedupes against its own original append (`log.ts` → `seenIds`).
 */
export function eventFromWire(wire: WireEvent): RealmEventInput {
  return {
    eventId: wire.eventId,
    tenantId: wire.tenantId,
    sessionId: wire.sessionId,
    type: wire.type,
    payload: payloadFromWire(wire.type, wire.payload),
    occurredAt: wire.occurredAt,
  };
}

/** A local event → the body `POST /events` expects. */
export function eventToWire(event: RealmEvent): Record<string, unknown> {
  return {
    eventId: event.eventId,
    tenantId: event.tenantId,
    sessionId: event.sessionId,
    type: event.type,
    payload: payloadToWire(event.type, event.payload),
    // The backend parses ISO or epoch; ISO is what its Pydantic model documents.
    occurredAt: new Date(event.occurredAt).toISOString(),
  };
}
