"""
Assertion harness for the session outcome consumer (no server required).

Run from backend/:
  python -m app.scorecard_test

Covers the same scenarios the browser deriver was validated against
(34-assertion run on 2026-07-29) plus the consumer-side hygiene rules:
dropout discounting, duration sanity, per-kind normalization, and the
pipeline/benchmark math.
"""

from __future__ import annotations

import uuid

from app import bus, db, scorecard
from app.models import RealmEventInput


def _push(events: list[dict]) -> list[int]:
    seqs = []
    for e in events:
        seqs.append(
            bus.append(
                RealmEventInput(
                    tenantId="t_floats",
                    sessionId=SESSION,
                    type=e["type"],
                    payload=e.get("payload", {}),
                    occurredAt=e.get("occurredAt"),
                )
            ).seq
        )
    return seqs


def _outcome() -> dict:
    return scorecard.compute_outcome(
        "t_floats",
        SESSION,
        scorecard.OutcomeParams(
            zone_config={
                "z_entry": {"kind": "entry", "weight": 0.5},
                "z_game": {"kind": "demo", "weight": 2.0},
                "z_screen": {"kind": "engagement", "weight": 1.0},
            },
        ),
    )


db.init_db()
SESSION = f"s_score_{uuid.uuid4().hex[:8]}"

# -- Scenario: 3 visitors, weighted zones, one dropout, one insane dwell ----
_push(
    [
        {"type": "session.started", "payload": {"name": "Outcome Harness", "venue": "Local"}},
        # P-001: enters z_entry (20s, engaged), moves to z_game (150s), exits clean
        {"type": "spatial.zone_enter", "payload": {"anonId": "P-001", "zoneId": "z_entry"}},
        {"type": "spatial.zone_exit", "payload": {"anonId": "P-001", "zoneId": "z_entry", "reason": "move"}},
        {"type": "spatial.dwell", "payload": {"anonId": "P-001", "zoneId": "z_entry", "durationSec": 20.0}},
        {"type": "spatial.zone_enter", "payload": {"anonId": "P-001", "zoneId": "z_game"}},
        {"type": "spatial.zone_exit", "payload": {"anonId": "P-001", "zoneId": "z_game", "reason": "leave"}},
        {"type": "spatial.dwell", "payload": {"anonId": "P-001", "zoneId": "z_game", "durationSec": 150.0}},
        # P-002: screen dwell 45s (< threshold, not engaged), then surface interaction (engaged)
        {"type": "spatial.zone_enter", "payload": {"anonId": "P-002", "zoneId": "z_screen"}},
        {"type": "spatial.zone_exit", "payload": {"anonId": "P-002", "zoneId": "z_screen", "reason": "leave"}},
        {"type": "spatial.dwell", "payload": {"anonId": "P-002", "zoneId": "z_screen", "durationSec": 45.0}},
        {"type": "surface.interaction", "payload": {"anonId": "P-002", "surfaceId": "s_main", "kind": "screen"}},
        # P-003: dropout mid-visit — dwell must be excluded
        {"type": "spatial.zone_enter", "payload": {"anonId": "P-003", "zoneId": "z_screen"}},
        {"type": "spatial.zone_exit", "payload": {"anonId": "P-003", "zoneId": "z_screen", "reason": "dropout"}},
        {"type": "spatial.dwell", "payload": {"anonId": "P-003", "zoneId": "z_screen", "durationSec": 300.0}},
        # P-003 again, clean visit, plus an insane dwell (sensor hang) and a passby
        {"type": "spatial.zone_enter", "payload": {"anonId": "P-003", "zoneId": "z_game"}},
        {"type": "spatial.zone_exit", "payload": {"anonId": "P-003", "zoneId": "z_game", "reason": "leave"}},
        {"type": "spatial.dwell", "payload": {"anonId": "P-003", "zoneId": "z_game", "durationSec": 60.0}},
        {"type": "spatial.zone_enter", "payload": {"anonId": "P-004", "zoneId": "z_screen"}},
        {"type": "spatial.zone_exit", "payload": {"anonId": "P-004", "zoneId": "z_screen", "reason": "leave"}},
        {"type": "spatial.dwell", "payload": {"anonId": "P-004", "zoneId": "z_screen", "durationSec": 99999.0}},
        {"type": "spatial.passby", "payload": {"anonId": "P-005", "adjacentZoneId": "z_entry", "closestDist": 0.03}},
        # consent → pipeline layer
        {"type": "consent.captured", "payload": {"contactId": "c_1", "anonId": "P-001", "tier": "T2"}},
    ]
)

o = _outcome()

# -- Reach --
assert o["reach"]["uniqueVisitors"] == 4, o["reach"]  # P-001..P-004 dwelled
assert o["reach"]["entries"] == 4, o["reach"]
assert o["reach"]["passBy"] == 1, o["reach"]
assert o["reach"]["peakConcurrency"] == 1, o["reach"]  # no overlapping visits in scenario

# -- Engagement (hygiene applied) --
# Included dwells: 20 + 150 + 45 + 60 = 275 over 4 dwells
assert o["engagement"]["avgDwellSec"] == 68.8, o["engagement"]
assert o["hygiene"]["dwellsIncluded"] == 4, o["hygiene"]
assert o["hygiene"]["dwellsExcludedDropout"] == 1, o["hygiene"]  # P-003 300s dropout
assert o["hygiene"]["dwellsExcludedInsane"] == 1, o["hygiene"]  # P-004 99999s
assert o["hygiene"]["dropoutExits"] == 1, o["hygiene"]
# Engaged: P-001 (150≥60), P-002 (surface interaction), P-003 (60≥60) → 3 of 4
assert o["engagement"]["engagementRate"] == 0.75, o["engagement"]
# Dwell-weighted attention: 20*0.5 + 150*2.0 + 45*1.0 + 60*2.0 = 475
assert o["engagement"]["dwellWeightedAttention"] == 475.0, o["engagement"]

# -- Per-kind normalization (the CHI '26 C3 feature) --
kinds = {k["kind"]: k for k in o["engagement"]["holdingTimeByKind"]}
assert set(kinds) == {"entry", "demo", "engagement"}, kinds
assert kinds["entry"]["avgDwellSec"] == 20.0 and kinds["entry"]["expectedSec"] == 10.0
assert kinds["entry"]["normalized"] == 2.0, kinds["entry"]
assert kinds["demo"]["dwells"] == 2, kinds["demo"]  # P-001 150s + P-003 60s
assert kinds["demo"]["normalized"] == round(105.0 / 120.0, 2) == 0.88, kinds["demo"]
assert o["engagement"]["holdingTimeIndex"] == round((2.0 + 0.88 + 0.75) / 3, 2) == 1.21, o["engagement"]

# -- Pipeline --
assert o["pipeline"]["leadsCaptured"] == 1, o["pipeline"]
assert o["pipeline"]["firstPartyCaptureRate"] == round(1 / 3, 3), o["pipeline"]
assert o["pipeline"]["roiRatio"] is None  # no economics given → honest null

# -- Benchmark verdict --
assert o["benchmarkVerdict"] == "unknown"

# -- Report detail: zone breakdown (per-visit data, sorted by visitors) --
zb = o["zones"]
assert [z["zoneId"] for z in zb] == ["z_screen", "z_game", "z_entry"], zb
z_screen, z_game, z_entry = zb
assert z_screen == {"zoneId": "z_screen", "kind": "engagement", "entries": 3, "visitors": 3, "avgDwellSec": 45.0, "totalDwellSec": 45.0}, z_screen
assert z_game["entries"] == 2 and z_game["visitors"] == 2 and z_game["avgDwellSec"] == 105.0 and z_game["totalDwellSec"] == 210.0, z_game
assert z_entry["entries"] == 1 and z_entry["avgDwellSec"] == 20.0, z_entry

# -- Report detail: first-touch funnel (honest path order) --
fn = o["funnel"]
assert [f["zoneId"] for f in fn] == ["z_screen", "z_entry"], fn  # 3 first-touches vs 1
assert fn[0]["firstTouchVisitors"] == 3 and fn[0]["shareOfVisitors"] == 75.0, fn[0]
assert fn[1]["firstTouchVisitors"] == 1 and fn[1]["shareOfVisitors"] == 25.0, fn[1]

# -- Report detail: peak time + longest dwell (traceable to events) --
assert isinstance(o["peakConcurrencyAt"], int) and o["peakConcurrencyAt"] > 0, o["peakConcurrencyAt"]
ld = o["longestDwell"]
assert ld is not None and ld["anonId"] == "P-001" and ld["zoneId"] == "z_game" and ld["durationSec"] == 150.0, ld
assert ld["at"] > 0, ld

# -- Economics + benchmark verdict via params --
o2 = scorecard.compute_outcome(
    "t_floats",
    SESSION,
    scorecard.OutcomeParams(
        activation_cost=1000.0,
        revenue_influenced=4000.0,
        qualified_leads=2,
    ),
)
assert o2["pipeline"]["costPerEngagedVisit"] == round(1000 / 3, 2), o2["pipeline"]
assert o2["pipeline"]["costPerQualifiedLead"] == 500.0, o2["pipeline"]
assert o2["pipeline"]["pipelineMultiple"] == 4.0, o2["pipeline"]
assert o2["pipeline"]["roiRatio"] == 3.0, o2["pipeline"]
assert o2["benchmarkVerdict"] == "strong", o2["benchmarkVerdict"]

# -- Edge: empty session is a safe, honest zero-scorecard --
o3 = scorecard.compute_outcome("t_floats", f"s_empty_{uuid.uuid4().hex[:8]}", scorecard.OutcomeParams())
assert o3["reach"]["uniqueVisitors"] == 0
assert o3["engagement"]["avgDwellSec"] == 0.0
assert o3["engagement"]["holdingTimeIndex"] is None
assert o3["pipeline"]["roiRatio"] is None
assert o3["benchmarkVerdict"] == "unknown"
assert o3["zones"] == [] and o3["funnel"] == []
assert o3["peakConcurrencyAt"] is None and o3["longestDwell"] is None

print("OK scorecard_test")
print(f"  session={SESSION}")
print(f"  reach={o['reach']}")
print(f"  engagement={o['engagement']['avgDwellSec']}s avg · {o['hygiene']['dwellsExcludedDropout']} dropout excluded")
print(f"  holdingTimeIndex={o['engagement']['holdingTimeIndex']}")
