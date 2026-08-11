/**
 * realmspace — ROI 4-layer scorecard.
 *
 * Pure functions that compute the industry-standard four layers of experiential
 * ROI (Reach → Engagement → Affinity → Pipeline) from the canonical event
 * stream. See docs/roi-framework.md. No React, no I/O — feed it events, get a
 * scorecard. Anonymous layers need no consent; the Pipeline layer only counts
 * consent-gated data.
 *
 * Benchmark: a 3:1–5:1 return is strong; the best benchmark is the client's own
 * history. This module reports the honest signal and lets the caller apply an
 * attribution model — it never inflates.
 */

import type {
  RealmEvent,
  DwellPayload,
  ZoneMovePayload,
  SurfaceInteractionPayload,
  PassbyPayload,
} from "@/lib/contracts";
import type { ZoneNode } from "@/lib/contracts";

export interface ReachLayer {
  uniqueVisitors: number;
  /**
   * Footfall: `count(ENTERED entry-zone)` — crossings, not people, so someone
   * who leaves and comes back counts twice. That is what footfall means.
   *
   * **null when no zone is marked `entry`**, because then it genuinely cannot
   * be computed. Reporting 0 would say nobody came; null says we were not told
   * where the door is. The report must render those differently.
   */
  entries: number | null;
  passBy: number; // negative signal — detected nearby, never entered
  /**
   * Most people inside a zone at once. Named for what it measures: derived from
   * enter/exit deltas, so someone in the space but not standing in any drawn
   * zone is not counted. It is a floor on true occupancy, never an estimate of
   * it — calling it "peak concurrency" overstated what the number knows.
   */
  peakZoneConcurrency: number;
}
export interface EngagementLayer {
  avgDwellSec: number;
  dwellWeightedAttention: number; // Σ(dwell × zoneWeight)
  /**
   * People past the engagement threshold — the numerator of `engagementRate`
   * and the denominator every per-engaged-visitor cost divides by. Exposed
   * because recovering it from the rate and the unique count reintroduces the
   * rounding the rate already applied.
   */
  engagedVisitors: number;
  engagementRate: number; // engaged / unique
  zoneParticipation: { zoneId: string; visitors: number; pct: number }[];
  surfaceInteractions: number;
}
export interface AffinityLayer {
  // survey/opt-in driven — present when captured, else null
  sentiment: number | null;
  npsLift: number | null;
  recallPct: number | null;
}
export interface PipelineLayer {
  firstPartyCaptureRate: number; // leads / engaged
  leadsCaptured: number;
  costPerEngagedVisit: number | null;
  costPerQualifiedLead: number | null;
  pipelineMultiple: number | null;
  roiRatio: number | null; // (revenue - cost) / cost
}

export interface Scorecard {
  reach: ReachLayer;
  engagement: EngagementLayer;
  affinity: AffinityLayer;
  pipeline: PipelineLayer;
  /** qualitative read vs the 3:1–5:1 industry benchmark */
  benchmarkVerdict: "below" | "strong" | "exceptional" | "unknown";
}

export interface ScorecardInputs {
  zones?: ZoneNode[];
  /** engagement threshold in seconds; dwell above this = "engaged" */
  engagedThresholdSec?: number;
  /** economics for the pipeline layer (optional) */
  activationCost?: number;
  revenueInfluenced?: number;
  qualifiedLeads?: number;
  /** affinity, if surveyed */
  sentiment?: number;
  npsLift?: number;
  recallPct?: number;
}

function zoneWeight(zones: ZoneNode[] | undefined, zoneId: string): number {
  const z = zones?.find((zz) => zz.id === zoneId);
  return z?.weight ?? 1;
}

export function computeScorecard(
  events: RealmEvent[],
  inputs: ScorecardInputs = {}
): Scorecard {
  const threshold = inputs.engagedThresholdSec ?? 60;
  const persons = new Set<string>();
  const entered = new Set<string>();
  const passByPersons = new Set<string>();
  const engagedPersons = new Set<string>();
  const dwellByZone = new Map<string, Set<string>>();
  let dwellSum = 0;
  let dwellCount = 0;
  let weightedAttention = 0;
  let surfaceInteractions = 0;
  let leadsCaptured = 0;
  let entryCrossings = 0;

  // Which zones are the door. roi-framework.md §2 defines footfall against the
  // entry zone specifically, not against any zone — a visitor reaching the
  // product wall is not a second person walking in.
  const entryZoneIds = new Set(
    (inputs.zones ?? []).filter((z) => z.kind === "entry").map((z) => z.id)
  );

  // Zone occupancy via enter/exit deltas. Moving A→B is exit-then-enter, so it
  // nets to zero; entering the first zone is +1 and leaving every zone is −1.
  let concurrent = 0;
  let peak = 0;

  for (const e of events) {
    switch (e.type) {
      case "spatial.zone_enter": {
        const p = e.payload as ZoneMovePayload;
        persons.add(p.anonId);
        entered.add(p.anonId);
        if (entryZoneIds.has(p.zoneId)) entryCrossings++;
        concurrent++;
        peak = Math.max(peak, concurrent);
        break;
      }
      case "spatial.zone_exit": {
        concurrent = Math.max(0, concurrent - 1);
        break;
      }
      case "spatial.passby": {
        const p = e.payload as PassbyPayload;
        passByPersons.add(p.anonId);
        break;
      }
      case "spatial.dwell": {
        const p = e.payload as DwellPayload;
        persons.add(p.anonId);
        dwellSum += p.durationSec;
        dwellCount++;
        weightedAttention += p.durationSec * zoneWeight(inputs.zones, p.zoneId);
        if (!dwellByZone.has(p.zoneId)) dwellByZone.set(p.zoneId, new Set());
        dwellByZone.get(p.zoneId)!.add(p.anonId);
        if (p.durationSec >= threshold) engagedPersons.add(p.anonId);
        break;
      }
      case "surface.interaction": {
        const p = e.payload as SurfaceInteractionPayload;
        persons.add(p.anonId);
        engagedPersons.add(p.anonId);
        surfaceInteractions++;
        break;
      }
      case "consent.captured":
      case "identity.resolved": {
        leadsCaptured++;
        break;
      }
    }
  }

  const unique = persons.size || entered.size;
  const engaged = engagedPersons.size;

  const zoneParticipation = [...dwellByZone.entries()].map(([zoneId, set]) => ({
    zoneId,
    visitors: set.size,
    pct: unique ? +((100 * set.size) / unique).toFixed(1) : 0,
  }));

  const reach: ReachLayer = {
    uniqueVisitors: unique,
    entries: entryZoneIds.size ? entryCrossings : null,
    passBy: passByPersons.size,
    peakZoneConcurrency: peak,
  };

  const engagement: EngagementLayer = {
    avgDwellSec: dwellCount ? +(dwellSum / dwellCount).toFixed(1) : 0,
    dwellWeightedAttention: +weightedAttention.toFixed(1),
    engagedVisitors: engaged,
    engagementRate: unique ? +((engaged / unique)).toFixed(3) : 0,
    zoneParticipation,
    surfaceInteractions,
  };

  const affinity: AffinityLayer = {
    sentiment: inputs.sentiment ?? null,
    npsLift: inputs.npsLift ?? null,
    recallPct: inputs.recallPct ?? null,
  };

  const cost = inputs.activationCost ?? null;
  // Operator-supplied, never measured — realmspace cannot see revenue until
  // Phase 4's CRM attribution. Absent stays absent: every metric below that
  // needs it reports null, so the report says "not known" instead of implying
  // a result nobody has.
  const rev = inputs.revenueInfluenced ?? null;
  const qLeads = inputs.qualifiedLeads ?? leadsCaptured;

  // `!= null` rather than truthiness throughout: a free activation has a cost
  // of 0, and `0 && …` would report its cost-per-visit as unknown instead of
  // as zero.
  const pipeline: PipelineLayer = {
    leadsCaptured,
    firstPartyCaptureRate: engaged ? +((leadsCaptured / engaged)).toFixed(3) : 0,
    costPerEngagedVisit: cost != null && engaged ? +(cost / engaged).toFixed(2) : null,
    costPerQualifiedLead: cost != null && qLeads ? +(cost / qLeads).toFixed(2) : null,
    pipelineMultiple: cost && rev != null ? +(rev / cost).toFixed(2) : null,
    roiRatio: cost && rev != null ? +(((rev - cost) / cost)).toFixed(2) : null,
  };

  let benchmarkVerdict: Scorecard["benchmarkVerdict"] = "unknown";
  if (pipeline.roiRatio != null) {
    if (pipeline.roiRatio >= 5) benchmarkVerdict = "exceptional";
    else if (pipeline.roiRatio >= 3) benchmarkVerdict = "strong";
    else benchmarkVerdict = "below";
  }

  return { reach, engagement, affinity, pipeline, benchmarkVerdict };
}
