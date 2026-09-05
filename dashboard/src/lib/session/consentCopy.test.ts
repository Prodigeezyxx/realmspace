import { describe, expect, it } from "vitest";

import { DEFAULT_CONSENT_COPY, copyVersionFor } from "./consentCopy";

describe("copyVersionFor", () => {
  it("moves when the words move", () => {
    // The property the whole field exists for: `event-bus-spec.md` §3 calls
    // `copy_version` the only thing that settles a withdrawal argued after the
    // fact, and a version that stayed put through an edit would record two
    // different sentences under one name.
    const at = new Date("2026-09-03T00:00:00Z");
    const before = copyVersionFor("We keep your details with this visit.", at);
    const after = copyVersionFor("We keep your details with every visit.", at);
    expect(before).not.toEqual(after);
  });

  it("is stable for the same wording, whitespace aside", () => {
    const at = new Date("2026-09-03T00:00:00Z");
    expect(copyVersionFor("  We keep\n your details. ", at)).toEqual(
      copyVersionFor("We keep your details.", at)
    );
  });

  it("says nothing rather than something when there is no wording", () => {
    // An empty version would be a name for a sentence nobody wrote, and the
    // backend refuses to mint a kiosk without one — this is what makes that
    // refusal reachable rather than surprising.
    expect(copyVersionFor("   ")).toBe("");
  });

  it("carries the month it was written in", () => {
    const version = copyVersionFor(DEFAULT_CONSENT_COPY, new Date("2026-09-03T00:00:00Z"));
    expect(version.startsWith("consent-2026-09-")).toBe(true);
  });
});
