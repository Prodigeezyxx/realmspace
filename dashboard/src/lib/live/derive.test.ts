/**
 * The live view's derived figures, as executable claims.
 *
 * `presentNow` is the one number the scorecard cannot give, and it is the one an
 * operator checks against the room in front of them — so being wrong here is
 * being wrong in the most visible way the product has.
 */

import { describe, expect, it } from "vitest";

import type { RealmEvent, RealmEventType } from "@/lib/contracts";
import { lastEventAt, presentNow, PROMPT_TTL_MS, staffPrompts } from "./derive";

const T0 = Date.parse("2026-08-04T10:00:00Z");

let seq = 0;
function ev(
  type: RealmEventType,
  payload: Record<string, unknown>,
  atMs = T0
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
const enter = (anonId: string, zoneId: string, at = T0) =>
  ev("spatial.zone_enter", { anonId, zoneId }, at);
const exit = (anonId: string, zoneId: string, at = T0) =>
  ev("spatial.zone_exit", { anonId, zoneId }, at);

describe("presentNow", () => {
  it("counts an entry with no matching exit", () => {
    expect(presentNow([enter("P1", "z_a"), enter("P2", "z_a")])).toBe(2);
  });

  it("an exit removes them", () => {
    expect(presentNow([enter("P1", "z_a"), exit("P1", "z_a")])).toBe(0);
  });

  it("counts people, not zone occupancies", () => {
    // Walking from the entrance to the product wall is exit-then-enter. One
    // person is present, not two — the figure exists so an operator can compare
    // it with the room, and double-counting a walk across it destroys that.
    const events = [
      enter("P1", "z_entry"),
      exit("P1", "z_entry"),
      enter("P1", "z_product"),
    ];
    expect(presentNow(events)).toBe(1);
  });

  it("someone who left and came back is present again", () => {
    const events = [
      enter("P1", "z_a", T0),
      exit("P1", "z_a", T0 + 1000),
      enter("P1", "z_a", T0 + 2000),
    ];
    expect(presentNow(events)).toBe(1);
  });

  it("a dropout or session-end exit leaves them gone", () => {
    // Those exits have no following enter, which is exactly the difference
    // between closing a visit and moving between zones.
    const events = [
      enter("P1", "z_a", T0),
      ev("spatial.zone_exit", { anonId: "P1", zoneId: "z_a", reason: "dropout" }, T0 + 1000),
    ];
    expect(presentNow(events)).toBe(0);
  });

  it("is zero on an empty log rather than throwing", () => {
    expect(presentNow([])).toBe(0);
  });
});

describe("lastEventAt", () => {
  it("reports the newest event's time", () => {
    expect(lastEventAt([enter("P1", "z_a", T0), enter("P2", "z_a", T0 + 5000)])).toBe(
      T0 + 5000
    );
  });

  it("is null for an empty log, not zero", () => {
    // A feed that stopped and a feed that never started are different, and the
    // live screen has to be able to say which.
    expect(lastEventAt([])).toBeNull();
  });
});

describe("staffPrompts", () => {
  const prompt = (message: string, at: number, priority = "normal") =>
    ev(
      "rule.staff_prompt",
      {
        message,
        zoneId: "z_entry",
        priority,
        ruleId: "r_entry_crowd",
        ruleName: "Entrance crowding",
      },
      at
    );

  it("reads a prompt off the log", () => {
    const prompts = staffPrompts([prompt("Greet the group", T0)], T0);
    expect(prompts).toHaveLength(1);
    expect(prompts[0].message).toBe("Greet the group");
    expect(prompts[0].ruleName).toBe("Entrance crowding");
  });

  it("drops a prompt once it is stale", () => {
    // A prompt still on screen twenty minutes later is not information, it is
    // furniture — and it teaches the floor staff to stop reading the panel.
    const old = prompt("Greet the group", T0);
    expect(staffPrompts([old], T0 + PROMPT_TTL_MS + 1)).toHaveLength(0);
  });

  it("shows the newest first, because that is the one to act on", () => {
    const events = [
      prompt("older", T0),
      prompt("newest", T0 + 30_000),
      prompt("middle", T0 + 10_000),
    ];
    expect(staffPrompts(events, T0 + 30_000).map((p) => p.message)).toEqual([
      "newest",
      "middle",
      "older",
    ]);
  });

  it("ignores every other kind of event", () => {
    // Deliberately not derived from `rule.fired` by checking what its action
    // happens to be — that would be a second implementation of the rule spec in
    // the browser, which is the split-brain ADR-002 exists to end.
    const events = [
      ev("rule.fired", { ruleId: "r_x", action: { type: "staff_prompt" } }, T0),
      enter("P1", "z_a"),
    ];
    expect(staffPrompts(events, T0)).toHaveLength(0);
  });

  it("renders the same panel in a replay as it did live", () => {
    // `now` is a parameter rather than Date.now() precisely so this holds: a
    // replay of an afternoon shows what was on screen at each moment.
    const events = [prompt("Greet the group", T0)];
    expect(staffPrompts(events, T0 + 1000)).toHaveLength(1);
    expect(staffPrompts(events, Date.now())).toHaveLength(0);
  });
});
