/**
 * The drift panel's arithmetic, as executable claims.
 *
 * Small, and load-bearing for the same reason `wire.test.ts` is: the number on
 * screen is the whole point of the event, and getting the sign or the
 * denominator wrong produces a plausible figure rather than an error.
 */

import { describe, expect, it } from "vitest";

import { drift, METRIC_LABELS, type DriftFinding } from "./useDriftEvents";

function finding(observed: number, baseline: number): DriftFinding {
  return {
    seq: 1,
    sessionId: "s_test",
    occurredAt: "2026-08-19T09:20:00+00:00",
    cameraId: "cam-1",
    metric: "confidence_mean",
    observed,
    baseline,
    severity: "critical",
  };
}

describe("drift", () => {
  it("is a signed fraction of the baseline, not of the observation", () => {
    // 0.90 → 0.45 is a 50% fall. Dividing by the observation instead would
    // report 100%, which is a different and much more alarming claim.
    expect(drift(finding(0.45, 0.9))).toBeCloseTo(-0.5);
  });

  it("keeps the sign, so an improvement never reads as a fall", () => {
    expect(drift(finding(0.99, 0.9))).toBeGreaterThan(0);
  });

  it("returns zero rather than Infinity when there is no baseline", () => {
    // The consumer will not emit this — it skips a zero baseline for exactly
    // this reason — but a hook that renders `Infinity%` on a malformed event is
    // a hook that fails in front of an operator rather than in a test.
    expect(drift(finding(0.5, 0))).toBe(0);
  });
});

describe("METRIC_LABELS", () => {
  it("names both metrics the consumer actually emits", () => {
    expect(Object.keys(METRIC_LABELS).sort()).toEqual([
      "confidence_mean",
      "track_length",
    ]);
  });

  it("does not name detection_rate", () => {
    // §3 lists it; the consumer refuses it because a falling detection rate is
    // confounded with the room emptying. A label here would be the first step
    // back towards emitting it.
    expect(METRIC_LABELS.detection_rate).toBeUndefined();
  });
});
