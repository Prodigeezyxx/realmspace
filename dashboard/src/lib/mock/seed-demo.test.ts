/**
 * The demo seed, as executable claims.
 *
 * Two properties matter. **Determinism**, because a pitch demo whose headline
 * moves between refreshes is worse than none. And **containment** — these are
 * invented visitors, and the backend log is append-only, so one leak is
 * permanent and indistinguishable from a real activation.
 */

import { beforeEach, describe, expect, it } from "vitest";

import { clearPartition, getCursor, headSeq, readAll } from "@/lib/bus";
import { computeScorecard } from "@/lib/roi/scorecard";
import type { RealmEvent } from "@/lib/contracts";
import { DEMO_SESSION } from "./session";
import { DEMO_MARKER, seedDemoSession } from "./seed-demo";

const T = "t_test";

beforeEach(() => {
  window.localStorage.clear();
  clearPartition(T, DEMO_SESSION.id);
});

describe("seedDemoSession", () => {
  it("produces the identical stream every run", () => {
    const first = seedDemoSession(T);
    const a = readAll(T, DEMO_SESSION.id).map((e) => e.eventId);

    clearPartition(T, DEMO_SESSION.id);
    window.localStorage.clear();

    const second = seedDemoSession(T);
    const b = readAll(T, DEMO_SESSION.id).map((e) => e.eventId);

    expect(second).toBe(first);
    expect(b).toEqual(a);
  });

  it("is a no-op the second time, so the demo never doubles", () => {
    const written = seedDemoSession(T);
    expect(written).toBeGreaterThan(0);
    expect(seedDemoSession(T)).toBe(0);
    expect(readAll(T, DEMO_SESSION.id)).toHaveLength(written);
  });

  it("marks the outbound cursor so nothing reaches the real bus", () => {
    // Without this, the next genuine emit() would flush 140 invented visitors
    // into an append-only log where nobody could tell them from a real day.
    seedDemoSession(T);
    expect(getCursor("remote-bus", T, DEMO_SESSION.id)).toBe(
      headSeq(T, DEMO_SESSION.id)
    );
  });

  it("labels every event as seeded", () => {
    seedDemoSession(T);
    const events = readAll(T, DEMO_SESSION.id);
    expect(
      events.every((e) => (e.payload as Record<string, unknown>)[DEMO_MARKER])
    ).toBe(true);
  });

  it("is chronological, which the occupancy maths depends on", () => {
    seedDemoSession(T);
    const times = readAll(T, DEMO_SESSION.id).map((e) => e.occurredAt);
    expect(times).toEqual([...times].sort((a, b) => a - b));
  });
});

describe("the demo scores like a real session", () => {
  it("produces a narrowing funnel and a plausible scorecard", () => {
    seedDemoSession(T);
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
    seedDemoSession(T);
    const card = computeScorecard(readAll(T, DEMO_SESSION.id) as RealmEvent[]);
    expect(card.affinity.sentiment).toBeNull();
    expect(card.affinity.npsLift).toBeNull();
  });
});
