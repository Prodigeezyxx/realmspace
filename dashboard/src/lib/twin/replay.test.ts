/**
 * The twin's replay builder, as executable claims.
 *
 * The one that matters most is the measured/inferred distinction. A path drawn
 * from zone membership is a statement about which room somebody was in, not
 * about the route they took across it — and if the twin renders the two
 * identically, it is making up a walk the same way the report used to make up an
 * ROI ratio. Plausible, unfalsifiable by whoever is watching, and wrong.
 */

import { describe, expect, it } from "vitest";

import type { RealmEvent, RealmEventType } from "@/lib/contracts";
import { buildReplay, polygonCentre } from "./replay";

const T0 = Date.parse("2026-08-04T10:00:00Z");

let seq = 0;
function ev(
  type: RealmEventType,
  payload: Record<string, unknown>,
  atMs: number
): RealmEvent {
  seq++;
  return {
    seq,
    eventId: `e-${seq}`,
    tenantId: "t_test",
    sessionId: "s_1",
    type,
    payload: payload as never,
    occurredAt: atMs,
    recordedAt: atMs,
  };
}

/** A detection whose centroid lands on (x, y) in normalized coords. */
const detection = (anonId: string, x: number, y: number, at: number) =>
  ev(
    "perception.detection",
    {
      anonId,
      bbox: [x * 1000 - 10, y * 1000 - 10, x * 1000 + 10, y * 1000 + 10],
      frameWidth: 1000,
      frameHeight: 1000,
      confidence: 0.9,
    },
    at
  );

const enter = (anonId: string, zoneId: string, at: number) =>
  ev("spatial.zone_enter", { anonId, zoneId }, at);
const exit = (anonId: string, zoneId: string, at: number) =>
  ev("spatial.zone_exit", { anonId, zoneId }, at);

const ZONES = [
  { id: "z_left", polygon: [[0, 0], [0.4, 0], [0.4, 1], [0, 1]] as [number, number][] },
  { id: "z_right", polygon: [[0.6, 0], [1, 0], [1, 1], [0.6, 1]] as [number, number][] },
];

describe("polygonCentre", () => {
  it("averages the vertices", () => {
    expect(polygonCentre(ZONES[0].polygon!)).toEqual([0.2, 0.5]);
  });

  it("falls back to the middle for an empty polygon rather than NaN", () => {
    expect(polygonCentre([])).toEqual([0.5, 0.5]);
  });
});

describe("buildReplay — where a position comes from", () => {
  it("uses detections when the log has them, and marks the track measured", () => {
    const replay = buildReplay(
      [detection("P1", 0.2, 0.3, T0), detection("P1", 0.8, 0.7, T0 + 10_000)],
      ZONES
    );

    expect(replay.tracks).toHaveLength(1);
    expect(replay.tracks[0].inferred).toBe(false);
    expect(replay.inferredCount).toBe(0);

    const [pos] = replay.positionsAt(5); // halfway
    expect(pos.x).toBeCloseTo(0.5, 3);
    expect(pos.y).toBeCloseTo(0.5, 3);
  });

  it("falls back to zone centres, and marks the track inferred", () => {
    // This is what makes the demo replayable at all — its seed emits no
    // detections.
    const replay = buildReplay(
      [enter("P1", "z_left", T0), exit("P1", "z_left", T0 + 10_000)],
      ZONES
    );

    expect(replay.tracks[0].inferred).toBe(true);
    expect(replay.inferredCount).toBe(1);

    const [pos] = replay.positionsAt(5);
    expect(pos.x).toBeCloseTo(0.2, 3); // the left zone's centre
    expect(pos.inferred).toBe(true);
  });

  it("prefers detections over zone events for the same person", () => {
    // A measured position is always better than a guessed one, even when the
    // zone events are more numerous.
    const replay = buildReplay(
      [
        enter("P1", "z_left", T0),
        detection("P1", 0.35, 0.9, T0 + 1000),
        detection("P1", 0.36, 0.9, T0 + 2000),
        exit("P1", "z_left", T0 + 3000),
      ],
      ZONES
    );

    expect(replay.tracks[0].inferred).toBe(false);
    const [pos] = replay.positionsAt(1);
    expect(pos.y).toBeCloseTo(0.9, 3); // where the camera saw them, not the centre
  });

  it("mixes both kinds in one session", () => {
    const replay = buildReplay(
      [
        detection("P1", 0.2, 0.2, T0),
        detection("P1", 0.3, 0.3, T0 + 5000),
        enter("P2", "z_right", T0),
        exit("P2", "z_right", T0 + 5000),
      ],
      ZONES
    );

    const byId = Object.fromEntries(replay.tracks.map((t) => [t.id, t.inferred]));
    expect(byId).toEqual({ P1: false, P2: true });
    expect(replay.inferredCount).toBe(1);
  });

  it("skips a zone nobody drew, rather than putting people at the origin", () => {
    const replay = buildReplay([enter("P1", "z_undrawn", T0)], ZONES);
    expect(replay.tracks).toHaveLength(0);
  });
});

describe("buildReplay — the timeline", () => {
  it("takes its bounds from the events, not from a constant", () => {
    // The old twin ran on `SESSION_DURATION = 620`, which was the length of the
    // mock data and of nothing else.
    const replay = buildReplay(
      [detection("P1", 0.2, 0.2, T0), detection("P1", 0.3, 0.3, T0 + 3_600_000)],
      ZONES
    );

    expect(replay.startAt).toBe(T0);
    expect(replay.durationSec).toBe(3600);
  });

  it("shows only the people present at that moment", () => {
    // The twin answers "who was in the room then", not "who attended".
    const replay = buildReplay(
      [
        detection("P1", 0.2, 0.2, T0),
        detection("P1", 0.2, 0.2, T0 + 10_000),
        detection("P2", 0.8, 0.8, T0 + 60_000),
        detection("P2", 0.8, 0.8, T0 + 70_000),
      ],
      ZONES
    );

    expect(replay.positionsAt(5).map((p) => p.id)).toEqual(["P1"]);
    expect(replay.positionsAt(65).map((p) => p.id)).toEqual(["P2"]);
    expect(replay.positionsAt(30)).toEqual([]); // between them, nobody
  });

  it("holds a single-detection person at their one confirmed position", () => {
    const replay = buildReplay([detection("P1", 0.4, 0.6, T0)], ZONES);
    const [pos] = replay.positionsAt(0);
    expect(pos.x).toBeCloseTo(0.4, 3);
    expect(pos.moving).toBe(false);
  });

  it("is empty rather than broken for a session with no events", () => {
    const replay = buildReplay([], ZONES);
    expect(replay.tracks).toEqual([]);
    expect(replay.durationSec).toBe(0);
    expect(replay.positionsAt(0)).toEqual([]);
  });

  it("gives a person the same colour across the whole replay", () => {
    const a = buildReplay([detection("P-042", 0.2, 0.2, T0)], ZONES);
    const b = buildReplay([detection("P-042", 0.9, 0.9, T0)], ZONES);
    expect(a.tracks[0].color).toBe(b.tracks[0].color);
  });
});
