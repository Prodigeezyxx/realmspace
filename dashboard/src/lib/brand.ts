/**
 * realmspace — the brand palette, for the places CSS cannot reach.
 *
 * `app/globals.css` is the source of truth for anything styleable, and almost
 * everything is: a class, a token, a `var(--accent)`. This file exists for the
 * handful of call sites that genuinely cannot read one —
 *
 *   - **SVG paint attributes.** `<stop stopColor>` and `<path stroke>` take a
 *     colour value, not a custom property, in the charts and the heatmap.
 *   - **Three.js materials.** `twin/TwinScene.tsx` builds colours for a WebGL
 *     scene, which has no stylesheet.
 *   - **Seed data.** A zone's colour is a *stored value* on the session, not a
 *     presentation choice — an operator can change it, and it has to survive
 *     being written to the graph and read back. `session/presets.ts` and
 *     `prefabs/standard-hall.ts` are picking a default, not styling anything.
 *
 * Before this, those nineteen call sites each carried the literal `#42faa1`
 * across nine files, so re-tokening the UI to `docs/brand.md` meant finding all
 * of them. Now it means changing two files: this one and `globals.css`.
 *
 * **Keep the values here in step with the tokens there.** They are two
 * expressions of one palette, and nothing enforces the agreement — a mismatch
 * shows up as a chart line that is a slightly different colour from the pill
 * beside it, which is the kind of thing nobody reports and everybody notices.
 */

/** Data — brand.md §3: "spatial data viz, flow/movement, secondary UI". */
export const BRAND_DATA = "#00d4aa";

/**
 * Action — brand.md §3: "only for things that demand immediate attention:
 * primary CTAs, live alerts, threshold-breach warnings, staff prompts. Do not
 * overuse." Almost nothing in a visualisation qualifies; a chart series is
 * telemetry, not an alarm.
 */
export const BRAND_ACTION = "#ff5c00";

/** Base — the canvas everything above is drawn on. */
export const BRAND_BASE = "#0a0b10";

/** Secondary accent, for charts where teal and orange are not enough. */
export const BRAND_BLUE = "#3b82f6";
