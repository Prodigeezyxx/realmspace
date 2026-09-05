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
 * ## Deterministic for a given hour
 *
 * A seeded PRNG, never `Math.random()`. A demo whose headline figure changes
 * between two refreshes is worse than no demo at all, so the stream is fixed for
 * any given anchor — and the anchor is the current hour, which is longer than
 * any demo lasts.
 *
 * It deliberately is *not* fixed forever. The activation has to look like it is
 * happening today: pinned to a date in the past, "people now" is permanently
 * zero and the live screen has nothing true to show. So the day slides forward
 * each hour, and the seed derives from the anchor, which reshuffles it rather
 * than replaying yesterday with new timestamps.
 */

import { appendMany, clearPartition, markLocalOnly, read } from "@/lib/bus";
import type { RealmEventInput } from "@/lib/contracts";
import { DEMO_SESSION } from "./session";

/** Marks every event this module writes, so nothing downstream has to guess. */
export const DEMO_MARKER = "demo_seed";
/** Which anchor a seeded event belongs to — see the trap note on seedDemoSession. */
export const DEMO_ANCHOR = "demo_anchor";

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

/** How long the demo day runs, ending at the anchor. */
const DAY_HOURS = 8;
const VISITORS = 140;
/**
 * The last few visitors are still in the room: an entry and no exit, so
 * `presentNow` is non-zero and the live screen has something true to show. It is
 * also what an unfinished session genuinely looks like — somebody standing in a
 * zone has not produced a dwell yet.
 *
 * Reserved explicitly rather than left to the arrival distribution. Landing in
 * the last few minutes by chance is roughly a one-in-a-hundred draw, so a demo
 * that relied on it would show an empty room more often than not, and which one
 * you got would depend on the hour.
 */
const STILL_INSIDE = 6;
const STILL_INSIDE_WINDOW_MS = 6 * 60 * 1000;

/**
 * The clock the demo hangs off, rounded down to the hour.
 *
 * Rounding is what keeps determinism useful. Anchoring to the exact millisecond
 * would give a different stream on every call, so the headline figures would
 * drift while somebody was presenting them; anchoring to a fixed date in the
 * past would put the demo months behind and make "people now" permanently zero.
 * An hourly anchor is stable for as long as any demo lasts and still lands the
 * activation on today.
 */
export function demoAnchor(now: number = Date.now()): number {
  return Math.floor(now / 3_600_000) * 3_600_000;
}

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

/** The anchor of the seed already in the log, or null if there isn't one. */
function seededAnchor(tenantId: string): number | null {
  for (const e of read(tenantId, DEMO_SESSION.id, { afterSeq: 0 })) {
    const p = e.payload as Record<string, unknown>;
    if (p?.[DEMO_MARKER] === true) return (p[DEMO_ANCHOR] as number) ?? 0;
  }
  return null;
}

/**
 * Write the demo session's event stream, once per anchor.
 *
 * Idempotent twice over: it returns early when the log already holds a seed for
 * this anchor, and every event carries a derived `eventId`, so even a concurrent
 * second call dedupes rather than doubling the visitor count.
 *
 * ## The trap in re-anchoring
 *
 * The `eventId` **must** include the anchor. It did not, and that made the
 * timeline impossible to move: re-seeding for a new hour produced byte-identical
 * ids, the log deduped every one of them, and the old timestamps survived. The
 * demo would have looked shifted in the source and been completely unchanged on
 * screen — a bug with no symptom except numbers that quietly refuse to update.
 *
 * A stale seed is therefore cleared rather than layered on top of, because the
 * ids no longer collide and appending would give two days at once.
 */
export function seedDemoSession(tenantId: string, now: number = Date.now()): number {
  const anchor = demoAnchor(now);
  const existing = seededAnchor(tenantId);
  if (existing === anchor) return 0;
  if (existing !== null) clearPartition(tenantId, DEMO_SESSION.id);

  const dayStart = anchor - DAY_HOURS * 3_600_000;
  // Seeded from the anchor so a new hour genuinely reshuffles the day rather
  // than replaying the same one with different labels.
  const rand = rng(anchor % 2_147_483_647);
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
      // same ids so the log dedupes instead of counting the day twice. The
      // anchor is part of the id precisely so that a *different* day does not.
      eventId: `demo_${DEMO_SESSION.id}_${anchor}_${idParts}`,
      tenantId,
      sessionId: DEMO_SESSION.id,
      type,
      payload: { ...payload, [DEMO_MARKER]: true, [DEMO_ANCHOR]: anchor },
      occurredAt: at,
    });
  };

  for (let v = 0; v < VISITORS; v++) {
    const anonId = `P-${String(v + 1).padStart(3, "0")}`;
    const stillInside = v >= VISITORS - STILL_INSIDE;
    // Arrivals spread across the day, bunched slightly after the middle so the
    // traffic chart has a shape rather than a flat line — except the reserved
    // few, who walked in within the last several minutes and are still here.
    const arrival = stillInside
      ? anchor - Math.floor(rand() * STILL_INSIDE_WINDOW_MS)
      : dayStart + Math.floor(rand() ** 0.8 * (DAY_HOURS * 3_600_000 - STILL_INSIDE_WINDOW_MS));
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

      // Still standing there: no exit, no dwell. Exactly what the log holds for
      // a person who has not left yet, and what makes "people now" a real
      // number rather than a decoration.
      if (stillInside) break;

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

      // Touchpoint interactions happen where the touchpoints are. The ids are
      // the demo session's *real* surface ids (`surfaces` above), not invented
      // ones — otherwise the report cannot match an interaction to the
      // touchpoint that produced it and falls back to showing a raw id.
      if (step.zoneId === "zone_experience" && rand() < 0.55) {
        emit(
          "surface.interaction",
          t,
          { anonId, surfaceId: "srf_mirror", kind: "ar" },
          `surface_${anonId}_srf_mirror`
        );
      }
      if (step.zoneId === "zone_product" && rand() < 0.34) {
        emit(
          "surface.interaction",
          t,
          { anonId, surfaceId: "srf_game", kind: "game" },
          `surface_${anonId}_srf_game`
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
  // One write, not 1,200. `persist()` serialises the whole partition, so
  // appending a day of visitors one at a time is quadratic — see `appendMany`.
  appendMany(events);

  // Never let the demo out of this browser. The outbound flush walks the same
  // log, so without this the next real emit() would post 140 invented visitors
  // into the append-only backend log, where nothing could tell them apart from
  // an activation that happened.
  markLocalOnly(tenantId, DEMO_SESSION.id);

  return events.length;
}
