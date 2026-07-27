# CHI '26 Digital-Twins Paper + CV/Events Link Dump — Research Analysis

> Date: 2026-07-27 · Sources: ~40 (1 peer-reviewed paper, 7 competitor/adjacent vendors, ~15 enabling-tech links, 1 regulatory guidance, ~12 noise/unverifiable) · Analyst: realmspace-analyst skill
> Prior analyses built on: none (first research doc)

## TL;DR

- **The CHI '26 paper is the strongest independent validation realmspace has.** A peer-reviewed, 2,000-visitor field study proves twin + tracking + automated metrics works at real scale — and its "future work" section (live staff view, adaptive content, replay-driven fixes) is literally our `/live`, Agents, and Twin tabs. Citable in the sales deck *today*.
- **The paper's hardest lessons are the ones we haven't built for yet:** 71% of raw sessions were discarded as invalid (1303 → 381) and metrics weren't comparable across exhibits. Session hygiene + metric normalization belong in P2's scorecard/deriver — cheap now, existential later.
- **Privacy-no-images is becoming table stakes, not a moat.** XY Sense makes the same claim; the CHI paper codifies it; the ICO guidance formalizes the regime we architecturally avoid. Our differentiator must be the **consent-gated PII bridge + full loop**, not privacy alone. `privacy.md` should cite the ICO biometric guidance by name.
- **Cvent is coming for the ROI narrative** — $1B product investment 2026–29, Forrester "events are a revenue engine" study, and *an anonymized cross-event benchmark dataset* — which is our fifth moat (network benchmark) being claimed by the incumbent with 340K venues. Speed on P2 matters more than ever.
- **Nobody in the dump closes the booth→CRM loop.** Cisco/XY own occupancy (sold to IT), Cvent/Popl own leads (sold to marketing), Delphize owns loyalty wallets. The gap our thesis claims is still empty.
- **Detection is now tutorial-grade commodity** (YOLO26 zone counting, homography heatmaps). Our defensibility is correctly the bus + graph + consent + report — not the detector. MAMMA / SAM 3D Body are the P6 upgrade path for twin fidelity, not a threat.

## Sources ingested

| # | Source | Type | Credibility | One-line claim |
|---|---|---|---|---|
| 1 | CHI '26 — *Using Digital Twins to Design and Evaluate Interactive Exhibitions* (Wieland et al., Konstanz) | Academic validation | **High** (peer-reviewed, full excerpt provided) | Digital twins + visitor tracking automate exhibition evaluation; replay-in-context reveals fixes; privacy-by-design mandatory |
| 2 | Cisco Spaces | Direct (occupancy/location) | Vendor marketing (discount), scale claims plausible | Network-as-sensor: 480K locations, 15B sq ft, occupancy/density/engagement for enterprises |
| 3 | XY Sense | Direct (privacy-safe occupancy) | Vendor marketing | Ceiling sensors, on-device processing, "zero images ever", 99% accuracy, workplace analytics + HVAC automation |
| 4 | Cvent | Adjacent incumbent (event platform) | Vendor marketing; Forrester-commissioned research | 8M+ events, 89% Fortune 100, CventIQ, lead capture, **cross-event anonymized benchmark data** |
| 5 | Popl (badge scanner) | Adjacent (lead capture) | Vendor marketing | Universal badge scan → qualify → enrich (Apollo partnership) → CRM sync |
| 6 | Delphize | Adjacent (verified behavior/loyalty) | Vendor marketing, unverified scale claims | Privacy-safe persistent identity + verified-behavior rewards wallet, "100M+ connections" |
| 7 | Veo Cam 3 / allclip | Adjacent (AI sports cameras) | Vendor marketing | AI camera + subscription analytics; hardware-cheap + SaaS model validated in sports |
| 8 | MAMMA (CVPR '26 Oral, MPI-IS) | Enabling tech | **High** (code released, CVPR oral) | Markerless multi-person 3D mocap from 4+ iPhones → SMPL-X, contact-aware, Vicon-grade; research-only license |
| 9 | SAM 3D Body (Meta, CVPR '26) | Enabling tech | High (award candidate) | Full 3D body mesh from a single RGB image |
| 10 | NVIDIA LocateAnything (CVPR '26) | Enabling tech | Medium-high (page 403'd; from NVIDIA post + coverage) | VLM detection/grounding, parallel bbox decoding, 138M samples, ~10× throughput |
| 11 | GR3D / FAST segmentation (CVPR '26) | Enabling tech | Medium (X previews only) | Single VLM: 2D+3D grounding + visual CoT; foundation-model segmentation at frame rate |
| 12 | FoundationStereo + Project Aria Gen 2 | Enabling tech | Medium-high (open tutorial) | Metric depth, 20 FPS, zero-shot, open source |
| 13 | GNM head (Google open-source) | Enabling tech / strategic signal | High (LinkedIn primary) | Parametric human head model toward "digital twin of humanity"; full-body roadmap |
| 14 | YOLO26 zone counting + vehicle counting (Ultralytics demos) | Enabling tech | Medium (demo videos) | People/vehicle counting in zones with YOLO + ByteTrack — our exact stack, now a tutorial |
| 15 | Homography → occupancy heatmap (Ruku) | Enabling tech | Medium (X description) | Feet→court-coordinates homography → per-player heatmaps; same math as booth heatmaps |
| 16 | DEIMv2-Wholebody49 | Enabling tech | Medium (X preview) | Unified detection + skeleton + segmentation in one 14ms inference |
| 17 | ObjectForesight world model | Enabling tech | Medium (X preview) | Predicts future 6D object trajectories — prediction beyond perception |
| 18 | ICO biometric recognition guidance (UK) | Regulatory | **High** (primary, regulator) | Special-category biometric regime: DPIA, explicit consent, systematic-monitoring risk; under DUAA review |
| 19 | ~12 links (X videos w/o previews, Instagram reels, Claude tutorials, harness-engineering, themetav3rse "Realmspace-esque") | Noise / unverifiable | — | Could not assess; flagged, not analyzed |

## Analysis

### The CHI '26 paper — read it as our academic mirror

This is the Konstanz group (ReLive, Blended Museum, Argus Vision lineage) running a
78-person, year-long, 2,000-visitor handheld-AR exhibition with full tracking, then
evaluating with a 3D twin. Their findings, mapped to us:

**O3 (opportunity) — automated, situated evaluation works at exhibition scale.** They
automated timing-and-tracking studies and replayed visitor paths in the twin to find
real design faults: AR portraits mounted at 2.0–2.7 m drew 15–23% attracting power and
35–73 s holding times; replay + a viewing-height overlay showed actual average viewing
height of 1.21 m → "lower the portraits." This is *exactly* the Twin-tab replay story
in our demo loop (PRD §3.1 step 4). **We have a peer-reviewed citation for our core
pitch.** Their vocabulary — *attracting power*, *holding time* (Visitor Studies /
Serrell standard metrics) — is the language museum and cultural-institution buyers
already speak. Our 4-layer scorecard should adopt these terms alongside our own.

**C3 (challenge) — data quality is the hidden killer.** They discarded **71%** of raw
sessions (1303 → 381) after building a cleaning pipeline: exclude outside opening
hours, unreasonable durations, missing mandatory tutorial, tracking dropouts; merge
fragmented logs. And even at 2.88 GB, their replay tool choked and needed segmentation.
**This is our future if the scorecard ingests raw detections naively.** Session-hygiene
heuristics (session bounds, duration sanity, track-merge, dropout flags) must be part
of P2's spatial-event deriver and scorecard consumer — they're far cheaper to build
now than to retrofit when a client's report contradicts their door count.

**Their second unsolved problem is our product opportunity:** they refused cross-exhibit
statistical comparisons because metrics aren't normalized for content, expected
duration, or interaction complexity. Our touchpoints already carry *type* metadata
(AR / game / screen / RFID / scent / product) with dwell targets — a per-type
normalization layer in the 4-layer scorecard ("holding time vs. expected-for-type")
is something academia explicitly couldn't do, and it's a DSR-flavored differentiator
for P2.

**Privacy: they independently arrive at our exact posture** — camera tracking must
never persist raw images, only derived data; full anonymization; inform visitors and
allow opt-out *even after the visit*. Post-visit opt-out is a nice detail our
withdrawal flow (P4) should explicitly support. Their "future directions" — live 3D
staff view (they cite Matterport), adaptive content triggered by visitor activity,
location-aware guides — is our `/live`, Agents (P3), and twin. **Their research
agenda is our shipped roadmap; that gap is the sales story.**

Also note their limitation list: two static scans, only tablets tracked, temporary
exhibition, never overcrowded. Brand activations are *harder* (dense crowds, changing
layouts) — honest expectation-setting for our CV accuracy risk (PRD §12).

### Cisco Spaces + XY Sense — the occupancy establishment

**Cisco Spaces** turns existing Cisco network gear into location sensors (Wi-Fi/BLE):
occupancy, density, indoor nav, captive portals, contextual engagement. 14K enterprise
customers, sold to IT/facilities. **XY Sense** sells ceiling occupancy sensors with an
on-device, "zero images ever" privacy claim, 2-second live views, an API, and building
automation (HVAC/lighting) — sold to workplace teams.

For realmspace: both **measure occupancy, not behavior→pipeline**, and both are
*permanent-install* products sold to IT — the opposite of our flight-case,
marketing-budget, per-activation motion (PRD §1.2: "IT review is bypassed when the kit
shows up with the booth"). Neither threatens the wedge. Two real implications:

1. **XY Sense proves privacy-safe occupancy is now an enterprise expectation** —
   our privacy claim must evolve from "we don't do faces" (they don't either) to the
   consent-gated PII bridge and the attributed outcome, which they structurally lack.
2. They validate the **venue-operator secondary ICP** (P6 permanent installs,
   `VISION.md` H3) — enterprise budgets exist for this data; we're the activation-
   shaped version of a category they've proven at building scale.

### Cvent + Popl — the budget owners without the signal

**Cvent** owns the event budget conversation: registration, badging, lead capture
(iCapture, Jifflenow), attendee apps, and now CventIQ + a Forrester-commissioned
"events are a revenue engine" study + "The Presence Premium" positioning. Critically,
their pitch now includes *"anonymized data, aggregated from millions of events,
calibrating every recommendation"* — **that is our fifth moat (network benchmark,
`competitive-landscape.md` §3.5) being claimed by the incumbent** with $1B of
2026–29 product investment behind it. But Cvent has *no spatial signal*: they know
who registered, who scanned a badge, which meetings happened — not what a visitor
physically did inside the activation. They are simultaneously (a) the most dangerous
long-term competitor if they buy spatial capability, and (b) the most valuable
integration/adjacency — our LeadHandoff could land in their ecosystem (P4).

**Popl** owns badge-scan lead capture → CRM, with an Apollo enrichment partnership
and "Ask Popl" AI. Their "works at every event" universality is exactly what our
consent-capture surfaces must interoperate with — badge scans from Popl-class tools
should be ingestible as `identity.resolved`-via-`form` events (P4, BYO webhook).
Their Apollo deal also validates our optional T3 enrichment adapter as table stakes,
not a differentiator.

### Delphize — positioning collision, different game

"Verified human behavior" infrastructure: privacy-safe persistent identity,
zero-party-auth wallet, missions/streaks/rewards, auditable attribution, "no personal
data stored." The *language* collides with ours (privacy-safe, verified, attribution),
but the game is loyalty mechanics — rewarding verified actions to drive retention —
not measuring space. Watch them on one axis: their wallet could become the
**consent-capture surface of record** for consumer brands, which is either a
competitor to our QR/kiosk surfaces or a clean integration (their wallet grants,
our graph links). No action now; monitor.

### Enabling tech — the CVPR '26 wave, and what it does to our defensibility

The pattern across MAMMA, SAM 3D Body, LocateAnything, GR3D, FAST segmentation,
FoundationStereo, DEIMv2, and the YOLO26 demos: **foundation models have eaten
detection, and 3D human reconstruction is now iPhone-accessible.** Three implications:

1. **Zone counting/dwell is tutorial-grade.** YOLO26 + ByteTrack zone counting and
   homography heatmaps are weekend demos now. Our defensibility was never the
   detector — it's the bus, graph, consent model, and ROI narrative. This dump
   confirms `gtm.md`'s "stay opinionated and let YOLO + VLM do the work" and the
   current P2 priority (ROI proof > detection upgrades).
2. **The twin-fidelity upgrade path clarified itself.** MAMMA (4+ iPhones →
   SMPL-X, contact-aware, CVPR '26 oral, code + GUI released) and SAM 3D Body
   (single RGB → body mesh) point at Pavilion-tier pose-grade avatars, real gaze,
   and the paper's viewing-height-style ergonomics analysis — as a **P6
   multi-camera-fusion** upgrade, not now. Caveat: MAMMA's license is
   non-commercial research only; SAM-3D-Body-class single-camera models are the
   more deployable path. NVIDIA LocateAnything (text-prompt grounding, ~10×
   throughput) is the natural future swap for COCO-SSD class detection when
   browser/edge compute allows — tracking stays ours.
3. **Nothing here threatens us.** No released model does consent-gated identity,
   durable spatial event graphs, or ROI reporting. Prediction models
   (ObjectForesight) are a P5 floor-orchestrator input, not a competitor.

Google's GNM head ("digital twin of humanity" parametric models) is the strategic
counter-signal: the industry giants are racing toward *identified, photoreal* human
modeling — the exact thing our privacy architecture forbids. That contrast sharpens
the sales narrative: we measure space, never people; they model people. Different
products, different compliance regimes.

### ICO biometric recognition guidance — cite it, don't fear it

The UK ICO guidance confirms biometric recognition (unique identification via
technical processing of physical/behavioral characteristics) is special-category:
DPIA expected, explicit consent or another Art. 9 condition, accuracy/discrimination
duties, and *systematic monitoring of public spaces* named as a risk factor. It also
blesses PET patterns we already embody: on-device processing, template protection,
data minimisation. **realmspace does no biometric recognition** — session-scoped
anonymous IDs, no faces, no cross-session re-identification — so we sit outside the
special-category regime entirely. This is a compliance *asset*: `privacy.md` should
cite the guidance by name and make that argument in the regulator's own vocabulary.
(Note: guidance is under review post-Data (Use and Access) Act — re-check at P4.)

## Benchmark vs. realmspace

| Dimension | Best-in-dump | realmspace (shipped tonight) | Verdict |
|---|---|---|---|
| Anonymous spatial tracking | XY Sense (occupancy), CHI paper (research) | Live COCO-SSD + centroid tracker → durable bus + graph | Comparable; ours persists to a queryable graph |
| Situated 3D replay | ReLive (research tool) | Twin tab (seed paths; bus replay in P2) | Behind research today; replay-from-bus closes it |
| Automated evaluation metrics | CHI paper (museum metrics) | 4-layer scorecard (mock-fed; P2 makes real) | Ahead of us in vocabulary; behind in productization |
| Occupancy at enterprise scale | Cisco Spaces (480K locations) | Single-camera edge kit | Different market (permanent vs per-activation) |
| Lead capture → CRM | Popl, Cvent iCapture | Contracts only (P4) | Behind; but they lack spatial intent fields |
| Behavior→pipeline attribution | **Nobody** | Bus + graph + LeadHandoff schema | **The gap holds** |
| Privacy-no-images | XY Sense | Same claim + consent-gated PII bridge | Table stakes for them; our bridge is the differentiator |
| Cross-event benchmark | Cvent (claimed, in-progress) | Designed (P6), not built | **Threatened — speed matters** |
| 3D human fidelity | MAMMA / SAM 3D Body (research) | Anonymous dot-avatars | Upgrade path in P6, license-aware |

## Opportunities (ranked)

1. **Cite the CHI paper in the sales deck + adopt museum-standard metric vocabulary**
   (attracting power, holding time, sweep) in the scorecard and report. Client job:
   Prove ROI. Cost: ~1 day (copy + P2 scorecard labels). Placement: P2 + landing copy.
2. **Build session-hygiene + normalization into P2's deriver/scorecard** (opening-
   hours/session-bounds filter, duration sanity, track-merge, per-touchpoint-type
   expected-duration normalization). This is the paper's 71%-data-loss lesson,
   pre-empted. Client job: Prove ROI (credible numbers). Cost: 2–4 days. Placement:
   P2 spatial-event deriver + scorecard consumer — add as explicit task lines.
3. **Badge-scanner/Popl-class ingestion as a consent-capture surface** (BYO webhook
   → `identity.resolved` via `form`). Client job: Capture leads. Cost: small — it's
   our existing P4 webhook-in surface with a named partner shape. Placement: P4.
4. **Position against Cvent's benchmark claim now** — "their benchmark is
   registrations; ours is what people *did*." Founder-led GTM copy, no code. Also:
   Cvent/iCapture ecosystems as P4 adapter targets.
5. **Museum/cultural-institution vertical, named** — the paper + our existing ICP
   mention (museums, art fairs) + Visitor-Studies vocabulary make this a cheap,
   credible wedge expansion for the GTM sprint. Founder-led, no code.
6. **P6 pose-grade twin upgrade (SAM-3D-Body-class single-RGB models)** — record as
   the multi-camera-fusion path; skip MAMMA's non-commercial license. Placement:
   P6, already correctly sequenced.

## Threats & blind spots (ranked)

1. **Cvent claims the cross-event benchmark** with $1B investment and 340K-venue
   distribution. Mechanism: their dataset is registration/engagement metadata, not
   spatial behavior — but buyers won't distinguish unless we ship ours. Likelihood:
   high. Mitigation: P2 report + P6 benchmark, sequenced correctly; sharpen
   differentiation copy now.
2. **Data-quality collapse à la the 71% discard.** If our first client report
   contradicts the client's door count, the product dies. Likelihood: medium without
   hygiene heuristics. Mitigation: opportunity #2, P2.
3. **Privacy-claim commoditization.** XY Sense + the CHI paper + ICO guidance make
   no-images baseline. Likelihood: certain. Mitigation: lead with the consent-gated
   bridge + attribution; privacy becomes the qualifier, not the headline.
4. **Cisco/XY moving from permanent installs toward venues/events.** Low near-term
   (their sales motion is IT-led; activations are marketing-led). Mitigation: speed +
   the flight-case distribution advantage.
5. **Delphize-class wallets becoming the consent surface of record.** Low-medium.
   Mitigation: P4 surfaces are BYO/integration-friendly by design.
6. **Unverifiable links (~12).** Including one flagged "Realmspace-esque"
   (themetav3rse). Likelihood of a hidden competitor in the noise: low but nonzero.
   Mitigation: re-send with visible content if it matters.

## Recommended actions

1. **P2 roadmap addition (docs/roadmap.md):** add two task lines — (a) session-hygiene
   heuristics in the spatial-event deriver (bounds, duration sanity, track-merge,
   dropout flags); (b) per-touchpoint-type metric normalization in the scorecard.
2. **Copy updates:** adopt *attracting power* / *holding time* vocabulary in
   `roi-framework.md` and report templates; add the CHI '26 citation to the landing
   page / sales deck ("peer-reviewed at CHI '26: situated twin replay works — we
   productize it").
3. **`docs/privacy.md`:** add an explicit section citing the ICO biometric-recognition
   guidance and arguing, in regulatory vocabulary, why realmspace sits outside the
   special-category regime (no unique identification, no templates, no persistence of
   images); add post-visit opt-out to the P4 withdrawal flow description.
4. **`docs/competitive-landscape.md`:** add rows — Cisco Spaces + XY Sense (new
   category: privacy-safe occupancy, IT-sold, permanent), Cvent (event-platform
   incumbent; benchmark-narrative collision flagged), Popl (lead capture;
   integration target), Delphize (verified-behavior wallet; monitor). Strengthen
   moat #2 wording from "privacy" to "consent-gated identity bridge."
5. **GTM sprint:** add museum/cultural-institution targets to the 50-email list;
   reference the CHI paper's museum context in the Loom script.
6. **Do nothing (concluded):** no detection-model swap (commodity is fine);
   no MAMMA adoption (license); no response to Delphize (monitor); no roadmap
   reordering — the dump confirms current P2 priority.

## Open questions to validate

1. What did the "Realmspace-esque" themetav3rse post actually show? (Needs the
   content re-sent — potential lookalike product.)
2. Does Cvent's benchmark dataset include any spatial/behavioral fields, or only
   registration/engagement metadata? (Cheap: their Forrester report + partner
   briefings; informs how loudly we must differentiate.)
3. Is Popl (or Cvent iCapture) webhook-accessible for lead ingestion in practice?
   (Determines whether P4 badge-scan ingestion is config or a partnership.)
4. SAM 3D Body licensing/terms for commercial edge deployment? (P6 spike, 1 hour.)
5. Would the Konstanz group (ReLive/Blended Museum) take a call? Academic
   credibility + talent pipeline + potential museum intros. (Founder email, 15 min.)
