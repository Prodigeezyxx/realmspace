/**
 * realmspace — is this payload readable?
 *
 * ## The failure this exists for
 *
 * The payloads on the bus are pinned in `docs/event-bus-spec.md` §3 in
 * snake_case, because they are shared with the Python producers; this app's
 * contract is camelCase. `lib/bus/wire.ts` translates between the two, and its
 * own header says why the mismatch is dangerous: it produces plausible-looking
 * numbers rather than an error.
 *
 * What that file does not do is check that the translation produced anything a
 * reader can use. So a payload the backend refuses outright — `graph_writer.py`
 * reads `payload["anon_id"]`, a missing key raises, the event is retried and
 * parked in `dead_letter` — is accepted silently here, and `lib/roi/scorecard.ts`
 * then does two things with it:
 *
 *   - `persons.add(undefined)` puts a **phantom visitor** in the Reach layer;
 *   - `dwellSum += undefined` makes every dwell figure `NaN`.
 *
 * Both reach the client report. The Phase 6 acceptance run found the second one
 * in the wild (`docs/roadmap.md`, defect 6) and fixed it in the benchmark only.
 * This is the same rule applied at the boundary instead: the two halves of the
 * system should agree about what a malformed payload is, and both should fail
 * loudly.
 *
 * ## What is checked, and what deliberately is not
 *
 * Only the fields a reader actually dereferences — `lib/roi/scorecard.ts`,
 * `lib/report/derive.ts`, `lib/live/derive.ts`, `lib/twin/replay.ts`. A field
 * nobody reads cannot produce a wrong figure by being absent, and refusing an
 * event over one would reject history this browser is only passing through.
 *
 * **An unknown type passes.** The taxonomy is additive (`event-bus-spec.md` §3)
 * and `wire.ts` takes the same position for its renames: a `spatial.*` type
 * nobody has written yet must not be refused by a browser that predates it.
 */

import type {
  DetectionPayload,
  DwellPayload,
  GazePayload,
  GroupPayload,
  PassbyPayload,
  RealmEventType,
  SurfaceInteractionPayload,
  SurfaceTouchedPayload,
  ZoneMovePayload,
} from "./events";

/**
 * The keys of `T` that are **not** optional.
 *
 * This is what keeps the table below honest. Listing a field as required here
 * when `events.ts` declares it optional — or misspelling one — is a compile
 * error rather than something a reviewer has to notice, so the interfaces stay
 * the single source of truth for what an event carries.
 */
type RequiredKeys<T> = {
  [K in keyof T]-?: undefined extends T[K] ? never : K;
}[keyof T];

/**
 * Per type, the fields without which a reader computes a wrong number.
 *
 * Not every non-optional field: `GroupPayload.status`, for instance, is required
 * by the interface and read only for display. These are the ones that end up
 * inside a `Set`, a sum or a divisor.
 */
const REQUIRED = {
  "perception.detection": ["anonId"] as const satisfies readonly RequiredKeys<DetectionPayload>[],
  "spatial.zone_enter": ["anonId", "zoneId"] as const satisfies readonly RequiredKeys<ZoneMovePayload>[],
  "spatial.zone_exit": ["anonId", "zoneId"] as const satisfies readonly RequiredKeys<ZoneMovePayload>[],
  "spatial.dwell": ["anonId", "zoneId", "durationSec"] as const satisfies readonly RequiredKeys<DwellPayload>[],
  "spatial.passby": ["anonId"] as const satisfies readonly RequiredKeys<PassbyPayload>[],
  "spatial.gaze": ["anonId", "targetId", "durationSec"] as const satisfies readonly RequiredKeys<GazePayload>[],
  "spatial.group": ["groupId", "memberAnonIds", "size"] as const satisfies readonly RequiredKeys<GroupPayload>[],
  // Deliberately not `anonId`: a tablet cannot know who pressed it, and
  // requiring one here would quarantine every tap the backend could not
  // attribute — the taps this event exists to count.
  "surface.touched": ["surfaceId"] as const satisfies readonly RequiredKeys<SurfaceTouchedPayload>[],
  "surface.interaction": ["anonId", "surfaceId"] as const satisfies readonly RequiredKeys<SurfaceInteractionPayload>[],
} satisfies Partial<Record<RealmEventType, readonly string[]>>;

/** Types this browser knows how to check. Everything else passes untouched. */
export const VALIDATED_EVENT_TYPES = Object.keys(REQUIRED) as RealmEventType[];

export type PayloadCheck = { ok: true } | { ok: false; reasons: string[] };

/**
 * Why a value is unusable, or null if it is fine.
 *
 * `NaN` is refused explicitly. It is the one value that passes `typeof x ===
 * "number"`, survives arithmetic, and turns a whole scorecard into `NaN` from a
 * single event — which is exactly the shape defect 6 took on a client's report.
 */
function fault(field: string, value: unknown): string | null {
  if (value === undefined || value === null) return `${field} is missing`;
  if (typeof value === "string") {
    return value.trim() === "" ? `${field} is empty` : null;
  }
  if (typeof value === "number") {
    return Number.isFinite(value) ? null : `${field} is ${value}`;
  }
  if (Array.isArray(value)) {
    return value.length === 0 ? `${field} is empty` : null;
  }
  return null;
}

/**
 * Can this app read the payload of an event of this type?
 *
 * Runs on **this app's dialect** — after `payloadFromWire`, not before, so a
 * snake_case payload that translation has already camelized is judged on what a
 * reader will actually see.
 */
export function validatePayload(
  type: RealmEventType,
  payload: unknown
): PayloadCheck {
  const required = REQUIRED[type as keyof typeof REQUIRED] as
    | readonly string[]
    | undefined;
  if (!required) return { ok: true };

  if (payload === null || typeof payload !== "object" || Array.isArray(payload)) {
    return { ok: false, reasons: ["payload is not an object"] };
  }

  const record = payload as Record<string, unknown>;
  const reasons: string[] = [];
  for (const field of required) {
    const reason = fault(field, record[field]);
    if (reason) reasons.push(reason);
  }

  return reasons.length ? { ok: false, reasons } : { ok: true };
}
