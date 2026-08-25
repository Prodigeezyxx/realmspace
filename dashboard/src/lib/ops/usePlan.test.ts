import { describe, expect, it } from "vitest";

import { describeLimit, type PlanLimit } from "./usePlan";

function limit(over: Partial<PlanLimit>): PlanLimit {
  return { limit: null, used: null, counted: true, note: null, ...over };
}

describe("describeLimit", () => {
  it("shows usage against a real ceiling", () => {
    expect(describeLimit(limit({ limit: 4, used: 3 }))).toEqual({
      text: "3 of 4",
      atCap: false,
    });
  });

  it("flags being at the ceiling, because the next save is the one refused", () => {
    expect(describeLimit(limit({ limit: 2, used: 2 })).atCap).toBe(true);
  });

  it("does not call an unenforced limit 'unlimited'", () => {
    // `gtm.md` states no integration count for any tier. That is a silence in
    // the pricing sheet, not a generous term, and rendering it as "unlimited"
    // would tell a client something nobody agreed to.
    expect(describeLimit(limit({ limit: null, used: 1 })).text).toBe(
      "1 · no limit set"
    );
  });

  it("does not render an uncounted usage as zero", () => {
    // Ask questions: the only record is a `cost.metered` event the
    // deterministic provider never writes, so 0 would look like a client who
    // had never asked anything.
    expect(describeLimit(limit({ counted: false, used: null }))).toEqual({
      text: "not counted",
      atCap: false,
    });
  });
});
