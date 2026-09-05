/**
 * The dry run, as executable claims.
 *
 * ADR-002 keeps one rule *language* and two runners of it. That only holds if
 * the preview reaches the same verdict as the edge on the same document, so
 * every case here has a twin in `backend/tests/test_rules.py` — same rule, same
 * events, same expected number of firings. When one changes, both fail.
 *
 *   would_fire_on_five_people        ← test_threshold_fires_when_the_count_is_reached
 *   counts_people_not_events         ← test_one_restless_visitor_is_not_five_people
 *   ignores_short_dwells             ← test_a_short_dwell_does_not_count_towards_a_long_one
 *   window_excludes_the_stragglers   ← test_events_outside_the_window_do_not_count
 *   cooldown_is_event_time           ← test_cooldown_expires_on_event_time
 */

import { describe, expect, it } from "vitest";

import { previewRule } from "./preview";
import type { RealmEvent } from "@/lib/contracts";
import type { RuleDocument } from "@/lib/contracts/rules";

const BASE = Date.UTC(2026, 7, 11, 10, 0, 0);

const RULE: RuleDocument = {
  ruleId: "r_entry_crowd",
  name: "Entrance crowding",
  triggerType: "spatial.dwell",
  triggerZoneId: null,
  condition: {
    type: "threshold",
    count: 5,
    windowSec: 30,
    zoneId: "z_entry",
    minDwellSec: 30,
  },
  action: { type: "log", message: "5 at entrance" },
  enabled: true,
  cooldownSec: 60,
};

let seq = 0;

function dwell(
  offsetSec: number,
  anonId: string,
  { zoneId = "z_entry", durationSec = 45 } = {}
): RealmEvent {
  seq += 1;
  return {
    seq,
    eventId: `e-${seq}`,
    tenantId: "t_test",
    sessionId: "s_preview",
    type: "spatial.dwell",
    payload: { anonId, zoneId, durationSec },
    occurredAt: BASE + offsetSec * 1000,
    recordedAt: BASE + offsetSec * 1000,
  } as unknown as RealmEvent;
}

function crowd(startSec = 0, people = 5, opts = {}): RealmEvent[] {
  return Array.from({ length: people }, (_, i) =>
    dwell(startSec + i, `P-${i}`, opts)
  );
}

describe("previewing a threshold rule", () => {
  it("would fire once on five people inside the window", () => {
    const preview = previewRule(RULE, crowd());
    expect(preview.firings).toHaveLength(1);
    expect(preview.firings[0].observed).toBe(5);
    expect(preview.firings[0].countedBy).toBe("people");
  });

  it("counts people, not events", () => {
    // One restless visitor emitting six dwells is one person. Counting events
    // would fire a five-person rule on them alone — the same trap the edge
    // evaluator has its own test for.
    const restless = Array.from({ length: 6 }, (_, i) => dwell(i, "P-001"));
    expect(previewRule(RULE, restless).firings).toHaveLength(0);
  });

  it("ignores dwells shorter than minDwellSec", () => {
    const passersby = crowd(0, 5, { durationSec: 2 });
    expect(previewRule(RULE, passersby).firings).toHaveLength(0);
  });

  it("does not count events outside the window", () => {
    const events = [...crowd(0, 4), dwell(90, "P-004")];
    expect(previewRule(RULE, events).firings).toHaveLength(0);
  });

  it("does not count another zone", () => {
    expect(previewRule(RULE, crowd(0, 5, { zoneId: "z_lounge" })).firings).toHaveLength(0);
  });

  it("applies cooldown on event time, not on the clock this runs at", () => {
    // The property that makes a preview of yesterday's log meaningful: the
    // second crowd is 10s later by the edge clock and inside the 60s cooldown,
    // however long ago the whole thing happened.
    const events = [...crowd(0), ...crowd(10)];
    expect(previewRule(RULE, events).firings).toHaveLength(1);
  });

  it("fires again once the cooldown has expired in event time", () => {
    const events = [...crowd(0), ...crowd(120)];
    expect(previewRule(RULE, events).firings).toHaveLength(2);
  });

  it("reports how many events it looked at, so an empty result is legible", () => {
    // "Would have fired 0×" and "there was nothing to fire on" are different
    // answers, and only one of them means the rule is wrong.
    const preview = previewRule(RULE, crowd(0, 3));
    expect(preview.firings).toHaveLength(0);
    expect(preview.considered).toBe(3);
  });
});

describe("what the preview refuses to guess at", () => {
  it("will not preview a `none` rule", () => {
    // This browser's log is a partial mirror of the bus, so an apparent silence
    // here can simply be an event that has not been mirrored. Showing zero
    // firings would read as "this rule never fires".
    const quiet: RuleDocument = {
      ...RULE,
      condition: { type: "none", windowSec: 60 },
    };
    const preview = previewRule(quiet, crowd());
    expect(preview.firings).toHaveLength(0);
    expect(preview.unsupported).toBeTruthy();
  });
});

describe("previewing a payload filter", () => {
  const OCCUPANCY: RuleDocument = {
    ruleId: "preset_capacity_z_entry",
    name: "Entry Arch at capacity",
    triggerType: "spatial.occupancy",
    triggerZoneId: "z_entry",
    condition: { type: "any", payloadEquals: { status: "over" } },
    action: { type: "staff_prompt", message: "Entry Arch is at capacity" },
    enabled: true,
    cooldownSec: 60,
  };

  function crossing(offsetSec: number, status: "over" | "cleared"): RealmEvent {
    seq += 1;
    return {
      seq,
      eventId: `e-${seq}`,
      tenantId: "t_test",
      sessionId: "s_preview",
      type: "spatial.occupancy",
      payload: { zoneId: "z_entry", occupancy: 3, capacity: 3, status },
      occurredAt: BASE + offsetSec * 1000,
      recordedAt: BASE + offsetSec * 1000,
    } as unknown as RealmEvent;
  }

  it("would fire on the zone filling and not on it clearing", () => {
    // Twin of `test_the_prompt_is_not_raised_again_when_the_zone_clears`. A dry
    // run that ignored the filter would tell an operator this fires twice as
    // often as it will.
    const preview = previewRule(OCCUPANCY, [
      crossing(0, "over"),
      crossing(120, "cleared"),
    ]);
    expect(preview.firings).toHaveLength(1);
    expect(preview.considered).toBe(1);
  });
});
