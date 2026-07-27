# realmspace — Competitive Landscape & Positioning

> Source: the Deep Dive Research note + market scans. The field is **fragmented
> by category** — no single competitor owns the full
> **Attract → Measure → Attribute** loop. That gap is our position.

---

## 1. The category map

| Category | Players | What they do | Where they stop (our opening) |
|---|---|---|---|
| **Retail/space analytics** | RetailNext, Trax, Placer.ai, Wavetec | Footfall, dwell, store traffic | Not built for activations; measure, don't act, don't attribute to pipeline |
| **Venue / event digital twins** | VenueTwin, Smart Spatial, Event Twins, museum twin integrators | 3D visualisation of spaces | Visualise, don't measure behaviour or prove ROI |
| **Generic computer vision** | AWS Rekognition, Google Vision, VisionPlatform, Incoresoft | CV toolkits/APIs | Toolkits not products; **do face recognition** — the opposite of our privacy moat |
| **Trade-show / event ROI tools** | ZoomInfo (event), Bizzabo, badge-scan lead tools | Lead capture + pipeline attribution | No *spatial* signal — they know a badge scanned, not what the visitor did in the space |
| **Experiential agencies** | Jack Morton, MKG, Sparks, etc. (and Floats) | Design & build activations | Ship the experience, not the measurement layer |

---

## 2. The gap (our thesis in one picture)

```
        MEASURE BEHAVIOUR          ATTRIBUTE TO PIPELINE         PROVE ROI / ACT
Retail analytics     ✅ (footfall)          ❌                        partial
Venue twins          ❌ (visual only)       ❌                        ❌
Generic CV           ✅ (but face-based)    ❌                        ❌
Event ROI tools      ❌ (badge only)        ✅                        partial
Agencies             ❌                     ❌                        ❌ (PDF/anecdote)
──────────────────────────────────────────────────────────────────────────────
realmspace           ✅ anonymous+spatial   ✅ consent-gated PII      ✅ 4-layer + ledger
```

**Nobody connects "what a visitor physically did in the space" to "the attributed
CRM outcome" with a privacy-safe, consent-gated bridge.** That connection is the
product.

---

## 3. Why we win (defensible, not just features)

1. **Full-loop ownership** — spatial behaviour → consent-gated identity →
   attributed CRM outcome → ROI proof. Every competitor owns one column above.
2. **Privacy as architecture** — anonymous-by-default, local-first, no faces.
   Generic-CV incumbents *cannot* claim this; it's their business model.
3. **Vertical stack (Floats)** — we design the booth + experience + measurement,
   so install is free, surfaces emit events for free, and CAC ≈ 0 on embedded.
4. **Integration-neutral** — all top CRMs + BYO means we're the default
   measurement layer regardless of the customer's stack (vs. tools that assume one CRM).
5. **Network benchmark** — multi-tenant anonymised aggregates let us tell a
   client "you're above/below the realmspace network median" — a moat that
   compounds with every activation and that no single-agency tool can build.

---

## 4. Likely objections & answers

| Objection | Answer |
|---|---|
| "AWS Rekognition can do CV." | And it does face recognition, which kills the enterprise/GDPR sale. We're architecturally unable to — that's the point. |
| "We already scan badges for leads." | A badge scan tells you *who arrived*, not *what they did*. We add the spatial intent (dwelled 3m at Product A) that makes the lead qualified. |
| "Placer.ai gives us footfall." | Footfall ≠ activation ROI. We attribute specific behaviour to specific pipeline, with a consent-gated identity bridge. |
| "Isn't this creepy?" | No faces, no cross-session tracking, consent-gated PII, local-first. We monitor space, not people. (See `consent-and-identity.md`.) |

---

## 5. Positioning statement

> For brands, agencies and venue operators running physical activations who can't
> prove what happened or what it was worth, **realmspace** is the spatial
> intelligence layer that measures anonymous behaviour, bridges it — with consent
> — to identified pipeline, and proves ROI in 24 hours. Unlike footfall
> analytics, generic CV, or badge-scan lead tools, realmspace owns the whole loop
> from footstep to attributed revenue, privately by design.

---

## 6. Market sizing (from the research note)

- **TAM:** $2–5B/yr (40k–100k target sites × ~$50k/yr).
- **SAM:** $60–350M (Africa + selected global accounts, 2k–5k sites).
- **SOM:** $1–10M ARR (1–3% of SAM over 5 years).

Customer archetypes: **Event Organiser** (sponsor ROI), **Brand Marketing Team**
(lead gen + CFO ROI), **Venue Operator** (utilisation), **Enterprise Brand Space
Operator** (layout/conversion). See `gtm.md` for the motions that reach them.
