# realmspace — ROI Proving Framework

> "Prove ROI" is the #1 client job (see `VISION.md`). The 24-hour report stays,
> but a single PDF is not "proving ROI" — it's presenting numbers. This document
> defines a **proving toolkit** grounded in current industry standards, so
> realmspace makes claims a CFO will accept, not vanity metrics.

---

## 1. The industry-standard model we adopt

Experiential/event measurement best practice (2025–2026) converges on capturing
value across **four layers**, each tied to a real outcome via first-party data:

| Layer | Question it answers | realmspace signal source |
|---|---|---|
| **1. Reach** | How many people, how much exposure? | Footfall, unique anonymous visitors, impressions, pass-by count |
| **2. Engagement** | Did they actually interact? | Dwell time, zone participation, repeat visits, surface interactions |
| **3. Affinity** | Did it change how they feel? | Sentiment, NPS lift, recall (survey/QR capture) |
| **4. Pipeline** | Did it drive business? | First-party capture rate, cost per qualified lead, pipeline multiple, influenced revenue, LTV |

**Benchmark:** a **3:1 to 5:1** return is considered strong; higher is
exceptional. **But the most useful benchmark is the client's own history** —
cost per lead, pipeline multiple, and conversion rate vs. their other channels.
realmspace's cross-activation dataset (multi-tenant) becomes the comparative
benchmark engine over time.

> Source basis: Snapbar 4-layer framework; Bizzabo / ZoomInfo pipeline-attribution
> guidance; GreenBook experiential measurement pillars. See `competitive-landscape.md`
> for how proving ROI is *also* a differentiator, not just a feature.

---

## 2. The realmspace metric catalogue

Each metric below maps to a layer, states its formula, and notes whether it needs
**anonymous** data only or **consent-gated PII**.

### Layer 1 — Reach (anonymous)
| Metric | Formula / definition |
|---|---|
| Unique visitors | `count(distinct anon person)` per session |
| Footfall / entries | `count(ENTERED entry-zone)` |
| Pass-by (negative signal) | people detected adjacent who never entered — captures "skip" signal |
| Peak concurrency | max simultaneous people in space |
| Impressions (physical) | dwell-weighted exposure to branded surfaces |

### Layer 2 — Engagement (anonymous)
| Metric | Formula / definition |
|---|---|
| Avg dwell time | `avg(DWELLED_IN.duration)` overall and per zone |
| **Dwell-weighted attention** | `Σ(dwell × zone_weight)` — our signature metric; not all dwell is equal |
| Engagement rate | `engaged_visitors / total_visitors` (engaged = dwell > threshold or surface interaction) |
| Zone participation | % of visitors reaching each zone (the funnel) |
| Repeat / return rate | visitors with ≥2 separate visit sessions |
| Surface interactions | `count(INTERACTED_WITH surface)` per surface |

### Layer 3 — Affinity (survey / opt-in)
| Metric | Formula / definition |
|---|---|
| Sentiment | tagged from opt-in survey or staff-logged interaction quality |
| NPS lift | post-activation NPS vs. control/baseline |
| Recall | % who recall brand/message in follow-up survey |

### Layer 4 — Pipeline (consent-gated PII)
| Metric | Formula / definition |
|---|---|
| **First-party capture rate** | `leads_captured / engaged_visitors` |
| **Cost per engaged visit (CPEV)** | `total_activation_cost / engaged_visitors` |
| **Cost per qualified lead (CPQL)** | `total_activation_cost / qualified_leads` |
| **Pipeline multiple** | `pipeline_value_influenced / activation_cost` |
| Influenced revenue | closed-won revenue where a booth touch exists in the path (attribution window) |
| **ROI ratio** | `(revenue_influenced − cost) / cost` → compare to 3:1–5:1 benchmark |
| Attribution decay | configurable 30/60/90-day windows on the outcome edge |
| Sponsor exposure value | dwell-weighted branded-surface exposure × media-equivalent rate |

---

## 3. Attribution models (how a footstep gets credit)

B2B attribution is notoriously fragile. realmspace supports multiple models so
clients can use the one their finance team accepts:

- **First-touch** — booth gets credit if it's the first recorded touch.
- **Last-touch** — booth gets credit if it's the last touch before conversion.
- **Multi-touch (linear / time-decay)** — booth gets a *share* of influenced
  revenue across the buyer journey. Most defensible for long B2B cycles.
- **Influenced (binary)** — deal is "booth-influenced" if any booth touch exists
  in the attribution window. Simplest to defend; the industry's rising standard.

**Design principle:** realmspace reports the raw, honest signal (this person
dwelled here, converted here, at this time) and lets the client pick the model.
We never inflate. Over-claiming kills the trust that is our moat.

---

## 4. The proving toolkit (what we ship, beyond the PDF)

1. **The 24h Report** *(exists — keep & upgrade)* — hero numbers, the 4-layer
   scorecard, funnel, top moments, zone breakdown, sponsor exposure,
   recommendations. Now templated from real session data.
2. **The live ROI scorecard** — the four layers as a live dashboard tile during
   the activation, so operators optimise on day 2.
3. **Benchmark comparison** — this activation vs. the client's own history
   ✅ *(2026-08-24)*, vs. the anonymised realmspace network median 🔲.

   The first half is on the report: the median of their last three activations,
   on unique visitors, engagement rate, average dwell and ROI ratio, each row
   carrying how many previous activations it rests on. Every figure comes from
   the same `computeScorecard` the report itself uses, run over each earlier
   session's own log against the parameters that session was actually run with.

   The second half is named on the card as absent. It needs anonymised
   aggregates across tenants (`multi-tenant.md` §6) and nothing in the system can
   read another tenant's data — row-level security fails closed on the attempt,
   and there is no aggregator. A figure we cannot produce gets a stated absence.
4. **The attribution ledger** — an exportable, auditable list of every
   booth-touch → outcome link with timestamps and consent basis. This is what a
   CFO/auditor asks for; nobody else in the category ships it.
5. **CFO one-pager** — a single auto-generated page: cost in, ROI ratio out, vs.
   benchmark, with the attribution model stated plainly.
6. **What-if / uplift** — "if you'd converted pass-by visitors at X%, pipeline
   would be Y" — turns negative signals into a next-activation argument.

---

## 5. "Design measurement in before it runs"

The strongest ROI comes from planning it into the activation, not bolting it on:

- Define the **funnel** (entry → experience → product → capture) at session setup
  — the prefab/zone editor already supports this.
- Set the **engagement threshold** and **zone weights** per activation.
- Decide the **capture mechanism** (badge/QR/kiosk/form) and **consent copy**
  up front.
- Set the **attribution model + window** with the client before doors open, so
  the ROI number is pre-agreed and un-arguable afterwards.

---

## 6. Roadmap hooks

- **Report upgrade → 4-layer scorecard**: ROI-proving phase.
- **Live ROI tile**: after the report is real.
- **Attribution ledger + CFO one-pager**: depends on identity + CRM
  (`integrations.md`, `consent-and-identity.md`).
- **Own-history benchmark**: ✅ built. Needs only the session listing, because
  the comparison is the same scorecard run over each earlier session's log.
- **Network benchmark**: still depends on multi-tenant cloud sync
  (`multi-tenant.md`) — an aggregator that can see across tenants, which is
  exactly what nothing here is allowed to be.

See `roadmap.md` for exact sequencing and acceptance criteria.
