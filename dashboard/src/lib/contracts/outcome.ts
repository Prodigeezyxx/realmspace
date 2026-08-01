/**
 * realmspace — session outcome contract.
 *
 * Mirrors backend/app/models.py SessionOutcome (the edge API's 4-layer ROI
 * scorecard over the durable bus). The backend is the authority for reports;
 * the client mirrors these shapes so /report can render either source.
 */

export type BenchmarkVerdict = "below" | "strong" | "exceptional" | "unknown";

export interface ReachLayer {
  uniqueVisitors: number;
  entries: number;
  passBy: number;
  peakConcurrency: number;
}

export interface ZoneParticipation {
  zoneId: string;
  visitors: number;
  pct: number;
}

export interface HoldingTimeByKind {
  kind: string;
  dwells: number;
  avgDwellSec: number;
  expectedSec: number;
  normalized: number;
}

export interface EngagementLayer {
  avgDwellSec: number;
  dwellWeightedAttention: number;
  engagementRate: number;
  zoneParticipation: ZoneParticipation[];
  surfaceInteractions: number;
  holdingTimeByKind: HoldingTimeByKind[];
  holdingTimeIndex: number | null;
}

export interface AffinityLayer {
  sentiment: number | null;
  npsLift: number | null;
  recallPct: number | null;
}

export interface PipelineLayer {
  leadsCaptured: number;
  firstPartyCaptureRate: number;
  costPerEngagedVisit: number | null;
  costPerQualifiedLead: number | null;
  pipelineMultiple: number | null;
  roiRatio: number | null;
}

export interface SessionHygiene {
  dwellsIncluded: number;
  dwellsExcludedDropout: number;
  dwellsExcludedInsane: number;
  dropoutExits: number;
  maxDwellSecApplied: number;
  engagedThresholdSec: number;
}

export interface ZoneOutcome {
  zoneId: string;
  kind: string;
  entries: number;
  visitors: number;
  avgDwellSec: number;
  totalDwellSec: number;
}

export interface FunnelStepOutcome {
  zoneId: string;
  kind: string;
  firstTouchVisitors: number;
  shareOfVisitors: number;
}

export interface LongestDwell {
  anonId: string;
  zoneId: string;
  durationSec: number;
  at: number;
}

export interface OutcomeSource {
  kind: string;
  eventsRead: number;
  note: string;
}

export interface SessionOutcome {
  tenantId: string;
  sessionId: string;
  computedAt: string;
  reach: ReachLayer;
  engagement: EngagementLayer;
  affinity: AffinityLayer;
  pipeline: PipelineLayer;
  benchmarkVerdict: BenchmarkVerdict;
  hygiene: SessionHygiene;
  zones: ZoneOutcome[];
  funnel: FunnelStepOutcome[];
  peakConcurrencyAt: number | null;
  longestDwell: LongestDwell | null;
  source: OutcomeSource;
}
