/**
 * The palette, as arithmetic rather than as a claim.
 *
 * `roadmap.md`'s Brand re-token bullet says "a script asserts every accent in
 * both blocks, and the report was read in a forced print view rather than taken
 * on trust". The forced print view happened; the script did not exist. The
 * check was run once by hand and the sentence outlived it, which is how the
 * headline ROI figure came to print at **1.36:1** against white in the first
 * place — found by looking at a page, not by reasoning about one.
 *
 * So this reads `globals.css` itself rather than a copy of the values. The
 * stylesheet is the source of truth for anything styleable (`brand.ts` exists
 * only for SVG paint attributes, Three.js materials and stored zone colours),
 * and a test that asserted against its own transcription would pass forever
 * while the product drifted.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { BRAND_ACTION, BRAND_BASE, BRAND_DATA } from "./brand";

// From the project root rather than `import.meta.url`: vitest serves this
// module over an http-scheme URL, which `readFileSync` will not take.
const CSS = readFileSync(join(process.cwd(), "src/app/globals.css"), "utf8");

/** WCAG 2.1 relative luminance. */
function luminance(hex: string): number {
  const channels = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const linear = channels.map((c) =>
    c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  );
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/**
 * The two palettes, split at `@media print`.
 *
 * Only `#rrggbb` values are collected. The `--accent-soft` tints are `rgba()`
 * fills behind other things and have no contrast obligation of their own; a
 * test that demanded 4.5:1 from a 12%-opacity wash would be asserting the
 * wrong thing loudly.
 */
function accents(block: string): Map<string, string> {
  const found = new Map<string, string>();
  for (const [, token, value] of block.matchAll(
    /(--accent[\w-]*)\s*:\s*(#[0-9a-fA-F]{6})/g
  )) {
    found.set(token, value.toLowerCase());
  }
  return found;
}

const splitAt = CSS.indexOf("@media print");
const SCREEN = accents(CSS.slice(0, splitAt));
const PRINT = accents(CSS.slice(splitAt));

/** Body text, per WCAG AA. The report is read, not glanced at. */
const AA = 4.5;

describe("brand accents", () => {
  it("has accents to check in both blocks", () => {
    // Guards the parse, not the palette. A regex that quietly matched nothing
    // would make every assertion below vacuously true — the most comfortable
    // way for this file to stop testing anything.
    expect(splitAt).toBeGreaterThan(0);
    expect(SCREEN.size).toBeGreaterThan(5);
    expect(PRINT.size).toBeGreaterThan(5);
  });

  it.each([...SCREEN])("%s is readable on the near-black canvas", (_t, hex) => {
    expect(contrast(hex, "#0a0b10")).toBeGreaterThanOrEqual(AA);
  });

  it.each([...PRINT])("%s is readable on paper", (_t, hex) => {
    expect(contrast(hex, "#ffffff")).toBeGreaterThanOrEqual(AA);
  });

  it("defines every accent in both blocks", () => {
    // The stylesheet's own comment names this failure: "a token defined in only
    // one of the two blocks is exactly how the black-rectangle bug happened."
    expect([...PRINT.keys()].sort()).toEqual([...SCREEN.keys()].sort());
  });

  it("keeps the two brand accents at the values brand.md walked them down to", () => {
    // Quoted in `globals.css` and in the roadmap. Pinned because the next
    // person to nudge a hue has to see these move.
    expect(contrast(PRINT.get("--accent")!, "#ffffff")).toBeCloseTo(5.03, 2);
    expect(contrast(PRINT.get("--accent-action")!, "#ffffff")).toBeCloseTo(4.94, 2);
  });

  it("does not point the calm accent at the Action orange", () => {
    // brand.md §3: Action is "only for things that demand immediate attention …
    // do not overuse". `--accent` is what almost every surface wears, so
    // pointing it there paints an alarm on the whole product.
    expect(SCREEN.get("--accent")).not.toBe(SCREEN.get("--accent-action"));
    expect(SCREEN.get("--accent")).toBe(BRAND_DATA);
  });
});

describe("brand.ts and globals.css", () => {
  it("are two expressions of one palette", () => {
    // `brand.ts`'s own docstring: "Keep the values here in step with the tokens
    // there … nothing enforces the agreement — a mismatch shows up as a chart
    // line that is a slightly different colour from the pill beside it, which
    // is the kind of thing nobody reports and everybody notices." This is the
    // enforcement it said was missing.
    expect(SCREEN.get("--accent")).toBe(BRAND_DATA);
    expect(SCREEN.get("--accent-action")).toBe(BRAND_ACTION);
    expect(CSS).toContain(`--bg-base: ${BRAND_BASE}`);
  });
});
