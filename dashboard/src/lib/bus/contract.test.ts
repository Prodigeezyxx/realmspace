/**
 * The backend's actual output, run through the whole browser pipeline.
 *
 * `__fixtures__/backend-events.json` is not handwritten. It is a verbatim
 * capture of `GET /events` from a running FastAPI backend, after configuring a
 * session through `POST /v1/sessions` and posting two detections — the real
 * chain: config → detection → tracker → spatial events.
 *
 * That is the point of it. Every other test in this directory checks the
 * translation against what we *believe* the backend sends. This one checks it
 * against what the backend actually sent, so the day someone renames a payload
 * key on the Python side, this fails instead of the report quietly losing a
 * layer of the scorecard.
 *
 * To refresh it: run the backend, repeat the flow, and replace the file.
 */

import { describe, expect, it } from "vitest";

import { computeScorecard } from "@/lib/roi/scorecard";
import type { RealmEvent } from "@/lib/contracts";
import fixture from "./__fixtures__/backend-events.json";
import { eventFromWire, type WireEvent } from "./wire";

/** The fixture as the local log would hold it after mirroring. */
const mirrored: RealmEvent[] = (fixture as WireEvent[]).map((wire, i) => ({
  ...eventFromWire(wire),
  seq: i + 1,
  eventId: wire.eventId,
  occurredAt: wire.occurredAt,
  recordedAt: wire.recordedAt,
})) as RealmEvent[];

describe("a real session, end to end", () => {
  it("captured the chain we think it did", () => {
    expect(fixture.map((e) => e.type)).toEqual([
      "session.zones_updated",
      "perception.detection",
      "perception.detection",
      "spatial.zone_enter",
      "spatial.zone_exit",
      "spatial.dwell",
    ]);
  });

  it("still sends the payload keys this app translates from", () => {
    // Pinned deliberately in the backend's spelling. If Python starts sending
    // `durationSec`, or drops `person_id`, this is where it surfaces — not in a
    // report three weeks later.
    const dwell = fixture.find((e) => e.type === "spatial.dwell")!;
    expect(Object.keys(dwell.payload as object).sort()).toEqual([
      "anon_id",
      "duration",
      "ended_at",
      "exceeded_threshold",
      "started_at",
      "zone_id",
    ]);

    const detection = fixture.find((e) => e.type === "perception.detection")!;
    expect(detection.payload).toHaveProperty("person_id");
    expect(detection.payload).toHaveProperty("frame_width");
  });

  it("scores correctly once translated", () => {
    const card = computeScorecard(mirrored, {
      engagedThresholdSec: 30,
      activationCost: 10_000,
      // The zone was configured with weight 2 — a minute in the Mirror Room is
      // worth two anywhere else (roi-framework.md §2).
      zones: [{ id: "z_left", name: "Mirror Room", kind: "experience", weight: 2 }],
    });

    expect(card.reach.uniqueVisitors).toBe(1);
    expect(card.engagement.avgDwellSec).toBe(90);
    // 90s × weight 2. The single number this whole chain exists to produce.
    expect(card.engagement.dwellWeightedAttention).toBe(180);
    expect(card.engagement.engagementRate).toBe(1);
    expect(card.pipeline.costPerEngagedVisit).toBe(10_000);
  });

  it("produces no NaN anywhere in the scorecard", () => {
    // The failure mode of a missed translation is not an exception, it is a
    // NaN that renders as a dash. Sweep for it rather than trusting the
    // assertions above to have covered every field.
    const card = computeScorecard(mirrored);
    const nans: string[] = [];
    JSON.stringify(card, (k, v) => {
      if (typeof v === "number" && Number.isNaN(v)) nans.push(k);
      return v;
    });
    expect(nans).toEqual([]);
  });
});
