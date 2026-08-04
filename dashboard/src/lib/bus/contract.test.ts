/**
 * The backend's actual output, run through the whole browser pipeline.
 *
 * `__fixtures__/backend-report-session.json` is not handwritten. It is a
 * verbatim capture of `GET /events` from a running FastAPI backend after the
 * real chain — `POST /v1/sessions` to configure the room and its touchpoints,
 * detections posted at camera density, a surface interaction, and finally the
 * operator ending the session.
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
import { buildSurfaceRows } from "@/lib/report/derive";
import { buildReplay } from "@/lib/twin/replay";
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
];

describe("a real session, end to end", () => {
  it("captured every stage of the chain", () => {
    const types = new Set(fixture.map((e) => e.type));
    expect([...types].sort()).toEqual([
      "perception.detection",
      "session.ended",
      "session.zones_updated",
      "spatial.dwell",
      "spatial.passby",
      "spatial.zone_enter",
      "spatial.zone_exit",
      "surface.interaction",
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
      // From the session-hygiene work: "move", "dropout" or "session_end". A
      // dropout duration is a lower bound on the real stay, and a reader that
      // cannot tell will average truncated visits in with complete ones.
      "reason",
      "started_at",
      "zone_id",
    ]);

    const passby = fixture.find((e) => e.type === "spatial.passby")!;
    expect(Object.keys(passby.payload as object).sort()).toEqual([
      "adjacent_zone_id",
      "anon_id",
      "at",
      "closest_dist",
      "reason",
    ]);

    const detection = fixture.find((e) => e.type === "perception.detection")!;
    expect(detection.payload).toHaveProperty("person_id");
    expect(detection.payload).toHaveProperty("frame_width");
  });

  it("closed the last visitor out because the operator ended the session", () => {
    // Nobody's detections stop and then resume, so without `session.ended` this
    // dwell would not exist at all — the visitor was still in the mirror room
    // when the doors shut.
    const closing = fixture
      .filter((e) => e.type === "spatial.dwell")
      .map((e) => (e.payload as { reason?: string }).reason);
    expect(closing).toContain("session_end");
  });

  it("recorded the visitor who came close and declined", () => {
    // P2 hovered 0.033 of the frame from the mirror room and never went in.
    // This is the Reach layer's negative signal, and the only metric in the
    // catalogue that can make an activation look worse.
    const passby = fixture.find((e) => e.type === "spatial.passby")!;
    const p = passby.payload as Record<string, unknown>;
    expect(p.anon_id).toBe("P2");
    expect(p.adjacent_zone_id).toBe("z_mirror");
    expect(p.closest_dist).toBeCloseTo(0.033, 3);
  });

  it("matches a hand count of the raw log", () => {
    /*
     * Two people through a two-zone room:
     *
     *   P1  entry 60s → mirror 120s, used the AR Mirror, still there at the end
     *   P2  entry 40s → hovered beside the mirror room and never entered it
     *
     * By hand, from the three dwell events and one pass-by in the fixture:
     *   unique visitors  2
     *   footfall         2      entry crossings, one each
     *   pass-by          1      P2 declined the mirror room
     *   avg dwell        73.3s  (60 + 40 + 120) / 3 = 73.333
     *   weighted attn    460    entry 100×1 + mirror 120×3
     *   engaged          1 of 2 P1 clears the 60s threshold; P2's 40s does not
     *   surfaces         1      the AR Mirror interaction
     *   CPEV             9000   9000 / 1 engaged
     *   ROI ratio        null   no revenue was supplied
     */
    const card = computeScorecard(mirrored, {
      engagedThresholdSec: 60,
      activationCost: 9000,
      zones: ZONES,
    });

    expect(card.reach.uniqueVisitors).toBe(2);
    expect(card.reach.entries).toBe(2);
    expect(card.reach.passBy).toBe(1);
    expect(card.engagement.avgDwellSec).toBe(73.3);
    expect(card.engagement.dwellWeightedAttention).toBe(460);
    expect(card.engagement.engagementRate).toBe(0.5);
    expect(card.engagement.surfaceInteractions).toBe(1);
    expect(card.pipeline.costPerEngagedVisit).toBe(9000);
    expect(card.pipeline.roiRatio).toBeNull();
    expect(card.benchmarkVerdict).toBe("unknown");
  });

  it("labels the touchpoint with the operator's name for it", () => {
    const [row] = buildSurfaceRows(mirrored, [
      { id: "sf_mirror", label: "AR Mirror" },
    ]);
    expect(row.label).toBe("AR Mirror");
    expect(row.interactions).toBe(1);
  });

  it("replays as measured paths in the twin", () => {
    // 54 real detections from a real camera-shaped producer. The twin should
    // treat all of it as measured — no zone-centroid inference — and take its
    // timeline from the events rather than from the 620-second constant the
    // page used to hardcode.
    const replay = buildReplay(mirrored, []);

    expect(replay.tracks.map((t) => t.id).sort()).toEqual(["P1", "P2"]);
    expect(replay.inferredCount).toBe(0);
    expect(replay.durationSec).toBeGreaterThan(0);

    // Everyone is somewhere real at the start of their own span.
    const atStart = replay.positionsAt(0);
    expect(atStart.length).toBeGreaterThan(0);
    for (const p of atStart) {
      expect(p.x).toBeGreaterThanOrEqual(0);
      expect(p.x).toBeLessThanOrEqual(1);
      expect(p.inferred).toBe(false);
    }
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
