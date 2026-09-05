/**
 * The composer's pure parts, as executable claims.
 *
 * The screen itself is a form; what is worth pinning is the three things about
 * it that can be wrong without looking wrong — an id that collides, a document
 * that the backend will refuse, and a field carried between two members of a
 * closed union that no longer accepts it.
 */

import { describe, expect, it } from "vitest";

import {
  blankRule,
  mintRuleId,
  switchAction,
  switchCondition,
} from "./RuleComposer";
import { describeRule, ruleProblems } from "@/lib/contracts/rules";

describe("a new rule", () => {
  it("starts as a document the backend would accept, once it has a name", () => {
    const rule = { ...blankRule(mintRuleId("entrance crowding")), name: "Crowding" };
    expect(ruleProblems(rule)).toEqual([]);
  });

  it("is refused while it has no name, which is what disables Arm", () => {
    expect(ruleProblems(blankRule(mintRuleId("x")))).toContain("a rule needs a name");
  });

  it("reads back as a sentence", () => {
    // ADR-002's "operator confirms" half: what is armed should be legible
    // without reading JSON.
    const rule = { ...blankRule("r_x_000000"), name: "Crowding" };
    expect(describeRule(rule)).toContain("any dwell");
  });
});

describe("minting an id", () => {
  it("is distinct every time, so composing twice is two candidates", () => {
    // Colliding ids would make the second silently replace the first the moment
    // Arm is pressed, since PUT is create-or-replace.
    const ids = new Set(Array.from({ length: 50 }, () => mintRuleId("Entrance crowding")));
    expect(ids.size).toBe(50);
  });

  it("is prefixed r_, which is not the presets' prefix", () => {
    // `presets.ts` prefixes `preset_` "so a saved preset is distinguishable
    // from a rule an operator wrote". This is the other half of that.
    const id = mintRuleId("Entrance crowding");
    expect(id.startsWith("r_")).toBe(true);
    expect(id.startsWith("preset_")).toBe(false);
    expect(id).toMatch(/^r_entrance_crowding_[0-9a-f]{6}$/);
  });

  it("survives a name with nothing sluggable in it", () => {
    expect(mintRuleId("🎪 !!! ")).toMatch(/^r_rule_[0-9a-f]{6}$/);
  });
});

describe("switching the condition", () => {
  it("does not carry a count onto a condition that forbids one", () => {
    // The backend refuses unknown keys on a condition now, so a merge here is a
    // 422 on a rule the operator can see is fine.
    const threshold = switchCondition({ type: "any", zoneId: null }, "threshold");
    const quiet = switchCondition(threshold, "none");
    expect(quiet).toEqual({ type: "none", windowSec: 600, zoneId: null });
    expect("count" in quiet).toBe(false);
  });

  it("keeps the zone, which means the same thing in all three", () => {
    const kept = switchCondition({ type: "any", zoneId: "z_entry" }, "threshold");
    expect(kept.zoneId).toBe("z_entry");
  });
});

describe("switching the action", () => {
  it("carries the message across, because it is what the operator wrote", () => {
    const prompt = switchAction({ type: "log", message: "Greet them" }, "staff_prompt");
    expect(prompt).toMatchObject({ type: "staff_prompt", message: "Greet them" });
  });

  it("never carries a destination onto a different action", () => {
    // A channel names somewhere. Carried onto a webhook it would point a rule at
    // an address the operator chose for something else.
    const webhook = switchAction(
      { type: "slack", channel: "#ops", message: "busy" },
      "webhook"
    );
    expect(webhook).toEqual({ type: "webhook", url: "" });
    expect(JSON.stringify(webhook)).not.toContain("#ops");
  });
});
