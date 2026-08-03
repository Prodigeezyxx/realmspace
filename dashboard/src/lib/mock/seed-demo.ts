/**
 * realmspace — a synthetic visitor stream for the demo session.
 *
 * ## Why this exists rather than a hardcoded report
 *
 * `/report` used to render a designed page of invented figures. Making it
 * data-true removes those, which would leave the demo session — the pitch
 * artifact — showing an empty report.
 *
 * The fix is not to keep a second, fake report. It is to give the demo session
 * real *events*. Everything downstream then behaves exactly as it does for a
 * live activation: the same scorecard code, the same funnel, the same empty
 * states when something is genuinely absent. The demo stops being a mock-up of
 * the product and becomes the product, run on invented input.
 *
 * The honesty line sits here, and it is a real one. **These events are made up
 * and every surface that shows them says so.** What must never happen again is
 * a *number* being made up — a figure with no event behind it, which nobody can
 * audit and which is what `roi-framework.md` §3 forbids.
 *
 * ## Deterministic on purpose
 *
 * A seeded PRNG, never `Math.random()`. The demo has to show the same numbers in
 * a pitch on Tuesday as it did in rehearsal on Monday; a report whose headline
 * moves between refreshes is worse than no demo. It also makes the seeding
 * testable, which a random one would not be.
 */

import { append, markLocalOnly, read } from "@/lib/bus";
import type { RealmEventInput } from "@/lib/contracts";
import { DEMO_SESSION } from "./session";

/** Marks every event this module writes, so nothing downstream has to guess. */
export const DEMO_MARKER = "demo_seed";

/** mulberry32 — small, fast, and identical across runs and machines. */
function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Doors open. Fixed, so the timeline is stable across runs. */
const DAY_START = Date.parse("2026-05-18T10:00:00Z");
const VISITORS = 140;

/**
 * The path a visitor takes, as zone ids in order, with a plausible dwell range.
 * Weighted so the funnel narrows — most people enter, fewer reach the product
 * wall, fewer still sit down. A demo that showed 100% conversion would teach
 * whoever watches it the wrong thing about the product.
 */
const FUNNEL: { zoneId: string; reachedBy: number; dwell: [number, number] }[] = [
  { zoneId: "zone_entry", reachedBy: 1.0, dwell: [8, 40] },
  { zoneId: "zone_experience", reachedBy: 0.63, dwell: [45, 400] },
  { zoneId: "zone_product", reachedBy: 0.41, dwell: [30, 240] },
  { zoneId: "zone_lounge", reachedBy: 0.22, dwell: [60, 600] },
];

function isSeeded(tenantId: string): boolean {
  return read(tenantId, DEMO_SESSION.id, { afterSeq: 0 }).some(
    (e) => (e.payload as Record<string, unknown>)?.[DEMO_MARKER] === true
  );
}

/**
 * Write the demo session's event stream, once.
 *
 * Idempotent twice over: it returns early if the log already holds seeded
 * events, and every event carries a derived `eventId`, so even a concurrent
 * second call dedupes in the log rather than doubling the visitor count.
 */
export function seedDemoSession(tenantId: string): number {
  if (isSeeded(tenantId)) return 0;

  const rand = rng(20260518);
  const events: RealmEventInput[] = [];

  const emit = (
    type: RealmEventInput["type"],
    at: number,
    payload: Record<string, unknown>,
    idParts: string
  ) => {
    events.push({
      // Derived, not random — the same reason the backend consumers derive
      // theirs (docs/event-bus-spec.md §3): re-running this must produce the
      // same ids so the log dedupes instead of counting the day twice.
      eventId: `demo_${DEMO_SESSION.id}_${idParts}`,
      tenantId,
      sessionId: DEMO_SESSION.id,
      type,
      payload: { ...payload, [DEMO_MARKER]: true },
      occurredAt: at,
    });
  };

  for (let v = 0; v < VISITORS; v++) {
    const anonId = `P-${String(v + 1).padStart(3, "0")}`;
    // Arrivals spread over eight hours, bunched slightly after lunch so the
    // traffic chart has a shape rather than a flat line.
    const arrival =
      DAY_START + Math.floor((rand() ** 0.8) * 8 * 3600 * 1000);
    let t = arrival;

    for (const step of FUNNEL) {
      if (rand() > step.reachedBy) break;

      const [lo, hi] = step.dwell;
      const duration = Math.round(lo + rand() * (hi - lo));
      const enteredAt = new Date(t).toISOString();
      const leftAt = new Date(t + duration * 1000).toISOString();

      emit(
        "spatial.zone_enter",
        t,
        { anonId, zoneId: step.zoneId, at: enteredAt },
        `enter_${anonId}_${step.zoneId}`
      );
      emit(
        "spatial.zone_exit",
        t + duration * 1000,
        { anonId, zoneId: step.zoneId, at: leftAt, enteredAt },
        `exit_${anonId}_${step.zoneId}`
      );
      emit(
        "spatial.dwell",
        t + duration * 1000,
        {
          anonId,
          zoneId: step.zoneId,
          durationSec: duration,
          startedAt: enteredAt,
          endedAt: leftAt,
          exceededThreshold: duration >= 60,
        },
        `dwell_${anonId}_${step.zoneId}`
      );

      // A short walk to the next zone.
      t += duration * 1000 + Math.round((5 + rand() * 25) * 1000);

      // Touchpoint interactions happen where the touchpoints are.
      if (step.zoneId === "zone_experience" && rand() < 0.55) {
        emit(
          "surface.interaction",
          t,
          { anonId, surfaceId: "surface_mirror", kind: "ar" },
          `surface_${anonId}_mirror`
        );
      }
      if (step.zoneId === "zone_product" && rand() < 0.34) {
        emit(
          "surface.interaction",
          t,
          { anonId, surfaceId: "surface_quiz", kind: "game" },
          `surface_${anonId}_quiz`
        );
      }
    }

    // Consent capture — the only path to a "lead" in the scorecard, and rare
    // enough that the capture rate looks like a real one.
    if (rand() < 0.24) {
      emit(
        "consent.captured",
        t,
        {
          anonId,
          contactId: `c_${anonId}`,
          tier: "T2",
          basis: "explicit_optin",
          copyVersion: "demo-v1",
          capturedBy: "kiosk",
        },
        `consent_${anonId}`
      );
    }
  }

  // Chronological, so the log reads like a day rather than like a loop over
  // visitors — enter/exit deltas depend on the ordering being real.
  events.sort((a, b) => (a.occurredAt ?? 0) - (b.occurredAt ?? 0));
  for (const e of events) append(e);

  // Never let the demo out of this browser. The outbound flush walks the same
  // log, so without this the next real emit() would post 140 invented visitors
  // into the append-only backend log, where nothing could tell them apart from
  // an activation that happened.
  markLocalOnly(tenantId, DEMO_SESSION.id);

  return events.length;
}
