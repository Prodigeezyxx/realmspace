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
    expect(buildSurfaceRows(events)).toEqual([
      { surfaceId: "mirror", interactions: 2, share: 100 },
      { surfaceId: "quiz", interactions: 1, share: 50 },
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
