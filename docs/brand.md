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

> ⚠️ **Migration note:** the current dashboard ships Plus Jakarta Sans +
> JetBrains Mono. To align with the brand book, migrate headlines → Sora,
> body → Inter, mono → IBM Plex Mono. Tracked in the roadmap (polish phase).

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

> ⚠️ **Brand-vs-code note:** the current dashboard + MVP mindmap use a blue
> primary (`#0F7FFF`) and a green/amber/red semantic ramp. The brand book's
> canonical action colour is **neon orange `#FF5C00`** and data colour is
> **teal `#00D4AA`**. Treat this file as the source of truth and re-token the
> UI during the polish phase (see `roadmap.md`). Keep functional status colours
> (success/warning/error) but map brand accents to orange/teal.

### Suggested CSS custom properties (for when we re-token the UI)

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
