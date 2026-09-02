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

import type { RealmEvent, RealmEventType, ZoneNode } from "@/lib/contracts";
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

/**
 * One plausible payload per event type, for the derived fetch-list test above.
 *
 * A `Record` over the whole union on purpose: adding a member to
 * `RealmEventType` breaks this file until somebody writes a sample for it, and
 * the test then decides on its own whether the benchmark has to fetch it.
 * The payloads only have to be readable by `computeScorecard` — every field it
 * ignores is noise, and every field it reads has to be here or the type looks
 * inert when it is not.
 */
const ZONES: ZoneNode[] = [
  { id: "z_entry", name: "Entry", kind: "entry", weight: 1 },
];

const SAMPLE_PAYLOADS: Record<RealmEventType, Record<string, unknown>> = {
  "perception.detection": { anonId: "P1", bbox: [0, 0, 1, 1] },
  "spatial.zone_enter": { anonId: "P1", zoneId: "z_entry" },
  "spatial.zone_exit": { anonId: "P1", zoneId: "z_entry" },
  "spatial.dwell": { anonId: "P1", zoneId: "z_entry", durationSec: 90 },
  "spatial.gaze": { anonId: "P1", targetId: "z_entry", durationSec: 3 },
  "spatial.group": { groupId: "g1", memberAnonIds: ["P1", "P2"], size: 2 },
  "spatial.passby": { anonId: "P1", adjacentZoneId: "z_entry" },
  // Names no visitor, so the scorecard reads nothing from it and the test below
  // decides it is not fetched. Peak zone occupancy is computed from the enters
  // and exits above, which is what makes this event a signal for the floor
  // rather than a figure on the report.
  "spatial.occupancy": {
    zoneId: "z_entry",
    occupancy: 4,
    capacity: 3,
    status: "over",
  },
  "spatial.tagged": { anonId: "P1", tagId: "t1", confidence: 0.9 },
  "surface.touched": { surfaceId: "sf_1", kind: "tap" },
  "surface.interaction": { anonId: "P1", surfaceId: "sf_1", kind: "tap" },
  "rfid.read": { readerId: "r1", tagId: "t1" },
  "consent.captured": { consentId: "c1", anonId: "P1", tier: "T2" },
  "consent.withdrawn": { consentId: "c1" },
  "identity.resolved": { anonId: "P1", contactId: "ct1" },
  "rule.fired": { ruleId: "r1", action: "slack" },
  "rule.staff_prompt": { message: "go", zoneId: "z_entry" },
  "rule.screen_swap": { screenId: "s1", contentId: "c1" },
  "insight.generated": { text: "quiet", refs: [] },
  "intent.scored": { anonId: "P1", score: 50, band: "warm" },
  "handoff.lead": { dedupeKey: "k", stage: "final" },
  "outcome.recorded": { dedupeKey: "k", stage: "won", value: 1000 },
  "crm.retract": { contactId: "ct1", destination: "all" },
  "erasure.requested": { requestedBy: "u1" },
  "erasure.completed": { contactIds: ["ct1"] },
  "retention.purge_requested": { requestedBy: "u1" },
  "retention.purged": { purgedThroughSeq: 10 },
  "followup.drafted": { contact: { id: "ct1" }, subject: "hi", body: "there" },
  "cost.metered": { kind: "llm_tokens", amount: 10, unit: "tokens" },
  "drift.detected": { cameraId: "cam-1", metric: "confidence_mean" },
  "calibration.updated": { cameraId: "cam-1", kind: "privacy_mask" },
  "session.started": { sessionId: "s_1" },
  "session.ended": { sessionId: "s_1" },
  "session.zones_updated": { sessionId: "s_1" },
};

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

  it("drops NaN, which one bad prior activation is enough to produce", () => {
    // An earlier activation whose dwell payloads the scorecard could not read
    // averages to NaN, and one of those poisons the whole row: a client's
    // report reading "Average dwell 58s · NaNs · NaN%" beside two real
    // activations. An uncomputable prior is an absence, not a measurement.
    expect(median([4, NaN, 6])).toBe(5);
    expect(median([NaN])).toBeNull();
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
    // A type added to computeScorecard's switch and forgotten here makes
    // previous activations score lower than the current one — a comparison
    // wrong in a flattering direction, which is the worst kind on a document a
    // client reads.
    //
    // **Derived, not listed.** The first version of this test asserted a
    // hand-written array against another hand-written array, so it could only
    // fail when somebody edited the list it was guarding — the exact opposite
    // of the mistake it is named for. It sat green while `surface.touched` went
    // into the switch and not into the fetch.
    //
    // This version asks the scorecard. One event of each type goes through it
    // alone; any type that moves the result away from the empty-log scorecard
    // is a type the benchmark has to fetch, and the compiler makes sure no type
    // is missing from the sample set — `SAMPLE_PAYLOADS` is a `Record` over the
    // whole union, so a new event type fails to typecheck until it has one.
    const emptyLog = JSON.stringify(computeScorecard([], { zones: ZONES }));

    for (const [type, payload] of Object.entries(SAMPLE_PAYLOADS)) {
      const alone = computeScorecard([ev(type as RealmEventType, payload)], {
        zones: ZONES,
      });
      const reads = JSON.stringify(alone) !== emptyLog;
      if (reads) {
        expect(
          SCORECARD_EVENT_TYPES as readonly string[],
          `${type} changes the scorecard and is not fetched for previous ` +
            `activations, so their figures will be lower than this one's`
        ).toContain(type);
      }
    }

    // A separate decision, about page size rather than correctness: a day's log
    // is almost all detections and the scorecard never reads one.
    expect(SCORECARD_EVENT_TYPES).not.toContain("perception.detection");
  });

  it("compares against a bounded window", () => {
    expect(BENCHMARK_WINDOW).toBe(3);
  });
});
