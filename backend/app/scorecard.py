"""
Session outcome consumer — the P2 ROI scorecard over the durable bus.

Computes the industry-standard four ROI layers (Reach → Engagement →
Affinity → Pipeline) from the append-only event stream for one session,
per docs/roi-framework.md. The formulas mirror
dashboard/src/lib/roi/scorecard.ts exactly (the framework doc is the single
source of truth; the two implementations are kept in lockstep and
cross-checked by scorecard_test.py).

Why the bus and not the graph: the scorecard needs per-visit detail the
relational projection collapses (the graph upserts one DWELLED_IN edge per
person-zone, so multi-visit dwell counts would be lost). Reading the bus
keeps every figure replayable and traceable to event seqs — the audit
requirement from the P2 acceptance criteria. Zone config (weights + kinds)
is supplied by the caller (query params for now; a future session.started
producer will carry it), because the deriver emits only zone ids.

Session hygiene (docs/research CHI '26 — 71% of raw sessions were invalid):
- Dropout discounting: a dwell whose preceding zone_exit was flagged
  `reason="dropout"` (track disappeared mid-visit) is excluded from
  engagement stats and counted in hygiene.dwellsExcludedDropout.
- Duration sanity: dwells outside (0, maxDwellSec] are dropped as sensor
  noise, never silently included.
- Sub-threshold dwells still count in avg dwell (mirroring the client) but
  only cross the "engaged" line at the threshold.
- Session bounds come for free: reads are session-scoped on the bus.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app import bus
from app.models import RealmEvent

MAX_DWELL_SEC = 7200.0  # 2h — anything longer is a sensor hang, not a visit

# Expected dwell (seconds) per zone kind — the v1 presets for
# per-touchpoint-type normalization. Frozen metric definitions live in
# docs/roi-framework.md; callers can override via expectedDwellSecByKind.
EXPECTED_DWELL_SEC_BY_KIND: dict[str, float] = {
    "entry": 10.0,
    "reveal": 90.0,
    "engagement": 60.0,
    "lounge": 120.0,
    "retail": 75.0,
    "sponsor": 30.0,
    "demo": 120.0,
    "press": 30.0,
    "exit": 10.0,
    "privacy_masked": 30.0,
    "other": 30.0,
}


@dataclass
class OutcomeParams:
    activation_cost: float | None = None
    revenue_influenced: float | None = None
    qualified_leads: int | None = None
    engaged_threshold_sec: float = 60.0
    max_dwell_sec: float = MAX_DWELL_SEC
    zone_config: dict[str, dict[str, Any]] = field(default_factory=dict)
    expected_dwell_sec_by_kind: dict[str, float] = field(default_factory=dict)

    def zone_kind(self, zone_id: str) -> str:
        return str(self.zone_config.get(zone_id, {}).get("kind", "other"))

    def zone_weight(self, zone_id: str) -> float:
        try:
            return float(self.zone_config.get(zone_id, {}).get("weight", 1.0))
        except (TypeError, ValueError):
            return 1.0

    def expected_dwell_for_kind(self, kind: str) -> float:
        override = self.expected_dwell_sec_by_kind.get(kind)
        if override is not None:
            return max(float(override), 0.01)
        return max(EXPECTED_DWELL_SEC_BY_KIND.get(kind, 30.0), 0.01)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_outcome(
    tenant_id: str,
    session_id: str,
    params: OutcomeParams,
) -> dict[str, Any]:
    """Compute the full 4-layer outcome for a session from the durable bus."""
    events = bus.read(tenant_id, session_id, after_seq=0, limit=5000)
    result = _outcome_from_events(events, params)
    result["tenantId"] = tenant_id
    result["sessionId"] = session_id
    return result


def _outcome_from_events(events: list[RealmEvent], params: OutcomeParams) -> dict[str, Any]:
    threshold = params.engaged_threshold_sec
    max_dwell = params.max_dwell_sec

    persons: set[str] = set()
    entered: set[str] = set()
    passby_persons: set[str] = set()
    engaged_persons: set[str] = set()
    dwell_by_zone: dict[str, set[str]] = {}
    surface_interactions = 0
    leads_captured = 0

    dwell_sum = 0.0
    dwell_count = 0
    weighted_attention = 0.0
    dwell_by_kind: dict[str, list[float]] = {}

    dwells_excluded_dropout = 0
    dwells_excluded_insane = 0
    dropout_exits = 0

    concurrent = 0
    peak = 0
    peak_at: int | None = None
    longest_dwell: dict[str, Any] | None = None

    # Per-zone report detail (per-visit data survives on the bus, unlike the
    # collapsed graph projection).
    zone_entries: dict[str, int] = {}
    zone_visitors: dict[str, set[str]] = {}
    zone_dwell_sum: dict[str, float] = {}
    zone_dwell_count: dict[str, int] = {}
    first_touch: dict[str, set[str]] = {}  # zone -> anonIds whose FIRST enter
    seen_persons: set[str] = set()

    last_exit_reason: dict[tuple[str, str], str] = {}

    for e in events:
        p = e.payload or {}
        t = e.type
        at = e.occurredAt

        if t == "spatial.zone_enter":
            anon, zone = p.get("anonId"), p.get("zoneId")
            if anon and zone:
                persons.add(anon)
                entered.add(anon)
                zone_entries[zone] = zone_entries.get(zone, 0) + 1
                zone_visitors.setdefault(zone, set()).add(anon)
                if anon not in seen_persons:
                    seen_persons.add(anon)
                    first_touch.setdefault(zone, set()).add(anon)
                concurrent += 1
                if concurrent > peak:
                    peak = concurrent
                    peak_at = at
                elif concurrent == peak and peak_at is None:
                    peak_at = at

        elif t == "spatial.zone_exit":
            anon, zone, reason = p.get("anonId"), p.get("zoneId"), p.get("reason")
            if anon and zone:
                concurrent = max(0, concurrent - 1)
                last_exit_reason[(anon, zone)] = reason or "leave"
                if reason == "dropout":
                    dropout_exits += 1

        elif t == "spatial.dwell":
            anon, zone = p.get("anonId"), p.get("zoneId")
            dur = float(p.get("durationSec", 0) or 0)
            if not (anon and zone):
                continue
            persons.add(anon)
            reason = last_exit_reason.get((anon, zone), "leave")
            if reason == "dropout":
                dwells_excluded_dropout += 1
                continue
            if not (0 < dur <= max_dwell):
                dwells_excluded_insane += 1
                continue
            dwell_sum += dur
            dwell_count += 1
            weighted_attention += dur * params.zone_weight(zone)
            dwell_by_zone.setdefault(zone, set()).add(anon)
            zone_dwell_sum[zone] = zone_dwell_sum.get(zone, 0.0) + dur
            zone_dwell_count[zone] = zone_dwell_count.get(zone, 0) + 1
            kind = params.zone_kind(zone)
            dwell_by_kind.setdefault(kind, []).append(dur)
            if longest_dwell is None or dur > longest_dwell["durationSec"]:
                longest_dwell = {
                    "anonId": anon,
                    "zoneId": zone,
                    "durationSec": round(dur, 1),
                    "at": at,
                }
            if dur >= threshold:
                engaged_persons.add(anon)

        elif t == "spatial.passby":
            anon = p.get("anonId")
            if anon:
                passby_persons.add(anon)

        elif t == "surface.interaction":
            anon = p.get("anonId")
            if anon:
                persons.add(anon)
                engaged_persons.add(anon)
            surface_interactions += 1

        elif t in ("consent.captured", "identity.resolved"):
            leads_captured += 1

    unique = len(persons) or len(entered)
    engaged = len(engaged_persons)

    zone_participation = [
        {
            "zoneId": zid,
            "visitors": len(v),
            "pct": round(100 * len(v) / unique, 1) if unique else 0.0,
        }
        for zid, v in sorted(dwell_by_zone.items())
    ]

    holding_time_by_kind: list[dict[str, Any]] = []
    normalized_values: list[float] = []
    for kind, durs in sorted(dwell_by_kind.items()):
        avg = sum(durs) / len(durs)
        expected = params.expected_dwell_for_kind(kind)
        normalized = round(avg / expected, 2)
        normalized_values.append(normalized)
        holding_time_by_kind.append(
            {
                "kind": kind,
                "dwells": len(durs),
                "avgDwellSec": round(avg, 1),
                "expectedSec": expected,
                "normalized": normalized,
            }
        )

    holding_time_index = (
        round(sum(normalized_values) / len(normalized_values), 2)
        if normalized_values
        else None
    )

    # Zone-by-zone report detail, ordered by visitors (report's "zone by zone").
    zone_breakdown = []
    for zid in sorted(zone_visitors, key=lambda z: len(zone_visitors[z]), reverse=True):
        z_sum = zone_dwell_sum.get(zid, 0.0)
        z_count = zone_dwell_count.get(zid, 0)
        zone_breakdown.append(
            {
                "zoneId": zid,
                "kind": params.zone_kind(zid),
                "entries": zone_entries.get(zid, 0),
                "visitors": len(zone_visitors[zid]),
                "avgDwellSec": round(z_sum / z_count, 1) if z_count else 0.0,
                "totalDwellSec": round(z_sum, 1),
            }
        )

    # Honest funnel: share of unique visitors whose FIRST enter was each zone.
    funnel = []
    for zid, anons in sorted(first_touch.items(), key=lambda kv: len(kv[1]), reverse=True):
        funnel.append(
            {
                "zoneId": zid,
                "kind": params.zone_kind(zid),
                "firstTouchVisitors": len(anons),
                "shareOfVisitors": round(100 * len(anons) / unique, 1) if unique else 0.0,
            }
        )

    pipeline = {
        "leadsCaptured": leads_captured,
        "firstPartyCaptureRate": round(leads_captured / engaged, 3) if engaged else 0.0,
        "costPerEngagedVisit": None,
        "costPerQualifiedLead": None,
        "pipelineMultiple": None,
        "roiRatio": None,
    }

    cost = params.activation_cost
    rev = params.revenue_influenced
    q_leads = params.qualified_leads if params.qualified_leads is not None else leads_captured

    if cost and engaged:
        pipeline["costPerEngagedVisit"] = round(cost / engaged, 2)
    if cost and q_leads:
        pipeline["costPerQualifiedLead"] = round(cost / q_leads, 2)
    if cost and rev is not None:
        pipeline["pipelineMultiple"] = round(rev / cost, 2)
        pipeline["roiRatio"] = round((rev - cost) / cost, 2)

    benchmark_verdict: str = "unknown"
    roi = pipeline["roiRatio"]
    if roi is not None:
        if roi >= 5:
            benchmark_verdict = "exceptional"
        elif roi >= 3:
            benchmark_verdict = "strong"
        else:
            benchmark_verdict = "below"

    return {
        "tenantId": "",
        "sessionId": "",
        "computedAt": _now_iso(),
        "reach": {
            "uniqueVisitors": unique,
            "entries": len(entered),
            "passBy": len(passby_persons),
            "peakConcurrency": peak,
        },
        "engagement": {
            "avgDwellSec": round(dwell_sum / dwell_count, 1) if dwell_count else 0.0,
            "dwellWeightedAttention": round(weighted_attention, 1),
            "engagementRate": round(engaged / unique, 3) if unique else 0.0,
            "zoneParticipation": zone_participation,
            "surfaceInteractions": surface_interactions,
            "holdingTimeByKind": holding_time_by_kind,
            "holdingTimeIndex": holding_time_index,
        },
        "affinity": {"sentiment": None, "npsLift": None, "recallPct": None},
        "pipeline": pipeline,
        "benchmarkVerdict": benchmark_verdict,
        "hygiene": {
            "dwellsIncluded": dwell_count,
            "dwellsExcludedDropout": dwells_excluded_dropout,
            "dwellsExcludedInsane": dwells_excluded_insane,
            "dropoutExits": dropout_exits,
            "maxDwellSecApplied": max_dwell,
            "engagedThresholdSec": threshold,
        },
        "zones": zone_breakdown,
        "funnel": funnel,
        "peakConcurrencyAt": peak_at,
        "longestDwell": longest_dwell,
        "source": {
            "kind": "bus",
            "eventsRead": len(events),
            "note": "computed over the durable event stream; zone config via query params",
        },
    }
