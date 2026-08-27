/**
 * What this browser will and will not read.
 *
 * The refusals matter more than the acceptances. Every case below was silently
 * accepted before, and each produced a specific wrong number on a client's
 * report rather than an error — which is why the assertions name the figure the
 * bad payload would have corrupted.
 */

import { describe, expect, it } from "vitest";

import { validatePayload } from "./validate";

describe("payloads a reader cannot use", () => {
  it("refuses a dwell with no zone — it has nothing to attribute attention to", () => {
    const check = validatePayload("spatial.dwell", {
      anonId: "P-1",
      durationSec: 92,
    });
    expect(check.ok).toBe(false);
    expect(check.ok === false && check.reasons).toEqual(["zoneId is missing"]);
  });

  it("refuses a zone_enter with no person — `persons.add(undefined)` is a phantom visitor", () => {
    const check = validatePayload("spatial.zone_enter", { zoneId: "z_a" });
    expect(check.ok).toBe(false);
    expect(check.ok === false && check.reasons).toEqual(["anonId is missing"]);
  });

  it("refuses NaN, which is the one value that survives arithmetic", () => {
    // The shape defect 6 took: a single unreadable duration turned average
    // dwell, dwell-weighted attention and every per-visitor cost into `NaN`.
    const check = validatePayload("spatial.dwell", {
      anonId: "P-1",
      zoneId: "z_a",
      durationSec: Number.NaN,
    });
    expect(check.ok).toBe(false);
    expect(check.ok === false && check.reasons).toEqual(["durationSec is NaN"]);
  });

  it("refuses an empty string, which reads as a person until it is divided by", () => {
    const check = validatePayload("spatial.passby", { anonId: "  " });
    expect(check.ok).toBe(false);
    expect(check.ok === false && check.reasons).toEqual(["anonId is empty"]);
  });

  it("names every fault at once, not the first", () => {
    const check = validatePayload("spatial.dwell", {});
    expect(check.ok === false && check.reasons).toEqual([
      "anonId is missing",
      "zoneId is missing",
      "durationSec is missing",
    ]);
  });

  it("refuses a payload that is not an object at all", () => {
    expect(validatePayload("spatial.dwell", null).ok).toBe(false);
    expect(validatePayload("spatial.dwell", "P-1").ok).toBe(false);
    expect(validatePayload("spatial.dwell", []).ok).toBe(false);
  });

  it("refuses a group with no members — `size` would disagree with the graph", () => {
    const check = validatePayload("spatial.group", {
      groupId: "g-1",
      memberAnonIds: [],
      size: 2,
    });
    expect(check.ok === false && check.reasons).toEqual(["memberAnonIds is empty"]);
  });
});

describe("what it deliberately lets through", () => {
  it("accepts a type nobody has written a reader for yet", () => {
    // The taxonomy is additive (`event-bus-spec.md` §3). A browser that
    // predates an event type must pass it through, not refuse it — the
    // alternative is that adding a producer breaks every deployed dashboard.
    expect(validatePayload("spatial.gaze", { nothing: "familiar" }).ok).toBe(true);
    expect(validatePayload("insight.generated", {}).ok).toBe(true);
  });

  it("accepts a detection with no frame size", () => {
    // The backend tracker dead-letters one of these, and that is its business:
    // by the time a detection reaches this browser the backend has already
    // accepted it, and refusing it here would drop history we are only reading.
    expect(
      validatePayload("perception.detection", { anonId: "P-1", bbox: [0, 0, 1, 1] }).ok
    ).toBe(true);
  });

  it("accepts a zero duration — a sub-second dwell is a measurement, not an absence", () => {
    expect(
      validatePayload("spatial.dwell", { anonId: "P-1", zoneId: "z_a", durationSec: 0 })
        .ok
    ).toBe(true);
  });

  it("ignores fields no reader dereferences", () => {
    // `at`, `reason`, `enteredAt` are all optional in the contract and absent
    // from plenty of real events. Requiring them would refuse the backend's own
    // output, which is what the fixture test in `bus/contract.test.ts` guards.
    expect(validatePayload("spatial.zone_exit", { anonId: "P-1", zoneId: "z_a" }).ok).toBe(
      true
    );
  });
});
