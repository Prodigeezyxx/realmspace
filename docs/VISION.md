# realmspace — Vision & Product Thesis

> **Watch the room think.**
>
> One product. One pipeline. realmspace is the **measure-and-attribute** layer
> that turns physical space into provable business outcomes.

This document is the north star. Every feature we build must serve the single
pipeline described here. If a proposed feature doesn't move a client further
along **Attract → Measure → Attribute/Follow-up**, it's out of scope.

---

## 1. There is one realmspace

realmspace is **not** two products (measurement vs. attribution). It is a single
product — the prototype in this repo today — plus a sequence of **follow-up
features** layered onto that same prototype: the event bus, attribution, and
integrations are *downstream capabilities of one product*, not a second vision.

The company pipeline is one continuous line:

```
   ATTRACT & IMMERSE          MEASURE                ATTRIBUTE & FOLLOW UP
   ┌────────────────┐   ┌──────────────────┐   ┌───────────────────────────┐
   │ Floats booths  │   │  realmspace      │   │ ROI proving · lead capture│
   │ + immersive    │──▶│  (this prototype)│──▶│ consent-gated identity ·  │
   │ experiences    │   │  spatial telemetry│   │ universal CRM + BYO sync  │
   └────────────────┘   └──────────────────┘   └───────────────────────────┘
      (we/partners           (what exists           (the follow-up features
       already sell)          today, hardened)        we're building next)
```

- **Attract & Immerse** — the booths and immersive experiences that draw people
  in and make them dwell. Floats already delivers these; other operators buy
  realmspace to instrument *their* booths.
- **Measure** — realmspace as it exists now: anonymous spatial telemetry, live
  dashboard, 3D digital twin, ask-the-room, report.
- **Attribute & Follow up** — the new features, sequenced by real client value:
  prove ROI, capture leads, resolve identity (with consent), and push outcomes
  into the tools clients already use (CRMs + bring-your-own).

---

## 2. realmspace is a standalone commercial product

realmspace is sold to **any booth/activation operator — not only Floats.**

That single fact drives major architecture decisions:

- **Multi-tenant** — strict per-tenant data isolation, self-serve onboarding,
  billing hooks. (See `multi-tenant.md`.)
- **Universal integrations, no lock-in** — support all top CRMs *and* a generic
  "bring-your-own" connector, because we can't assume a customer's stack.
  (See `integrations.md`.)
- **Vertical advantage for Floats, horizontal product for the market** — Floats
  gets the tightest version (we design the booth *and* the measurement), but the
  product must stand alone for operators who only buy the software/kit.

Floats' vertical integration is still the **wedge and the flagship reference
customer** — but the business we're building is a product company, not an
internal tool.

---

## 3. The thesis — why this wins (grounded, not hallucinated)

**Market truth (from our own deep-dive research):**
TAM $2–5B · SAM (Africa + select global) $60–350M · SOM $1–10M ARR / 5yr.
The competitive field is **fragmented by category** and nobody owns the full loop:

- **Retail analytics** (RetailNext, Trax, Placer.ai) — measure footfall, don't
  act, not built for activations.
- **Venue / event twins** (VenueTwin, Smart Spatial) — visualise, don't attribute
  to pipeline.
- **Generic CV** (AWS Rekognition, Google Vision) — toolkits not products, and
  they do face-recognition — the exact thing our privacy posture forbids.

**Where realmspace uniquely wins — the full-loop moat:**

1. **We close the loop no one else closes** — from a footstep, to an anonymous
   dwell signal, to (with consent) an identified lead, to an attributed CRM
   outcome and an ROI proof. Competitors own one slice; realmspace owns the line.
2. **Privacy is architecturally honest** — anonymous-by-default, local-first,
   no faces. Identity only ever appears through an explicit consent gate. This is
   a moat against the generic-CV incumbents that *can't* make that claim.
3. **For Floats specifically: the vertical stack** — we design the booth, the
   experience, and the measurement, so cameras live where they should, every
   interactive surface emits structured events for free, and distribution CAC is
   ~zero (every booth we ship is a warehoused deployment).
4. **Integration-neutral** — because we sell to the public and push into any CRM,
   we become the *default* measurement layer regardless of the customer's stack.

**One line:**
> *No CV vendor ships the booth. No experiential agency ships the measurement.
> No footfall tool proves pipeline. realmspace owns the whole line — space,
> experience, measurement, and attributed outcome — which is why a footstep
> becomes provable revenue while competitors are still counting doors.*

---

## 4. The jobs realmspace does (client use cases, in priority order)

Sequencing is driven by **what clients ask for and pay for first**, not by
technical layers. Our working priority (validate with real deals):

1. **Prove ROI** — "was the activation worth it?" The wedge: it directly
   justifies more booth spend. Report + a real proving toolkit
   (see `roi-framework.md`).
2. **Capture leads** — turn engaged visitors into contactable records
   (badge/QR/kiosk/form), with consent.
3. **Resolve identity (consent-gated)** — merge the anonymous spatial path with a
   named lead once consent exists. Attribution supports **both** anonymous and
   PII modes. (See `consent-and-identity.md`.)
4. **Sync to CRM + follow up** — push qualified, enriched outcomes into any CRM
   (or BYO), and draft contextual follow-ups. (See `integrations.md`.)

---

## 5. Anonymous *and* PII — one model, two modes

realmspace attribution runs in two modes on the same data model:

- **Anonymous mode (default, always on):** session-scoped IDs (`P-217`), no PII,
  privacy posture intact. Powers heatmaps, dwell, funnels, ROI proving.
- **PII mode (unlocked per-person by a consent event):** the anonymous path is
  linked to an identified contact. Everything downstream of the consent event —
  identity resolution, CRM sync, personalised follow-up — is gated on it.

The consent event is the switch. Before it: pure anonymous telemetry. After it:
an attributed lead. No PII is ever written without a recorded consent basis.
This is designed into the schema with hard redlines — see
`consent-and-identity.md`.

---

## 6. The event bus is plumbing, not philosophy

To feed **Measure → Attribute/Follow-up** we need a spine that can carry a signal
from perception to a CRM reliably, offline, and replayably. That spine is an
**append-only, idempotent, replayable event bus.** It is an *implementation
detail of the follow-up features* — not a separate product and not a rewrite of
the prototype. Every new capability (attribution, CRM sync, SDR follow-up) is a
**new consumer on the same bus.** (See `event-bus-spec.md`.)

Design constraints forced by the real world (conference WiFi, latency, failures):

- **Local-first** — the full loop runs on the edge device offline; cloud sync is
  an optional, post-session, anonymised replay upload.
- **Real-time actions run on the edge** (< 3s), never a cloud round-trip.
- **Idempotent + replayable** — a failed CRM sync just replays; nothing is lost.

---

## 7. What stays the center of gravity

The existing prototype — landing, live (real on-device tracking), twin, ask,
agents, report — **stays the heart of the product.** We *harden and extend* it;
we do not re-architect around it. New backend work exists to make the prototype's
mocked pieces real and to add the follow-up features behind it.

See `roadmap.md` for the phased, use-case-driven plan and `brand.md` for the
visual/verbal identity every surface must follow.
