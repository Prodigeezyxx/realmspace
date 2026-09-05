/**
 * The presets, as executable claims.
 *
 * The point of these is not that compilation "works" — it is that the eight
 * definitions no longer constitute a second rule language. Two things have to
 * hold for that: everything that *is* a rule compiles to a document the backend
 * would accept, and everything that is not says so instead of being padded into
 * the shape.
 */

import { describe, expect, it } from "vitest";

import { agentDefinitions } from "./definitions";
import { capacityRules, notARuleReason, toRule } from "./presets";
import { ruleProblems } from "@/lib/contracts/rules";

function byId(id: string) {
  const found = agentDefinitions.find((a) => a.id === id);
  if (!found) throw new Error(`no definition ${id}`);
  return found;
}

describe("compiling a preset", () => {
  it("turns the dwell agent's private trigger name into a bus event", () => {
    // `person_dwell_exceeded` was never an event; only the browser runtime knew
    // what it meant. Writing the mapping down is most of what ending the
    // split-brain amounts to.
    const rule = toRule(byId("dwell"));
    expect(rule?.triggerType).toBe("spatial.dwell");
  });

  it("keeps the dwell threshold as a condition, since every stay emits an event", () => {
    const rule = toRule(byId("dwell"));
    expect(rule?.condition).toMatchObject({
      type: "threshold",
      count: 1,
      windowSec: 30,
      minDwellSec: 30,
    });
  });

  it("converts a cooldown in milliseconds without rounding it down", () => {
    // A cooldown shorter than the operator asked for is the failure that floods
    // a Slack channel, so the conversion rounds up.
    const rule = toRule(byId("alert"));
    expect(rule?.cooldownSec).toBe(60);
  });

  it("compiles a stream-only preset to `log`, not to an action it never had", () => {
    // `AgentOutput` is a delivery mechanism, not an action. Silently upgrading
    // `stream` to a Slack post would be the compiler arming a rule on the
    // operator's behalf.
    const rule = toRule(byId("dwell"));
    expect(rule?.action.type).toBe("log");
  });

  it("produces documents the backend's own validator would accept", () => {
    for (const definition of agentDefinitions) {
      const rule = toRule(definition);
      if (!rule) continue;
      expect(ruleProblems(rule)).toEqual([]);
    }
  });

  it("gives every preset a distinct id, so arming two does not overwrite one", () => {
    const ids = agentDefinitions
      .map(toRule)
      .filter((r) => r !== null)
      .map((r) => r!.ruleId);
    expect(new Set(ids).size).toBe(ids.length);
  });
});

describe("the definitions that are not rules", () => {
  it.each(["heatmap", "layout", "nlq", "report", "zone"])(
    "%s does not compile, and says why",
    (id) => {
      const definition = byId(id);
      expect(toRule(definition)).toBeNull();
      expect(notARuleReason(definition)).toBeTruthy();
    }
  );

  it("gives a reason for every definition that does not compile", () => {
    // The finding, not a gap: a list mixing "ping ops when the entrance is
    // crowded" with "regenerate the heatmap" reads as one kind of thing and is
    // two. Anything without a reason would be an unexplained blank on the
    // composer screen.
    for (const definition of agentDefinitions) {
      const compiled = toRule(definition);
      expect(compiled === null).toBe(notARuleReason(definition) !== null);
    }
  });
});

describe("the crowding presets", () => {
  const zones = [
    { id: "z_entry", name: "Entry Arch", capacity: 10 },
    { id: "z_lounge", name: "Lounge", capacity: null },
    { id: "z_wall", name: "Product Wall" },
  ];

  it("offers one per zone the operator gave a capacity, and none for the rest", () => {
    // A threshold nobody set is not a threshold — the same refusal
    // `consumers/occupancy.py` makes about emitting anything for those zones.
    const rules = capacityRules(zones);
    expect(rules).toHaveLength(1);
    expect(rules[0].triggerZoneId).toBe("z_entry");
  });

  it("filters on the status, so the prompt is not raised when the zone clears", () => {
    // `spatial.occupancy` carries both ends of a crossing. Without the filter
    // the same prompt — "Entry Arch is at capacity" — would be raised at the
    // moment it stops being true.
    const [rule] = capacityRules(zones);
    expect(rule.condition.type).toBe("any");
    expect(
      rule.condition.type !== "none" ? rule.condition.payloadEquals : null
    ).toEqual({ status: "over" });
  });

  it("compiles to a document the backend would accept", () => {
    for (const rule of capacityRules(zones)) {
      expect(ruleProblems(rule)).toEqual([]);
    }
  });

  it("is empty for an activation where nobody set a capacity", () => {
    expect(capacityRules([{ id: "z_a", name: "A" }])).toEqual([]);
  });
});
