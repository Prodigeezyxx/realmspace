/**
 * The scorecard, as executable claims.
 *
 * These exist because of what this file replaced: a component that fell back to
 * an invented `DEMO` scorecard and passed hardcoded economics, so the headline
 * ROI ratio was fiction regardless of the data. The tests below pin the two
 * properties that stop that recurring — **absent is not zero**, and a figure the
 * log cannot support comes back `null`.
 */

import { describe, expect, it } from "vitest";

import type { RealmEvent, RealmEventType, ZoneNode } from "@/lib/contracts";
import { computeScorecard } from "./scorecard";

let seq = 0;
function ev(type: RealmEventType, payload: Record<string, unknown>): RealmEvent {
  seq++;
  return {
    seq,
    eventId: `e-${seq}`,
    tenantId: "t_test",
    sessionId: "s_1",
    type,
    payload: payload as never,
    occurredAt: Date.parse("2026-08-03T10:00:00Z") + seq * 1000,
    recordedAt: 0,
  };
}

const ZONES: ZoneNode[] = [
  { id: "z_entry", name: "Entry", kind: "entry", weight: 1 },
  { id: "z_mirror", name: "Mirror Room", kind: "experience", weight: 3 },
];

describe("Reach — footfall", () => {
  it("counts crossings of the entry zone, not people in any zone", () => {
    // roi-framework.md §2: footfall is `count(ENTERED entry-zone)`. Someone
    // reaching the mirror room is not a second person walking in, and someone
    // who steps out and back in is two crossings.
    const events = [
      ev("spatial.zone_enter", { anonId: "P1", zoneId: "z_entry" }),
      ev("spatial.zone_enter", { anonId: "P1", zoneId: "z_mirror" }),
      ev("spatial.zone_enter", { anonId: "P1", zoneId: "z_entry" }),
      ev("spatial.zone_enter", { anonId: "P2", zoneId: "z_entry" }),
    ];
    const card = computeScorecard(events, { zones: ZONES });

    expect(card.reach.entries).toBe(3);
    expect(card.reach.uniqueVisitors).toBe(2);
  });

  it("returns null for footfall when no zone is marked as the entry", () => {
    // The distinction this whole rewrite is about: 0 would say nobody came,
    // null says we were never told where the door is.
    const events = [ev("spatial.zone_enter", { anonId: "P1", zoneId: "z_mirror" })];
    const card = computeScorecard(events, {
      zones: [{ id: "z_mirror", name: "Mirror", kind: "experience" }],
    });

    expect(card.reach.entries).toBeNull();
    expect(card.reach.uniqueVisitors).toBe(1);
  });

  it("tracks peak occupancy across zone moves without double counting", () => {
    // A→B is exit-then-enter and must net to zero.
    const events = [
      ev("spatial.zone_enter", { anonId: "P1", zoneId: "z_entry" }),
      ev("spatial.zone_enter", { anonId: "P2", zoneId: "z_entry" }),
      ev("spatial.zone_exit", { anonId: "P1", zoneId: "z_entry" }),
      ev("spatial.zone_enter", { anonId: "P1", zoneId: "z_mirror" }),
    ];
    expect(computeScorecard(events, { zones: ZONES }).reach.peakZoneConcurrency).toBe(2);
  });
});

describe("Reach — peak zone occupancy", () => {
  /**
   * A running +1/−1 over the log answers "how many were in a zone at once" only
   * if the log is in the order things happened. A producer that appends one
   * visitor's whole journey before starting the next — or a batch replayed
   * after an outage — breaks that, and the report tells a client one person was
   * in the room when three were. Found by the Phase 6 acceptance run.
   */
  function at(type: RealmEventType, payload: Record<string, unknown>, iso: string) {
    seq++;
    return {
      seq,
      eventId: `e-${seq}`,
      tenantId: "t_test",
      sessionId: "s_1",
      type,
      payload: payload as never,
      occurredAt: Date.parse(iso),
      recordedAt: 0,
    } as RealmEvent;
  }

  it("counts overlap by when it happened, not by where it landed in the log", () => {
    // Two visitors, appended one complete journey at a time. In log order the
    // counter never exceeds 1; in event time they are in the room together.
    const events = [
      at("spatial.zone_enter", { anonId: "P1", zoneId: "z_mirror" }, "2026-08-03T10:00:00Z"),
      at("spatial.zone_exit", { anonId: "P1", zoneId: "z_mirror" }, "2026-08-03T10:05:00Z"),
      at("spatial.zone_enter", { anonId: "P2", zoneId: "z_mirror" }, "2026-08-03T10:02:00Z"),
      at("spatial.zone_exit", { anonId: "P2", zoneId: "z_mirror" }, "2026-08-03T10:03:00Z"),
    ];

    expect(computeScorecard(events, { zones: ZONES }).reach.peakZoneConcurrency).toBe(2);
  });

  it("does not count two visits that never overlapped", () => {
    const events = [
      at("spatial.zone_enter", { anonId: "P1", zoneId: "z_mirror" }, "2026-08-03T10:00:00Z"),
      at("spatial.zone_exit", { anonId: "P1", zoneId: "z_mirror" }, "2026-08-03T10:01:00Z"),
      at("spatial.zone_enter", { anonId: "P2", zoneId: "z_mirror" }, "2026-08-03T10:02:00Z"),
      at("spatial.zone_exit", { anonId: "P2", zoneId: "z_mirror" }, "2026-08-03T10:03:00Z"),
    ];

    expect(computeScorecard(events, { zones: ZONES }).reach.peakZoneConcurrency).toBe(1);
  });
});

describe("Engagement", () => {
  it("weights dwell by the zone's configured weight", () => {
    const events = [
      ev("spatial.dwell", { anonId: "P1", zoneId: "z_entry", durationSec: 100 }),
      ev("spatial.dwell", { anonId: "P1", zoneId: "z_mirror", durationSec: 100 }),
    ];
    // 100×1 + 100×3
    expect(
      computeScorecard(events, { zones: ZONES }).engagement.dwellWeightedAttention
    ).toBe(400);
  });

  it("uses the session's threshold, not a built-in one", () => {
    const events = [
      ev("spatial.dwell", { anonId: "P1", zoneId: "z_mirror", durationSec: 45 }),
    ];
    expect(
      computeScorecard(events, { zones: ZONES, engagedThresholdSec: 30 }).engagement
        .engagementRate
    ).toBe(1);
    expect(
      computeScorecard(events, { zones: ZONES, engagedThresholdSec: 60 }).engagement
        .engagementRate
    ).toBe(0);
  });
});

describe("Engagement — a tap and the person who made it", () => {
  // A tablet at a touchpoint has no camera. It can attest that its button was
  // pressed and not to who pressed it, so the backend attributes a tap only
  // when exactly one person was in the zone. The two claims are counted apart:
  // "we measured 40 taps" and "we can name 11 of the people who made them".

  it("counts an unattributed tap as an interaction and not as an engaged visitor", () => {
    const card = computeScorecard(
      [ev("surface.touched", { surfaceId: "sf_mirror", kind: "tap" })],
      { zones: ZONES }
    );
    expect(card.engagement.surfaceInteractions).toBe(1);
    expect(card.engagement.engagedVisitors).toBe(0);
  });

  it("counts an attributed tap once, and the person with it", () => {
    // Both events are on the log for one press: the tablet's raw tap and the
    // interaction the resolver derived from it. Counting the interaction twice
    // would double a touchpoint's tally against the taps it actually took.
    const card = computeScorecard(
      [
        ev("surface.touched", { surfaceId: "sf_mirror", kind: "tap", touchId: "t1" }),
        ev("surface.interaction", {
          anonId: "P1",
          surfaceId: "sf_mirror",
          kind: "tap",
          touchId: "t1",
        }),
      ],
      { zones: ZONES }
    );
    expect(card.engagement.surfaceInteractions).toBe(1);
    expect(card.engagement.engagedVisitors).toBe(1);
  });

  it("still counts booth hardware that posts an interaction with no raw tap", () => {
    // The event contract has always allowed a surface to report an interaction
    // directly — an RFID plinth has no `surface.touched` behind it, and
    // dropping it would lose the signal this whole layer is about.
    const card = computeScorecard(
      [ev("surface.interaction", { anonId: "P1", surfaceId: "sf_rfid", kind: "scan" })],
      { zones: ZONES }
    );
    expect(card.engagement.surfaceInteractions).toBe(1);
    expect(card.engagement.engagedVisitors).toBe(1);
  });
});

describe("Pipeline — what must stay blank", () => {
  const engaged = [
    ev("spatial.dwell", { anonId: "P1", zoneId: "z_mirror", durationSec: 120 }),
    ev("spatial.dwell", { anonId: "P2", zoneId: "z_mirror", durationSec: 120 }),
  ];

  it("reports no ROI ratio when revenue was never supplied", () => {
    const card = computeScorecard(engaged, { zones: ZONES, activationCost: 1000 });

    expect(card.pipeline.roiRatio).toBeNull();
    expect(card.pipeline.pipelineMultiple).toBeNull();
    expect(card.benchmarkVerdict).toBe("unknown");
    // But the part that *is* measured still lands.
    expect(card.pipeline.costPerEngagedVisit).toBe(500);
  });

  it("reports no cost metrics when no cost was set", () => {
    const card = computeScorecard(engaged, { zones: ZONES });
    expect(card.pipeline.costPerEngagedVisit).toBeNull();
    expect(card.pipeline.costPerQualifiedLead).toBeNull();
  });

  it("handles a free activation as zero cost, not as unknown", () => {
    // `cost && …` used to make a genuinely free activation indistinguishable
    // from one whose cost nobody entered.
    const card = computeScorecard(engaged, { zones: ZONES, activationCost: 0 });
    expect(card.pipeline.costPerEngagedVisit).toBe(0);
  });

  it("rates against the 3:1–5:1 benchmark only once both figures exist", () => {
    const card = computeScorecard(engaged, {
      zones: ZONES,
      activationCost: 1000,
      revenueInfluenced: 5000,
    });
    expect(card.pipeline.roiRatio).toBe(4);
    expect(card.benchmarkVerdict).toBe("strong");
  });
});

describe("an empty log", () => {
  it("is all zeroes and nulls — never a fabricated shape", () => {
    const card = computeScorecard([]);
    expect(card.reach.uniqueVisitors).toBe(0);
    expect(card.reach.entries).toBeNull();
    expect(card.engagement.avgDwellSec).toBe(0);
    expect(card.pipeline.roiRatio).toBeNull();
    expect(card.benchmarkVerdict).toBe("unknown");
  });
});
