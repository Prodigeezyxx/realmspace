# realmspace — Metric Definitions v1 (frozen)

> **Status: FROZEN 2026-08-01.** These are the v1 formulas the scorecard,
> the session outcome API, the report, and the live ROI tile all compute
> against. Any change is a versioned change (v2 with a migration note), never
> an edit-in-place — a client's report must remain reproducible forever.
>
> Reference implementations (kept in lockstep):
> - `backend/app/scorecard.py` (authoritative — server computes reports)
> - `dashboard/src/lib/roi/scorecard.ts` (offline fallback, same formulas)
> - Cross-checked by `backend/app/scorecard_test.py` (20+ assertions).

Conventions: anonymous metrics need no consent; the Pipeline layer only
counts consent-gated data. All metrics are session-scoped
(`tenant_id`, `session_id`). Honest signal only — never inflate.

---

## 0. Hygiene rules (applied to ALL metrics below)

From `docs/research/2026-07-27-chi26-digital-twins-and-cv-dump.md` — field
studies discarded ~71% of raw sessions. We filter before scoring, and every
exclusion is counted, never silent.

| Rule | Value | Source |
|---|---|---|
| Zone-change confirm window | 600 ms (producer) | `spatial-deriver.ts` `ZONE_CONFIRM_MS` |
| Minimum dwell to count | 1.0 s (producer) | `MIN_DWELL_SEC` |
| Dropout discount | dwell whose preceding `zone_exit.reason == "dropout"` is EXCLUDED from all dwell-based metrics | consumer: `scorecard.py` |
| Maximum dwell sanity | `0 < durationSec ≤ 7200` else dropped as sensor noise | `MAX_DWELL_SEC` |
| Engagement threshold | dwell ≥ 60 s **or** any `surface.interaction` | default; override via `engagedThresholdSec` |
| Session bounds | reads are session-scoped on the bus | inherent |

Exclusions are reported in `SessionOutcome.hygiene`:
`dwellsIncluded`, `dwellsExcludedDropout`, `dwellsExcludedInsane`,
`dropoutExits`.

---

## 1. Layer 1 — Reach (anonymous)

| Metric | Formula | Events |
|---|---|---|
| Unique visitors | count(distinct anonId) across `zone_enter` + `dwell` + `surface.interaction` | all spatial/surface |
| Entries | count(distinct anonId with ≥1 `zone_enter`) | `spatial.zone_enter` |
| Pass-by | count(distinct anonId with `spatial.passby` and no `zone_enter`) | `spatial.passby` |
| Peak concurrency | max over time of (enters − exits), deltas from `zone_enter`/`zone_exit` | enter/exit |
| Peak at | `occurredAt` when peak was first reached | enter/exit |

## 2. Layer 2 — Engagement (anonymous)

| Metric | Formula | Events |
|---|---|---|
| Avg dwell | Σ(dwell.durationSec) / count(dwells) — hygiene-filtered | `spatial.dwell` |
| Dwell-weighted attention | Σ(dwell.durationSec × zone.weight) — **signature metric** | dwell × zone config |
| Engagement rate | engaged / unique visitors | dwell ≥ threshold or interaction |
| Zone participation | per zone: distinct visitors / unique visitors (%) | dwell per zone |
| Surface interactions | count(`surface.interaction`) | surface |
| Holding time (per kind) | per zone kind: avg dwell / expected-for-kind | dwell × zone config |
| Holding time index | mean of per-kind normalized values | above |

Expected-dwell presets (v1, overridable per call via `expectedDwellSecByKind`):

| Kind | Expected (s) |
|---|---|
| entry / exit | 10 |
| reveal | 90 |
| engagement | 60 |
| lounge | 120 |
| retail | 75 |
| sponsor / press | 30 |
| demo | 120 |
| privacy_masked / other | 30 |

## 3. Layer 3 — Affinity (survey / opt-in; null until captured)

Sentiment, NPS lift, recall — all `null` in v1 until a survey producer exists.
Reports show "—", never a fabricated number.

## 4. Layer 4 — Pipeline (consent-gated)

| Metric | Formula | Input |
|---|---|---|
| Leads captured | count(`consent.captured` + `identity.resolved`) | events |
| First-party capture rate | leads / engaged | events |
| Cost per engaged visit (CPEV) | activationCost / engaged | caller |
| Cost per qualified lead (CPQL) | activationCost / qualifiedLeads | caller (defaults to leads) |
| Pipeline multiple | revenueInfluenced / activationCost | caller |
| ROI ratio | (revenue − cost) / cost | caller |
| Benchmark verdict | ≥5 exceptional · 3–5 strong · <3 below · else unknown | roiRatio |

## 5. Zone config (weights + kinds)

The deriver emits only `zoneId` strings, so zone weights/kinds are caller
input today (query params / event context) until a `session.started` producer
carries them. Default weight is 1.0; kind defaults to `other`.

## 6. Traceability

Every figure is computable from the append-only bus for its session
(`GET /v1/events?tenantId&sessionId`), and the outcome response reports
`source.eventsRead`. Full per-figure seq-range citations land with the
report-polish item in Phase 2.
