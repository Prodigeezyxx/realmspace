# realmspace — Brand Reference

> Source: **realmspace brand guidelines.pdf** (© Miracle Otugo, 26/06/2026).
> This file is the canonical, machine-readable distillation of the brand book so
> the product, docs and future UI stay on-brand. When the PDF and this file
> disagree, the PDF wins — update this file to match.

**The brand name is always written lowercase: `realmspace`.** Never "RealmSpace",
"Realmspace" or "REALMSPACE" in product copy, UI, or marketing. (Historical docs
in this repo use mixed case; new work uses lowercase.)

---

## 1. The symbol

A geometric, monogram-based mark — the visual anchor of the brand.

- **The Frame** — a partial outer square = the physical **"Realm"** (defined
  environments, boundaries, spaces we monitor). Intentionally **broken** to signify
  openness to data input, sensor integration, and the fluidity of space.
- **The Diagonal** — a jagged stroke cutting through the square = **data
  trajectories**: movement, traffic patterns, dwell time, the flow of telemetry
  across physical boundaries.
- **The Monogram** — an interlocking lowercase **r + s** (Realm + Space). A
  subliminal signature: even without the wordmark, the symbol tells the story.
- **The Aesthetic** — inspired by **7-segment displays, digital terminal grids,
  and ASCII encoding**. Strict **45° angles**, blocky modular geometry. Reinforces
  the core values: **math, reliability, precision, data fidelity.** The blocky
  form is designed to tile into **seamless repeating patterns** for collateral.

**Hard rule:** the mark must **never be curved, smoothed, or rounded.** Its strict
grid geometry is the anchor of the brand.

### Logo variants
1. Sign · 2. Horizontal – Primary · 3. Emblem · 4. Vertical – Primary ·
5. Sign – alternative · 6. Vertical – Name Only.
*Seek advice when adapting the logo to a new medium.*

### Improper usage (never do)
Rotation · scaling below **24px** minimum · changing proportions · stretching /
distorting · colour modifications · logo on a matching background · rearranging
components · altering elements.

---

## 2. Typography

| Role | Typeface | Why |
|---|---|---|
| **Headlines** | **Sora** | Geometric, sophisticated, modern; matches the wordmark for a cohesive brand presence. |
| **Body / UI** | **Inter** | UI/UX gold standard; neutral and legible at small sizes for dense data dashboards. |
| **Data / support** | **IBM Plex Mono** | Monospaced, terminal-style; mirrors the ASCII / 7-segment logo aesthetic. Reserved for raw, engineered telemetry contexts (metrics, IDs, code, event logs). |

**Usage rule:** isolate IBM Plex Mono for data-driven contexts — it is a visual
cue that the user is looking at real, extracted telemetry. Sora for headlines,
Inter for everything else.

> ✅ **Migrated 2026-08-24.** `app/layout.tsx` loads all three; `--font-head`
> (Sora), `--font-sans` (Inter) and `--font-mono` (IBM Plex Mono) are the
> tokens. The wordmark is set in Sora for the reason the table gives.

---

## 3. Colour (canonical palette)

Dark-mode-first. The black base anchors the physical Realm; white gives pinpoint
data clarity; neon orange injects urgency for alerts; teal/cyan is the fluid
digital thread connecting spatial movement to insight.

| Token | Role | HEX | CMYK |
|---|---|---|---|
| `--rs-base` | **Base** — primary dark canvas ("dark mode first") | `#0A0B10` | 89 / 78 / 62 / 97 |
| `--rs-foreground` | **Foreground** — primary text, active UI, high-priority labels | `#FFFFFF` | 0 / 0 / 0 / 0 |
| `--rs-action` | **Action** — CTAs, alerts, high-priority telemetry warnings | `#FF5C00` | 0 / 74 / 95 / 0 |
| `--rs-data` | **Data** — spatial data viz, flow/movement, secondary UI | `#00D4AA` | 67 / 0 / 47 / 0 |
| `--rs-accent-blue` | Secondary accent (from brand colour page) | `#3B82F6` | 75 / 50 / 0 / 0 |

**Semantic usage**
- **Base `#0A0B10`** — app background, long-form data monitoring surfaces.
- **Foreground `#FFFFFF`** — reserved for primary text & active elements; max legibility.
- **Action `#FF5C00`** — *only* for things that demand immediate attention: primary
  CTAs, live alerts, threshold-breach warnings, staff prompts. Do not overuse.
- **Data `#00D4AA`** — heatmaps, path/flow lines in the twin, zone accents,
  charts representing movement. This is the "telemetry" colour.
- **Accent blue `#3B82F6`** — supporting charts / links where orange+teal aren't enough.

> ✅ **Re-tokened 2026-08-24.** `--accent` is **Data teal**, because that is
> what the general-purpose accent is actually used for, and `--accent-action`
> carries **Action orange** to the two uses §3 names: primary CTAs and staff
> prompts. Functional status colours are kept, as this section asks.
>
> Two things the re-token learned, recorded here because they constrain future
> work:
>
> 1. **A "selected" state is not a call to action.** Three controls used the
>    primary button style to show they were switched on, and each became a
>    permanent alert when primary turned orange. There is a `selected` Button
>    variant now, in the calm accent.
> 2. **Both accents fail on paper untreated** — teal 1.91:1 and orange 3.10:1
>    against white. The print block carries darkened, hue-preserved variants
>    (`#007e65` and `#c44700`) clearing 4.5:1. Anything adding a colour to the
>    palette owes the same pair.

### The CSS custom properties, as shipped

`app/globals.css` is the source of truth; `lib/brand.ts` mirrors the three
values the runtime needs where a CSS variable cannot reach — SVG paint, a
Three.js material, and zone seed data. Keep the two in step.

```css
:root {
  --rs-base:        #0A0B10;
  --rs-foreground:  #FFFFFF;
  --rs-action:      #FF5C00;
  --rs-data:        #00D4AA;
  --rs-accent-blue: #3B82F6;

  /* functional status (retain) */
  --rs-success: #10B981;
  --rs-warning: #F59E0B;
  --rs-error:   #EF4444;

  /* type */
  --rs-font-head: "Sora", system-ui, sans-serif;
  --rs-font-body: "Inter", system-ui, sans-serif;
  --rs-font-mono: "IBM Plex Mono", ui-monospace, monospace;
}
```

---

## 4. Voice & values (implied by the brand system)

The visual system encodes the product voice — carry it into copy:

- **Precise, engineered, trustworthy** — we speak in real numbers and telemetry,
  not vibes. Mono font for the receipts.
- **Calm authority** — dark, high-contrast, frictionless. We monitor without noise.
- **Privacy as posture** — "we monitor space, not people." The broken frame is
  data in/out, never surveillance of identity (see `consent-and-identity.md`).
- **Physical ↔ digital bridge** — every metaphor ties a footstep to an insight.

---

## 5. How this brand maps to the product pipeline

The brand story *is* the product story:

- **The Frame (physical Realm)** → **Attract & Immerse** — the booths/spaces.
- **The broken edges (sensor in/out)** → **Measure** — realmspace perception.
- **The Diagonal (flow → insight)** → **Attribute & Follow up** — turning
  movement into ROI, leads, and CRM outcomes.

See `VISION.md` for the full pipeline thesis.
