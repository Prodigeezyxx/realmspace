/**
 * The demo seed, as executable claims.
 *
 * Three properties matter. **Determinism within an hour**, because a pitch demo
 * whose headline moves between refreshes is worse than none. **Movement across
 * hours**, because an activation pinned to a date in the past has nobody in the
 * room and nothing true to show live. And **containment** — these are invented
 * visitors, the backend log is append-only, so one leak is permanent and
 * indistinguishable from a real activation.
 */

import { beforeEach, describe, expect, it } from "vitest";

import { clearPartition, getCursor, headSeq, readAll } from "@/lib/bus";
import { presentNow } from "@/lib/live/derive";
import { computeScorecard } from "@/lib/roi/scorecard";
import type { RealmEvent } from "@/lib/contracts";
import { DEMO_SESSION } from "./session";
import { DEMO_MARKER, demoAnchor, seedDemoSession } from "./seed-demo";

const T = "t_test";
/** A fixed clock, so nothing here depends on when the suite happens to run. */
const NOW = Date.parse("2026-08-04T15:20:00Z");
const AN_HOUR_LATER = NOW + 3_600_000;

beforeEach(() => {
  window.localStorage.clear();
  clearPartition(T, DEMO_SESSION.id);
});

describe("demoAnchor", () => {
  it("rounds down to the hour, so a demo is stable while it is being given", () => {
    expect(demoAnchor(NOW)).toBe(Date.parse("2026-08-04T15:00:00Z"));
    expect(demoAnchor(NOW + 60_000)).toBe(demoAnchor(NOW));
  });
});

describe("seedDemoSession", () => {
  it("produces the identical stream for the same hour", () => {
    const first = seedDemoSession(T, NOW);
    const a = readAll(T, DEMO_SESSION.id).map((e) => e.eventId);

    clearPartition(T, DEMO_SESSION.id);
    window.localStorage.clear();

    // A minute later is the same hour, so the same day.
    const second = seedDemoSession(T, NOW + 60_000);
    const b = readAll(T, DEMO_SESSION.id).map((e) => e.eventId);

    expect(second).toBe(first);
    expect(b).toEqual(a);
  });

  it("is a no-op when the log already holds this hour's seed", () => {
    const written = seedDemoSession(T, NOW);
    expect(written).toBeGreaterThan(0);
    expect(seedDemoSession(T, NOW)).toBe(0);
    expect(readAll(T, DEMO_SESSION.id)).toHaveLength(written);
  });

  it("actually moves the timeline into the next hour", () => {
    /*
     * The trap this pins. `eventId` used to be `demo_{session}_{parts}` with no
     * timestamp in it, so re-seeding for a new hour produced byte-identical ids,
     * the log deduped every one, and the old timestamps survived untouched. The
     * demo would have looked shifted in the source and been completely
     * unchanged on screen — a bug whose only symptom is numbers that quietly
     * refuse to update.
     */
    seedDemoSession(T, NOW);
    const before = readAll(T, DEMO_SESSION.id);
    const lastBefore = Math.max(...before.map((e) => e.occurredAt));

    seedDemoSession(T, AN_HOUR_LATER);
    const after = readAll(T, DEMO_SESSION.id);
    const lastAfter = Math.max(...after.map((e) => e.occurredAt));

    expect(lastAfter).toBeGreaterThan(lastBefore);
    // And it replaced the old day rather than stacking a second one on top.
    expect(Math.min(...after.map((e) => e.occurredAt))).toBeGreaterThan(
      Math.min(...before.map((e) => e.occurredAt))
    );
  });

  it("marks the outbound cursor so nothing reaches the real bus", () => {
    // Without this, the next genuine emit() would flush 140 invented visitors
    // into an append-only log where nobody could tell them from a real day.
    seedDemoSession(T, NOW);
    expect(getCursor("remote-bus", T, DEMO_SESSION.id)).toBe(
      headSeq(T, DEMO_SESSION.id)
    );
  });

  it("labels every event as seeded", () => {
    seedDemoSession(T, NOW);
    const events = readAll(T, DEMO_SESSION.id);
    expect(
      events.every((e) => (e.payload as Record<string, unknown>)[DEMO_MARKER])
    ).toBe(true);
  });

  it("is chronological, which the occupancy maths depends on", () => {
    seedDemoSession(T, NOW);
    const times = readAll(T, DEMO_SESSION.id).map((e) => e.occurredAt);
    expect(times).toEqual([...times].sort((a, b) => a - b));
  });
});

describe("the demo scores like a real session", () => {
  it("leaves people in the room, so the live view has something true to show", () => {
    // Visitors who arrived in the last few minutes have an entry and no exit —
    // exactly what the log looks like for somebody still standing there.
    seedDemoSession(T, NOW);
    const events = readAll(T, DEMO_SESSION.id) as RealmEvent[];
    expect(presentNow(events)).toBeGreaterThan(0);
  });

  it("produces a plausible scorecard", () => {
    seedDemoSession(T, NOW);
    const events = readAll(T, DEMO_SESSION.id) as RealmEvent[];
    const card = computeScorecard(events, {
      zones: [
        { id: "zone_entry", name: "Entry Arch", kind: "entry", weight: 1 },
        { id: "zone_experience", name: "Mirror Room", kind: "experience", weight: 3 },
      ],
      engagedThresholdSec: 60,
      activationCost: 12000,
    });

    expect(card.reach.uniqueVisitors).toBeGreaterThan(50);
    expect(card.reach.entries).toBeGreaterThan(0);
    expect(card.engagement.avgDwellSec).toBeGreaterThan(0);
    // Cost is configured, so this is computable.
    expect(card.pipeline.costPerEngagedVisit).not.toBeNull();
    // Revenue is not supplied, so this must not be.
    expect(card.pipeline.roiRatio).toBeNull();
  });

  it("does not fill the affinity layer, which nothing measures", () => {
    seedDemoSession(T, NOW);
    const card = computeScorecard(readAll(T, DEMO_SESSION.id) as RealmEvent[]);
    expect(card.affinity.sentiment).toBeNull();
    expect(card.affinity.npsLift).toBeNull();
  });
});
