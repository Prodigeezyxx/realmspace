/**
 * The wizard's refusals, as sentences.
 *
 * The claim under test is not "step 3 blocks without a template" — the old
 * boolean did that correctly. It is that an operator is told which field, in
 * words that point at the screen.
 */

import { describe, expect, it } from "vitest";

import { blockedReason, type WizardDraftState } from "./wizard-validation";
import type { Touchpoint, Zone } from "./types";

function zone(over: Partial<Zone> = {}): Zone {
  return { id: "z1", name: "Entry Arch", type: "entry", ...over } as Zone;
}
function touchpoint(over: Partial<Touchpoint> = {}): Touchpoint {
  return { id: "tp1", name: "Mirror", type: "ar_mirror", ...over } as Touchpoint;
}

/** A draft that satisfies every step. Each test breaks exactly one thing. */
function complete(over: Partial<WizardDraftState> = {}): WizardDraftState {
  return {
    type: "brand_activation",
    name: "Northbound — Manchester",
    venue: "Mayfield Depot",
    startAtLocal: "2026-09-01T10:00",
    prefabId: "pf_island",
    zones: [zone()],
    touchpoints: [touchpoint()],
    ...over,
  };
}

describe("nothing is blocked when nothing is missing", () => {
  it("lets every step through", () => {
    for (const step of [1, 2, 3, 4, 5]) {
      expect(blockedReason(step, complete())).toBeNull();
    }
  });
});

describe("step 3 — the one the Phase 6 walk found", () => {
  it("explains the seeded zones, not just the missing template", () => {
    // `selectType()` hydrates zones from the experience type at step 1, so this
    // screen arrives looking complete. A message that said only "choose a
    // template" would send the operator looking for something they thought
    // they had already done — which is how the old greyed-out button read.
    const reason = blockedReason(3, complete({ prefabId: null }));

    expect(reason).toContain("space template");
    expect(reason).toContain("experience type");
    expect(reason).toContain("zones");
  });

  it("asks for a zone when there genuinely is none", () => {
    expect(blockedReason(3, complete({ zones: [] }))).toContain("at least one zone");
  });

  it("counts unnamed zones rather than saying 'some'", () => {
    expect(blockedReason(3, complete({ zones: [zone({ name: "  " })] }))).toContain(
      "One zone has no name"
    );
    expect(
      blockedReason(3, complete({ zones: [zone({ name: "" }), zone({ id: "z2", name: "" })] }))
    ).toContain("2 zones have no name");
  });

  it("names the template first, because it is the one that blocks silently", () => {
    // Both wrong: the template is the surprising requirement, the unnamed zone
    // is visible on the screen as an empty box.
    const reason = blockedReason(
      3,
      complete({ prefabId: null, zones: [zone({ name: "" })] })
    );
    expect(reason).toContain("space template");
  });
});

describe("the other three steps, which had the same silent disable", () => {
  it("names both missing fields on step 1", () => {
    expect(blockedReason(1, complete({ type: null, name: "" }))).toBe(
      "Choose an experience type and a name for this activation."
    );
  });

  it("names only what is actually missing", () => {
    expect(blockedReason(1, complete({ name: "   " }))).toBe(
      "Choose a name for this activation."
    );
    expect(blockedReason(2, complete({ startAtLocal: "" }))).toBe(
      "Fill in a start date and time."
    );
    expect(blockedReason(2, complete({ venue: "" }))).toBe("Fill in the venue.");
  });

  it("catches a touchpoint whose name was cleared", () => {
    expect(blockedReason(4, complete({ touchpoints: [touchpoint({ name: " " })] }))).toBe(
      "One touchpoint has no name."
    );
  });

  it("does not block step 4 when there are no touchpoints at all", () => {
    // Touchpoints are optional — a booth with no interactive surfaces is a
    // booth, and Engagement reports a marked zero for it.
    expect(blockedReason(4, complete({ touchpoints: [] }))).toBeNull();
  });
});
