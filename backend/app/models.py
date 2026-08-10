"""Pydantic models matching dashboard/src/lib/contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

RealmEventType = Literal[
    "perception.detection",
    "spatial.zone_enter",
    "spatial.zone_exit",
    "spatial.dwell",
    "spatial.gaze",
    "spatial.group",
    "spatial.passby",
    "surface.interaction",
    "consent.captured",
    "consent.withdrawn",
    "identity.resolved",
    "rule.fired",
    "insight.generated",
    "handoff.lead",
    "cost.metered",
    "session.started",
    "session.ended",
]

PII_EVENT_TYPES = frozenset(
    {
        "consent.captured",
        "consent.withdrawn",
        "identity.resolved",
        "handoff.lead",
    }
)


class RealmEventInput(BaseModel):
    tenantId: str
    sessionId: str
    type: RealmEventType
    payload: dict[str, Any] = Field(default_factory=dict)
    eventId: str | None = None
    occurredAt: int | None = None  # ms epoch
    consentProof: dict[str, Any] | None = None


class RealmEvent(BaseModel):
    seq: int
    eventId: str
    tenantId: str
    sessionId: str
    type: RealmEventType
    payload: dict[str, Any]
    occurredAt: int
    recordedAt: int


class EventBatch(BaseModel):
    events: list[RealmEventInput]


class AuthResolveResponse(BaseModel):
    userId: str
    email: str
    displayName: str | None
    orgId: str
    role: Literal["admin", "operator", "analyst", "viewer"]


class GraphSnapshot(BaseModel):
    tenantId: str
    sessionId: str
    persons: list[dict[str, Any]]
    zones: list[dict[str, Any]]
    surfaces: list[dict[str, Any]]
    contacts: list[dict[str, Any]]
    consents: list[dict[str, Any]]
    edges: list[dict[str, Any]]


class HealthResponse(BaseModel):
    status: str
    version: str
    database: str


class SessionMeta(BaseModel):
    sessionId: str
    eventCount: int
    firstAt: int  # ms epoch
    lastAt: int  # ms epoch


class ReachLayer(BaseModel):
    uniqueVisitors: int
    entries: int
    passBy: int
    peakConcurrency: int


class ZoneParticipation(BaseModel):
    zoneId: str
    visitors: int
    pct: float


class HoldingTimeByKind(BaseModel):
    kind: str
    dwells: int
    avgDwellSec: float
    expectedSec: float
    normalized: float


class EngagementLayer(BaseModel):
    avgDwellSec: float
    dwellWeightedAttention: float
    engagementRate: float
    zoneParticipation: list[ZoneParticipation]
    surfaceInteractions: int
    holdingTimeByKind: list[HoldingTimeByKind]
    holdingTimeIndex: float | None


class AffinityLayer(BaseModel):
    sentiment: float | None
    npsLift: float | None
    recallPct: float | None


class PipelineLayer(BaseModel):
    leadsCaptured: int
    firstPartyCaptureRate: float
    costPerEngagedVisit: float | None
    costPerQualifiedLead: float | None
    pipelineMultiple: float | None
    roiRatio: float | None


class SessionHygiene(BaseModel):
    dwellsIncluded: int
    dwellsExcludedDropout: int
    dwellsExcludedInsane: int
    dropoutExits: int
    maxDwellSecApplied: float
    engagedThresholdSec: float


class ZoneOutcome(BaseModel):
    zoneId: str
    kind: str
    entries: int
    visitors: int
    avgDwellSec: float
    totalDwellSec: float


class FunnelStepOutcome(BaseModel):
    zoneId: str
    kind: str
    firstTouchVisitors: int
    shareOfVisitors: float


class LongestDwell(BaseModel):
    anonId: str
    zoneId: str
    durationSec: float
    at: int


class OutcomeSource(BaseModel):
    kind: str
    eventsRead: int
    note: str


class SessionOutcome(BaseModel):
    tenantId: str
    sessionId: str
    computedAt: str
    reach: ReachLayer
    engagement: EngagementLayer
    affinity: AffinityLayer
    pipeline: PipelineLayer
    benchmarkVerdict: Literal["below", "strong", "exceptional", "unknown"]
    hygiene: SessionHygiene
    zones: list[ZoneOutcome]
    funnel: list[FunnelStepOutcome]
    peakConcurrencyAt: int | None
    longestDwell: LongestDwell | None
    source: OutcomeSource


class AskRequest(BaseModel):
    question: str
    tenantId: str
    sessionId: str


class AskResponse(BaseModel):
    question: str
    answer: str
    chartType: str | None = None
    template: str | None = None
    fallback: bool = False
    table: list[dict[str, Any]] | None = None
    labels: list[str] | None = None
    values: list[float] | None = None
    value: float | None = None


# ── Rules Engine (Phase 3) ──────────────────────────────────────────────────

class RuleCondition(BaseModel):
    type: Literal["threshold", "any", "none"]
    count: int | None = None
    windowSec: int = 30
    zoneId: str | None = None
    minDwellSec: float | None = None


class RuleAction(BaseModel):
    type: Literal["slack", "webhook", "screen_swap", "staff_prompt", "log"]
    channel: str | None = None
    url: str | None = None
    message: str = ""
    screenId: str | None = None


class RuleDefinition(BaseModel):
    ruleId: str
    tenantId: str
    name: str
    triggerType: str  # e.g. "spatial.zone_enter", "spatial.dwell"
    triggerZoneId: str | None = None
    condition: RuleCondition
    action: RuleAction
    enabled: bool = True
    cooldownSec: int = 60


class RuleCreateRequest(BaseModel):
    tenantId: str
    name: str
    triggerType: str
    triggerZoneId: str | None = None
    condition: RuleCondition
    action: RuleAction
    cooldownSec: int = 60


class RuleUpdateRequest(BaseModel):
    name: str | None = None
    condition: RuleCondition | None = None
    action: RuleAction | None = None
    enabled: bool | None = None
    cooldownSec: int | None = None


class RulesListResponse(BaseModel):
    rules: list[RuleDefinition]


class RuleTestRequest(BaseModel):
    rule: RuleCreateRequest
    tenantId: str
    sessionId: str


class RuleTestResponse(BaseModel):
    wouldFire: bool
    matchingEvents: int
    reason: str
