/**
 * The client's-own-history benchmark, as executable claims.
 *
 * The one that matters most is `test the figures are the report's own`: this
 * whole feature is only trustworthy because it runs the same `computeScorecard`
 * the report runs. If somebody later "optimises" it into a server-side
 * aggregate, that test is what says the two definitions have parted.
 *
 * The rest are about absences. A benchmark is a document a client reads about
 * their own money, and every way of having no number here means something
 * different: no history at all, history with nothing measured, or history whose
 * cost was never entered. Rendering those as zeros would report a first
 * activation as a decline.
 */

import { describe, expect, it } from "vitest";

import type { RealmEvent, RealmEventType } from "@/lib/contracts";
import { computeScorecard } from "@/lib/roi/scorecard";
import {
  BENCHMARK_WINDOW,
  benchmarkFigures,
  buildBenchmark,
  median,
  SCORECARD_EVENT_TYPES,
} from "./useBenchmark";
import type { RemoteSessionSummary } from "@/lib/session/publish";

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

/** `people` visitors, of whom `engaged` dwelled past the 60s threshold. */
function log(people: number, engaged: number, dwellSec = 90): RealmEvent[] {
  const out: RealmEvent[] = [];
  for (let i = 0; i < people; i++) {
    const anonId = `P-${i}`;
    out.push(ev("spatial.zone_enter", { anonId, zoneId: "z_a" }));
    out.push(
      ev("spatial.dwell", {
        anonId,
        zoneId: "z_a",
        durationSec: i < engaged ? dwellSec : 5,
      })
    );
  }
  return out;
}

function summary(
  sessionId: string,
  extra: Partial<RemoteSessionSummary> = {}
): RemoteSessionSummary {
  return {
    sessionId,
    client: "Aperture",
    campaign: sessionId,
    venue: "Hall",
    city: null,
    startedAt: null,
    endsAt: null,
    engagedThresholdSeconds: 60,
    activationCost: 10000,
    currency: "USD",
    revenueInfluenced: 40000,
    qualifiedLeads: null,
    ...extra,
  };
}

function scored(
  people: number,
  engaged: number,
  s: RemoteSessionSummary = summary("s")
) {
  return {
    summary: s,
    scorecard: computeScorecard(log(people, engaged), {
      engagedThresholdSec: s.engagedThresholdSeconds,
      activationCost: s.activationCost ?? undefined,
      revenueInfluenced: s.revenueInfluenced ?? undefined,
    }),
  };
}

describe("median", () => {
  it("takes the middle of an odd set", () => {
    expect(median([1, 100, 3])).toBe(3);
  });

  it("averages the middle two of an even set", () => {
    expect(median([1, 2, 3, 10])).toBe(2.5);
  });

  it("drops nulls rather than counting them as zero", () => {
    // The activation whose cost nobody entered has no ROI ratio. Scored as 0 it
    // would invent a failure it never had, and flatter everything beside it.
    expect(median([4, null, 6])).toBe(5);
  });

  it("is null when nothing is present at all", () => {
    expect(median([null, undefined])).toBeNull();
  });
});

describe("buildBenchmark", () => {
  it("compares this activation against the median of the previous ones", () => {
    const current = computeScorecard(log(100, 50), {
      engagedThresholdSec: 60,
      activationCost: 10000,
      revenueInfluenced: 40000,
    });
    const previous = [scored(40, 10), scored(60, 30), scored(80, 20)];

    const { rows } = buildBenchmark(current, previous);
    const visitors = rows.find((r) => r.key === "uniqueVisitors")!;

    expect(visitors.current).toBe(100);
    expect(visitors.median).toBe(60);
    expect(visitors.n).toBe(3);
  });

  it("says so when this is the client's first activation", () => {
    const current = computeScorecard(log(10, 5), { engagedThresholdSec: 60 });
    const { rows, missing } = buildBenchmark(current, []);

    // Not zeros. A first activation has not declined from anything.
    expect(rows.every((r) => r.median === null)).toBe(true);
    expect(rows.every((r) => r.n === 0)).toBe(true);
    expect(missing.some((m) => m.includes("first activation"))).toBe(true);
  });

  it("reports n per figure, not per activation", () => {
    // Two priors, one of which nobody entered a cost for. It counts towards the
    // visitor median and not towards the ROI one, and the card has to be able
    // to say that the two rows rest on different evidence.
    const current = computeScorecard(log(50, 25), {
      engagedThresholdSec: 60,
      activationCost: 10000,
      revenueInfluenced: 40000,
    });
    const previous = [
      scored(40, 20),
      scored(60, 30, summary("s_no_cost", { activationCost: null })),
    ];

    const { rows, missing } = buildBenchmark(current, previous);
    expect(rows.find((r) => r.key === "uniqueVisitors")!.n).toBe(2);
    expect(rows.find((r) => r.key === "roiRatio")!.n).toBe(1);
    expect(missing.some((m) => m.includes("no ROI ratio"))).toBe(true);
  });

  it("names the network median as absent, always", () => {
    // The figure multi-tenant.md §6 describes and nothing here can produce: it
    // needs anonymised aggregates across tenants, and RLS fails closed on the
    // read. A stated absence, not a plausible number.
    const current = computeScorecard(log(10, 5), { engagedThresholdSec: 60 });
    const { missing } = buildBenchmark(current, [scored(10, 5)]);
    expect(missing.some((m) => m.includes("network median"))).toBe(true);
  });
});

describe("the figures are the report's own", () => {
  it("reads them straight off the scorecard the report renders", () => {
    // The load-bearing test. This benchmark is only defensible because it runs
    // the same computeScorecard the report runs; the moment these are computed
    // separately they can disagree about one session and nothing would fail.
    const events = log(80, 20, 120);
    const scorecard = computeScorecard(events, {
      engagedThresholdSec: 60,
      activationCost: 5000,
      revenueInfluenced: 25000,
    });

    expect(benchmarkFigures(scorecard)).toEqual({
      uniqueVisitors: scorecard.reach.uniqueVisitors,
      engagementRate: scorecard.engagement.engagementRate,
      avgDwellSec: scorecard.engagement.avgDwellSec,
      roiRatio: scorecard.pipeline.roiRatio,
    });
    expect(scorecard.reach.uniqueVisitors).toBe(80);
    expect(scorecard.engagement.engagementRate).toBe(0.25);
  });

  it("fetches every event type the scorecard reads, and no detections", () => {
    // A type added to computeScorecard's switch and forgotten here would make
    // previous activations score lower than the current one — a comparison
    // wrong in a flattering direction, which is the worst kind on a document a
    // client reads.
    expect([...SCORECARD_EVENT_TYPES].sort()).toEqual([
      "consent.captured",
      "identity.resolved",
      "spatial.dwell",
      "spatial.passby",
      "spatial.zone_enter",
      "spatial.zone_exit",
      "surface.interaction",
    ]);
    expect(SCORECARD_EVENT_TYPES).not.toContain("perception.detection");
  });

  it("compares against a bounded window", () => {
    expect(BENCHMARK_WINDOW).toBe(3);
  });
});
