"use client";

/**
 * realmspace — this activation against the client's own history.
 *
 * `roi-framework.md` §2 says which comparison is worth the most, and it is not
 * the industry band: *"the most useful benchmark is the client's own history"*.
 * Until now the report offered only the 3–5:1 band on the ROI pill, so a client
 * reading their second activation could not see whether it beat their first.
 *
 * ## The same scorecard, not a second one
 *
 * Every figure here comes from `computeScorecard` — the same function, over
 * each earlier session's own event log. The backend deliberately computes no
 * ROI metric (`routers/sessions.py`, `GET /v1/sessions/{id}/graph`): re-deriving
 * the four layers in Python would give the product two definitions of
 * "engagement rate" and nothing to notice when they stopped agreeing. So the
 * backend supplies a *listing* of activations, and the comparison is arithmetic
 * over scorecards computed here.
 *
 * That is why `benchmarkFigures` is exported and tested against the report's own
 * scorecard: if these two ever disagree about a session, the bug is loud.
 *
 * ## What is deliberately not here
 *
 * The **realmspace network median** — this activation against every other
 * client's. `multi-tenant.md` §6 describes it as the network effect and is
 * equally clear about its condition: *"never expose one tenant's raw data to
 * another — only modelled, anonymised benchmarks"*. Nothing in this repo can
 * read across tenants: row-level security fails closed on the attempt and there
 * is no cloud aggregator to compute the aggregate. So the card names it as
 * absent rather than showing a number nobody produced.
 */

import { useEffect, useState } from "react";

import {
  busEmail,
  ensureTenantId,
  fetchSessionEvents,
  isRemoteBusEnabled,
} from "@/lib/bus";
import {
  fetchSessionList,
  type RemoteSessionSummary,
} from "@/lib/session/publish";
import { computeScorecard, type Scorecard } from "@/lib/roi/scorecard";
import type { RealmEvent } from "@/lib/contracts";
import type { Session } from "@/lib/session/types";

/**
 * The event types a scorecard actually reads.
 *
 * `perception.detection` is absent and that is the point: it is almost every
 * row in a day's log and the scorecard never looks at one. Filtering server-side
 * is the difference between a benchmark that loads and one that pages through
 * hundreds of thousands of rows to compute nothing.
 *
 * Keep in step with the `switch` in `computeScorecard`. A type added there and
 * forgotten here makes prior activations quietly score lower than the current
 * one — a comparison that is wrong in a flattering direction, which is the
 * worst kind for a document a client reads.
 */
export const SCORECARD_EVENT_TYPES = [
  "spatial.zone_enter",
  "spatial.zone_exit",
  "spatial.dwell",
  "spatial.passby",
  "surface.interaction",
  "consent.captured",
  "identity.resolved",
] as const;

/** How many previous activations a benchmark is built from, at most. */
export const BENCHMARK_WINDOW = 3;

/** The four figures compared. Chosen because each is measured, not supplied. */
export interface BenchmarkFigures {
  uniqueVisitors: number | null;
  engagementRate: number | null;
  avgDwellSec: number | null;
  /** Null unless the operator entered both a cost and an influenced revenue. */
  roiRatio: number | null;
}

export interface BenchmarkRow {
  key: keyof BenchmarkFigures;
  label: string;
  /** True when a bigger number is a better activation. All four are, today. */
  higherIsBetter: boolean;
  current: number | null;
  median: number | null;
  /** How many previous activations had this figure at all. */
  n: number;
}

export interface Benchmark {
  status: "loading" | "ready" | "none";
  rows: BenchmarkRow[];
  /** The activations the median was taken over, newest first. */
  comparedWith: RemoteSessionSummary[];
  /** Why a figure or the whole card is absent. Rendered, never swallowed. */
  missing: string[];
}

/**
 * A scorecard reduced to the four comparable figures.
 *
 * Exported so a test can assert these are the report's own numbers rather than
 * a parallel calculation that happens to look similar.
 */
export function benchmarkFigures(scorecard: Scorecard): BenchmarkFigures {
  return {
    uniqueVisitors: scorecard.reach.uniqueVisitors,
    engagementRate: scorecard.engagement.engagementRate,
    avgDwellSec: scorecard.engagement.avgDwellSec,
    roiRatio: scorecard.pipeline.roiRatio,
  };
}

/**
 * Median, not mean.
 *
 * Three activations where one was rained off should not drag the baseline the
 * client is measured against — a mean lets a single bad day make an ordinary
 * one look like a triumph, which is the direction `roi-framework.md` §3 says we
 * must never be wrong in.
 *
 * Nulls are dropped rather than counted as zero. An activation whose cost was
 * never entered has no ROI ratio; treating that as 0 would invent a failure it
 * never had and make everything after it look better by comparison.
 *
 * **`NaN` is dropped for the same reason, and it was reaching the page.** A
 * prior activation whose dwell payloads the scorecard could not read averages
 * to `NaN`, and one such value poisoned the whole median — a client's report
 * showing "Average dwell 58s · NaNs · NaN%" beside two real activations. Found
 * by the Phase 6 acceptance run. An uncomputable prior is an *absence*, which
 * this row already knows how to say; `n` counts what actually contributed, so
 * the reader can see the median thinned.
 */
export function median(values: (number | null | undefined)[]): number | null {
  const present = values
    .filter((v): v is number => v != null && Number.isFinite(v))
    .sort((a, b) => a - b);
  if (!present.length) return null;
  const mid = Math.floor(present.length / 2);
  const value =
    present.length % 2 ? present[mid] : (present[mid - 1] + present[mid]) / 2;
  return +value.toFixed(3);
}

const ROW_LABELS: { key: keyof BenchmarkFigures; label: string }[] = [
  { key: "uniqueVisitors", label: "Unique visitors" },
  { key: "engagementRate", label: "Engagement rate" },
  { key: "avgDwellSec", label: "Average dwell" },
  { key: "roiRatio", label: "ROI ratio" },
];

/**
 * Build the comparison from a current scorecard and the previous ones.
 *
 * Pure, and separated from the fetching for that reason: the arithmetic that
 * decides what a client is told about their own history is worth testing
 * without a network in the way.
 */
export function buildBenchmark(
  current: Scorecard,
  previous: { summary: RemoteSessionSummary; scorecard: Scorecard }[]
): Omit<Benchmark, "status"> {
  const currentFigures = benchmarkFigures(current);
  const previousFigures = previous.map((p) => benchmarkFigures(p.scorecard));

  const rows: BenchmarkRow[] = ROW_LABELS.map(({ key, label }) => {
    const values = previousFigures.map((f) => f[key]);
    return {
      key,
      label,
      higherIsBetter: true,
      current: currentFigures[key],
      median: median(values),
      // Counted per figure, not per activation. A previous session with no
      // cost contributes to the visitor median and not to the ROI one, and a
      // reader has to be able to see that the two rows rest on different
      // amounts of evidence.
      n: values.filter((v) => v != null && Number.isFinite(v)).length,
    };
  });

  const missing: string[] = [];
  if (!previous.length) {
    missing.push(
      "This is the first activation on record for this client, so there is no history to compare it against yet."
    );
  }
  if (previous.length && rows.every((r) => r.n === 0)) {
    missing.push(
      "Previous activations exist but none of them measured anything comparable."
    );
  }
  const roi = rows.find((r) => r.key === "roiRatio");
  if (previous.length && roi && roi.n < previous.length) {
    const without = previous.length - roi.n;
    missing.push(
      `${without} previous ${without === 1 ? "activation has" : "activations have"} no ROI ratio, because ${without === 1 ? "its" : "their"} cost or influenced revenue was never entered. ${without === 1 ? "It is" : "They are"} left out of that row only.`
    );
  }
  missing.push(
    "The realmspace network median is not shown. It would need anonymised aggregates across tenants, and nothing here can read another tenant's data — see multi-tenant.md."
  );

  return { rows, comparedWith: previous.map((p) => p.summary), missing };
}

const EMPTY: Omit<Benchmark, "status"> = {
  rows: [],
  comparedWith: [],
  missing: [],
};

export function useBenchmark(session: Session, current: Scorecard): Benchmark {
  const [state, setState] = useState<Benchmark>(() => ({
    status: isRemoteBusEnabled() && !session.isDemo ? "loading" : "none",
    ...EMPTY,
  }));

  useEffect(() => {
    let cancelled = false;

    async function load() {
      // The demo session's events are invented. Benchmarking a real activation
      // against them — or them against real ones — would put a fabricated
      // figure on the deliverable, which is the whole thing `roi-framework.md`
      // §3 forbids.
      if (!isRemoteBusEnabled() || session.isDemo) {
        setState({
          status: "none",
          ...EMPTY,
          missing: session.isDemo
            ? ["The demo session is not benchmarked: its events are synthetic, and comparing a real activation against invented ones would put a fabricated figure on the report."]
            : [],
        });
        return;
      }

      const email = busEmail();
      // Same reason as `useSessionReport`: read synchronously, this is the
      // default tenant rather than this browser's, and every prior activation's
      // events would be dropped on the way in — a client with a history would
      // be told they had none.
      const tenantId = await ensureTenantId(email);
      if (cancelled) return;

      const listing = await fetchSessionList(email);
      if (cancelled) return;

      const earlier = (listing ?? [])
        .filter((s) => s.sessionId !== session.id)
        .slice(0, BENCHMARK_WINDOW);

      const previous: { summary: RemoteSessionSummary; scorecard: Scorecard }[] = [];
      for (const summary of earlier) {
        const events = (await fetchSessionEvents(tenantId, summary.sessionId, {
          types: [...SCORECARD_EVENT_TYPES],
        })) as RealmEvent[];
        if (cancelled) return;

        previous.push({
          summary,
          scorecard: computeScorecard(events, {
            // Each activation is scored against the parameters it was actually
            // run with. Its threshold, its cost, its own client-supplied
            // revenue — anything else compares two different measurements and
            // calls the difference a result.
            engagedThresholdSec: summary.engagedThresholdSeconds,
            activationCost: summary.activationCost ?? undefined,
            revenueInfluenced: summary.revenueInfluenced ?? undefined,
            qualifiedLeads: summary.qualifiedLeads ?? undefined,
            // No zones, deliberately, and it costs nothing here: not one of
            // the four compared figures reads them. The two scorecard figures
            // that do — entry crossings, which need to know which zone is the
            // door, and dwell-weighted attention, which needs each zone's
            // weight — are absent from the comparison for exactly that reason.
            // Weights are set per activation ("not all dwell is equal",
            // roi-framework.md §2), so comparing them across two activations
            // would report a difference in the agreement rather than on the
            // floor. Fetching each prior session's config to compute figures
            // nobody compares would be three more round trips for nothing.
          }),
        });
      }

      if (cancelled) return;
      setState({ status: "ready", ...buildBenchmark(current, previous) });
    }

    void load();
    return () => {
      cancelled = true;
    };
    // Keyed on the current session's four figures rather than on the scorecard
    // object: `useSessionReport` builds a fresh one on every load, and an
    // object identity in this list would refetch three activations' logs on
    // every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.id, session.isDemo, JSON.stringify(benchmarkFigures(current))]);

  return state;
}
