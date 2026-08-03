/**
 * The backend's actual output, run through the whole browser pipeline.
 *
 * `__fixtures__/backend-report-session.json` is not handwritten. It is a
 * verbatim capture of `GET /events` from a running FastAPI backend after the
 * real chain — `POST /v1/sessions` to configure the room, then detections
 * posted at camera density, then the tracker's own `spatial.*` output.
 *
 * That is the point of it. Every other test in this directory checks the
 * translation against what we *believe* the backend sends. This one checks it
 * against what the backend actually sent, so the day a payload key is renamed
 * on the Python side this fails, rather than the report quietly losing a layer
 * of the scorecard three weeks later.
 *
 * The scenario is deliberately built so the answers can be worked out on paper
 * from the raw log and compared — see the hand count below.
 *
 * To refresh it: run the backend, repeat the flow, replace the file, and redo
 * the hand count. A fixture nobody re-checks is a fixture that pins yesterday's
 * bug.
 */

import { describe, expect, it } from "vitest";

import { computeScorecard } from "@/lib/roi/scorecard";
import type { RealmEvent } from "@/lib/contracts";
import fixture from "./__fixtures__/backend-report-session.json";
import { eventFromWire, type WireEvent } from "./wire";

/** The fixture as the local log would hold it after mirroring. */
const mirrored: RealmEvent[] = (fixture as WireEvent[]).map((wire, i) => ({
  ...eventFromWire(wire),
  seq: i + 1,
  eventId: wire.eventId,
  occurredAt: wire.occurredAt,
  recordedAt: wire.recordedAt,
})) as RealmEvent[];

const ZONES = [
  { id: "z_entry", name: "Entry", kind: "entry" as const, weight: 1 },
  { id: "z_mirror", name: "Mirror Room", kind: "experience" as const, weight: 3 },
  { id: "z_out", name: "Outside", kind: "other" as const, weight: 1 },
];

describe("a real session, end to end", () => {
  it("captured the whole chain, config through spatial events", () => {
    const types = new Set(fixture.map((e) => e.type));
    expect([...types].sort()).toEqual([
      "perception.detection",
      "session.zones_updated",
      "spatial.dwell",
      "spatial.zone_enter",
      "spatial.zone_exit",
    ]);
  });

  it("still sends the payload keys this app translates from", () => {
    // Pinned in the backend's spelling. If Python starts sending `durationSec`,
    // or drops `person_id`, this is where it surfaces.
    const dwell = fixture.find((e) => e.type === "spatial.dwell")!;
    expect(Object.keys(dwell.payload as object).sort()).toEqual([
      "anon_id",
      "duration",
      "ended_at",
      "exceeded_threshold",
      // From the session-hygiene work: "move" or "dropout". A dropout duration
      // is a lower bound on the real stay, and a reader that cannot tell will
      // average truncated visits in with complete ones.
      "reason",
      "started_at",
      "zone_id",
    ]);

    const detection = fixture.find((e) => e.type === "perception.detection")!;
    expect(detection.payload).toHaveProperty("person_id");
    expect(detection.payload).toHaveProperty("frame_width");
  });

  it("shows the hygiene rules running in the shipped path", () => {
    // Two of the eight stays ended by the track going quiet rather than by the
    // person moving on. Before the dropout sweep those produced no dwell at
    // all — the visit was simply discarded.
    const reasons = fixture
      .filter((e) => e.type === "spatial.dwell")
      .map((e) => (e.payload as { reason?: string }).reason);

    expect(reasons.filter((r) => r === "dropout")).toHaveLength(2);
    expect(reasons.filter((r) => r === "move")).toHaveLength(6);
  });

  it("matches a hand count of the raw log", () => {
    /*
     * Three people through a three-zone room:
     *
     *   P1  entry 90s → mirror 120s → out 15s, back in: entry 30s → out 10s
     *   P2  entry 30s → out 10s
     *   P3  straight to mirror 180s, never through the entry
     *
     * By hand, from the eight dwell events in the fixture:
     *   unique visitors  3        (P1, P2, P3)
     *   footfall         3        entry crossings — P1 twice, P2 once. Not 2
     *                             (distinct people) and not 8 (all zone
     *                             entries); the old code had it wrong both ways.
     *   avg dwell        60.6s    (90+120+15+30+10+30+10+180) / 8 = 60.625
     *   weighted attn    1085     entry 150×1 + mirror 300×3 + out 35×1
     *   engaged          2 of 3   P1 and P3 clear 60s; P2's 30s does not
     *   CPEV             4500     9000 / 2 engaged
     *   ROI ratio        null     no revenue was supplied
     */
    const card = computeScorecard(mirrored, {
      engagedThresholdSec: 60,
      activationCost: 9000,
      zones: ZONES,
    });

    expect(card.reach.uniqueVisitors).toBe(3);
    expect(card.reach.entries).toBe(3);
    expect(card.engagement.avgDwellSec).toBe(60.6);
    expect(card.engagement.dwellWeightedAttention).toBe(1085);
    expect(card.engagement.engagementRate).toBe(0.667);
    expect(card.pipeline.costPerEngagedVisit).toBe(4500);
    expect(card.pipeline.roiRatio).toBeNull();
    expect(card.benchmarkVerdict).toBe("unknown");
  });

  it("produces no NaN anywhere in the scorecard", () => {
    // The failure mode of a missed translation is not an exception, it is a NaN
    // that renders as a dash. Sweep for it rather than trusting the assertions
    // above to have covered every field.
    const card = computeScorecard(mirrored, { zones: ZONES });
    const nans: string[] = [];
    JSON.stringify(card, (k, v) => {
      if (typeof v === "number" && Number.isNaN(v)) nans.push(k);
      return v;
    });
    expect(nans).toEqual([]);
  });
});
