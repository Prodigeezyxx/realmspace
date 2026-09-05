/**
 * The report's derived views, as executable claims.
 *
 * The theme running through these: a derivation must never manufacture a
 * favourable shape. The funnel keeps the operator's declared order even when
 * that makes the activation look worse, and every "no data" path returns
 * nothing rather than a plausible placeholder.
 */

import { describe, expect, it } from "vitest";

import type { RealmEvent, RealmEventType } from "@/lib/contracts";
import {
  buildFunnel,
  buildMoments,
  buildRecommendations,
  buildSurfaceRows,
  buildZoneRows,
  hourlyAvgDwell,
  hourlyLeads,
  hourlyVisitors,
  buildGazeRows,
  scoringChanges,
} from "./derive";

const T0 = Date.parse("2026-08-03T10:00:00Z");

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

function dwell(anonId: string, zoneId: string, durationSec: number, at = T0) {
  return ev("spatial.dwell", { anonId, zoneId, durationSec }, at);
}
function enter(anonId: string, zoneId: string, at = T0) {
  return ev("spatial.zone_enter", { anonId, zoneId }, at);
}

const ZONES = [
  { id: "z_entry", name: "Entry", funnelOrder: 0 },
  { id: "z_mirror", name: "Mirror Room", funnelOrder: 1 },
  { id: "z_product", name: "Bottle Wall", funnelOrder: 2 },
];

describe("buildFunnel", () => {
  it("keeps the operator's declared order even when traffic contradicts it", () => {
    // More people reach the product wall than the mirror. Sorting by traffic
    // would hide that by reordering the steps — every activation would then
    // show a monotonic funnel and no drop-off would ever be visible.
    const events = [
      dwell("P1", "z_entry", 10),
      dwell("P2", "z_entry", 10),
      dwell("P3", "z_entry", 10),
      dwell("P1", "z_mirror", 90),
      dwell("P1", "z_product", 60),
      dwell("P2", "z_product", 60),
    ];

    const funnel = buildFunnel(events, ZONES);
    expect(funnel.map((f) => f.zoneId)).toEqual([
      "z_entry",
      "z_mirror",
      "z_product",
    ]);
    expect(funnel.map((f) => f.visitors)).toEqual([3, 1, 2]);
  });

  it("computes share against the first step and drop against the previous one", () => {
    const events = [
      dwell("P1", "z_entry", 10),
      dwell("P2", "z_entry", 10),
      dwell("P3", "z_entry", 10),
      dwell("P4", "z_entry", 10),
      dwell("P1", "z_mirror", 90),
      dwell("P2", "z_mirror", 90),
    ];
    const funnel = buildFunnel(events, ZONES);

    expect(funnel[0].share).toBe(100);
    expect(funnel[1].share).toBe(50);
    expect(funnel[1].drop).toBe(50);
  });

  it("returns nothing when no zone declares a funnel position", () => {
    // No declared journey means no funnel to report against. Inventing one from
    // traffic order would be a claim the operator never made.
    const events = [dwell("P1", "z_entry", 10)];
    expect(buildFunnel(events, [{ id: "z_entry", name: "Entry" }])).toEqual([]);
  });
});

describe("buildZoneRows", () => {
  it("counts distinct visitors and averages dwell per zone", () => {
    const events = [
      dwell("P1", "z_mirror", 100),
      dwell("P1", "z_mirror", 200), // same person, two visits
      dwell("P2", "z_mirror", 60),
    ];
    const [row] = buildZoneRows(events, ZONES);

    expect(row.visitors).toBe(2);
    expect(row.avgDwellSec).toBe(120); // (100+200+60)/3
  });

  it("orders by aggregate attention, not by headcount", () => {
    const events = [
      dwell("P1", "z_entry", 5),
      dwell("P2", "z_entry", 5),
      dwell("P3", "z_entry", 5),
      dwell("P4", "z_mirror", 600),
    ];
    const rows = buildZoneRows(events, ZONES);
    expect(rows[0].zoneId).toBe("z_mirror");
  });
});

describe("buildSurfaceRows", () => {
  it("counts interactions per surface and scales share to the busiest", () => {
    const events = [
      ev("surface.interaction", { anonId: "P1", surfaceId: "mirror" }),
      ev("surface.interaction", { anonId: "P2", surfaceId: "mirror" }),
      ev("surface.interaction", { anonId: "P3", surfaceId: "quiz" }),
    ];
    expect(
      buildSurfaceRows(events, [{ id: "mirror", label: "AR Mirror" }])
    ).toEqual([
      // The configured one gets the operator's name; the unconfigured one keeps
      // its raw id, which is how a reading from a surface nobody set up shows.
      { surfaceId: "mirror", label: "AR Mirror", interactions: 2, share: 100 },
      { surfaceId: "quiz", label: "quiz", interactions: 1, share: 50 },
    ]);
  });

  it("is empty when nothing emits interactions, rather than zero-filled", () => {
    expect(buildSurfaceRows([dwell("P1", "z_mirror", 60)])).toEqual([]);
  });
});

describe("hourlyVisitors", () => {
  it("counts distinct people per hour and fills quiet hours with zero", () => {
    const H = 3_600_000;
    const events = [
      enter("P1", "z_entry", T0),
      enter("P1", "z_mirror", T0 + 60_000), // same hour, same person
      enter("P2", "z_entry", T0),
      // nothing in hour 2
      enter("P3", "z_entry", T0 + 2 * H),
    ];
    expect(hourlyVisitors(events)).toEqual([2, 0, 1]);
  });
});

describe("hourly series for the sparklines", () => {
  const H = 3_600_000;

  it("averages dwell per hour and keeps quiet hours as zero", () => {
    // Skipping the quiet hour would compress a lull into a straight line and
    // make a dead afternoon look like a steady one.
    const events = [
      dwell("P1", "z_mirror", 100, T0),
      dwell("P2", "z_mirror", 200, T0),
      dwell("P3", "z_mirror", 60, T0 + 2 * H),
    ];
    expect(hourlyAvgDwell(events)).toEqual([150, 0, 60]);
  });

  it("accumulates leads so the curve reads as progress", () => {
    const events = [
      ev("consent.captured", { anonId: "P1" }, T0),
      ev("consent.captured", { anonId: "P2" }, T0 + H),
      ev("identity.resolved", { anonId: "P3" }, T0 + 2 * H),
    ];
    expect(hourlyLeads(events)).toEqual([1, 2, 3]);
  });

  it("returns nothing rather than a flat line when there is no data", () => {
    expect(hourlyAvgDwell([])).toEqual([]);
    expect(hourlyLeads([])).toEqual([]);
  });
});

describe("buildMoments", () => {
  it("reports the actual longest dwell, not a chosen highlight", () => {
    const events = [
      dwell("P1", "z_mirror", 120),
      dwell("P2", "z_product", 840),
      enter("P1", "z_mirror"),
    ];
    const moments = buildMoments(events, ZONES);
    const longest = moments.find((m) => m.title.startsWith("Longest"));

    expect(longest?.title).toContain("14m");
    expect(longest?.title).toContain("Bottle Wall");
  });

  it("stays short when the day was unremarkable, rather than padding", () => {
    expect(buildMoments([], ZONES)).toEqual([]);
  });
});

describe("buildRecommendations", () => {
  it("names the worst drop-off with the figure behind it", () => {
    const funnel = [
      { zoneId: "z_entry", name: "Entry", visitors: 100, share: 100, drop: 0 },
      { zoneId: "z_mirror", name: "Mirror Room", visitors: 40, share: 40, drop: 60 },
    ];
    const [rec] = buildRecommendations(funnel, [], 0.5);

    expect(rec).toContain("Mirror Room");
    expect(rec).toContain("60%");
  });

  it("says nothing when no threshold is crossed", () => {
    // A quiet, healthy activation gets no advice rather than filler. The old
    // page always listed four recommendations regardless of the data.
    const funnel = [
      { zoneId: "z_entry", name: "Entry", visitors: 100, share: 100, drop: 0 },
      { zoneId: "z_mirror", name: "Mirror", visitors: 95, share: 95, drop: 5 },
    ];
    const rows = [
      {
        zoneId: "z_mirror",
        name: "Mirror",
        visitors: 95,
        avgDwellSec: 30,
        reachPct: 95,
      },
      {
        zoneId: "z_entry",
        name: "Entry",
        visitors: 100,
        avgDwellSec: 20,
        reachPct: 100,
      },
    ];
    expect(buildRecommendations(funnel, rows, 0.8)).toEqual([]);
  });
});

describe("buildGazeRows", () => {
  const ZONES = [
    { id: "z_wall", name: "Sponsor Wall" },
    { id: "z_pod", name: "Product Pod" },
  ];

  function gaze(anonId: string, targetId: string, durationSec: number): RealmEvent {
    return {
      seq: 1,
      eventId: `g-${anonId}-${targetId}`,
      tenantId: "t",
      sessionId: "s",
      type: "spatial.gaze",
      payload: { anonId, targetId, durationSec, confidence: 0.8 },
      occurredAt: 0,
      recordedAt: 0,
    } as RealmEvent;
  }

  function entered(anonId: string, zoneId: string): RealmEvent {
    return {
      seq: 1,
      eventId: `e-${anonId}-${zoneId}`,
      tenantId: "t",
      sessionId: "s",
      type: "spatial.zone_enter",
      payload: { anonId, zoneId },
      occurredAt: 0,
      recordedAt: 0,
    } as RealmEvent;
  }

  it("separates the people who looked from the people who walked in", () => {
    // The whole point of the panel. Three looked at the wall; one of them
    // then walked into it, and the funnel already counts that one.
    const rows = buildGazeRows(
      [
        gaze("P-1", "z_wall", 4),
        gaze("P-2", "z_wall", 6),
        gaze("P-3", "z_wall", 5),
        entered("P-3", "z_wall"),
      ],
      ZONES
    );

    expect(rows).toHaveLength(1);
    expect(rows[0].name).toBe("Sponsor Wall");
    expect(rows[0].watchers).toBe(3);
    expect(rows[0].watchersWhoNeverEntered).toBe(2);
    expect(rows[0].attentionSec).toBe(15);
  });

  it("ranks by attention that never converted, not by total attention", () => {
    // A zone twenty people stared at and nobody entered is the finding; one
    // with more total seconds from people who all walked in is not.
    const rows = buildGazeRows(
      [
        gaze("P-1", "z_pod", 60),
        entered("P-1", "z_pod"),
        gaze("P-2", "z_wall", 3),
        gaze("P-3", "z_wall", 3),
      ],
      ZONES
    );

    expect(rows.map((r) => r.zoneId)).toEqual(["z_wall", "z_pod"]);
  });

  it("drops a look with an unreadable duration rather than summing NaN", () => {
    // The rule the whole bus boundary was rebuilt around: one unreadable value
    // must not poison a figure a client is shown.
    const rows = buildGazeRows(
      [gaze("P-1", "z_wall", 5), gaze("P-2", "z_wall", Number.NaN)],
      ZONES
    );

    expect(rows[0].attentionSec).toBe(5);
    expect(rows[0].watchers).toBe(1);
  });

  it("says nothing when nothing looked", () => {
    expect(buildGazeRows([entered("P-1", "z_wall")], ZONES)).toEqual([]);
  });
});

describe("a purged activation is not a quiet day", () => {
  /**
   * The substitution this guards against, in one sentence: after retention
   * empties an old activation's payloads, its report comes back looking exactly
   * like one nobody attended. Telling a client they had a quiet day when the
   * truth is that their window expired is the same class of error the report
   * already separates "nobody came" from "nothing was measuring" to avoid.
   *
   * `useSessionReport` decides this from the events rather than the session's
   * configured dates — the dates are what an operator typed, the events are
   * what happened.
   */
  const FLOOR = Date.parse("2026-06-01T00:00:00Z");

  function at(iso: string): RealmEvent {
    return {
      seq: 1,
      eventId: `e-${iso}`,
      tenantId: "t",
      sessionId: "s",
      type: "spatial.dwell",
      payload: { anonId: "P-1", zoneId: "z_a", durationSec: 10 },
      occurredAt: Date.parse(iso),
      recordedAt: Date.parse(iso),
    } as RealmEvent;
  }

  /** The rule as `useSessionReport` applies it. */
  function isExpired(events: RealmEvent[], floor: number | null): boolean {
    const newest = events.reduce((max, e) => Math.max(max, e.occurredAt), 0);
    return floor != null && events.length > 0 && newest < floor;
  }

  it("calls it expired when everything predates the floor", () => {
    expect(isExpired([at("2026-02-01T10:00:00Z"), at("2026-03-01T10:00:00Z")], FLOOR)).toBe(
      true
    );
  });

  it("does not, when anything at all is inside the window", () => {
    // One recent event means the activation is live, however old the rest is.
    expect(isExpired([at("2026-02-01T10:00:00Z"), at("2026-08-01T10:00:00Z")], FLOOR)).toBe(
      false
    );
  });

  it("does not, on a plan with no retention window", () => {
    // `plans.retention_floor` returns null for a tier that states no window,
    // and nothing is purged on those — so an old activation there is intact.
    expect(isExpired([at("2026-02-01T10:00:00Z")], null)).toBe(false);
  });

  it("does not, when there are no events at all", () => {
    // That is "nobody came" or "never configured", which the report already
    // tells apart. Claiming expiry here would invent a third answer.
    expect(isExpired([], FLOOR)).toBe(false);
  });
});


describe("scoringChanges", () => {
  it("names the rule that moved, in words a client would read", () => {
    const lines = scoringChanges([
      ev("session.config_updated", {
        changed: [
          { field: "engaged_threshold_seconds", from: 30, to: 60 },
        ],
        by: "u_op",
      }),
    ]);
    expect(lines).toHaveLength(1);
    expect(lines[0]).toContain("the engagement threshold");
    expect(lines[0]).toContain("30");
    expect(lines[0]).toContain("60");
  });

  it("shows a rule it cannot name rather than dropping it", () => {
    // A field added to the backend's `SCORING_PARAMETERS` and not to the map
    // here is still a rule that moved. Silence would be the worse failure —
    // the whole point of this line is that the change is not silent.
    const lines = scoringChanges([
      ev("session.config_updated", {
        changed: [{ field: "some_new_rule", from: 1, to: 2 }],
      }),
    ]);
    expect(lines[0]).toContain("some_new_rule");
  });

  it("says nothing about an activation whose rules never moved", () => {
    expect(scoringChanges([dwell("P-1", "z_entry", 90)])).toEqual([]);
  });
});
