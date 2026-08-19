import { describe, expect, it } from "vitest";

import { liveInsights } from "./derive";
import type { RealmEvent } from "@/lib/contracts/events";

function insight(over: Record<string, unknown> = {}, at = 1_000): RealmEvent {
  return {
    seq: 42,
    eventId: "e1",
    tenantId: "t",
    sessionId: "s",
    type: "insight.generated",
    occurredAt: at,
    recordedAt: at,
    payload: {
      text: "Product Pod held attention longest.",
      refs: [{ seq: 7, event_id: "src-1" }],
      window: { minutes: 10 },
      basis: "deterministic",
      truncated: false,
      ...over,
    },
  } as unknown as RealmEvent;
}

describe("liveInsights", () => {
  it("carries the refs, because the click-through is the whole contract", () => {
    const [row] = liveInsights([insight()]);
    expect(row.refs).toEqual([{ seq: 7, event_id: "src-1" }]);
    expect(row.seq).toBe(42);
  });

  it("keeps the basis so a composed sentence is never shown as a model's", () => {
    expect(liveInsights([insight()])[0].basis).toBe("deterministic");
    expect(liveInsights([insight({ basis: "anthropic" })])[0].basis).toBe(
      "anthropic"
    );
  });

  it("surfaces a truncated window rather than describing part of it as whole", () => {
    expect(liveInsights([insight({ truncated: true })])[0].truncated).toBe(true);
  });

  it("is newest first, and does not expire", () => {
    // Unlike staff prompts: a prompt goes stale, while an observation about a
    // window that already closed stays true.
    const rows = liveInsights([
      insight({ text: "older" }, 1_000),
      insight({ text: "newer" }, 9_000),
    ]);
    expect(rows.map((r) => r.text)).toEqual(["newer", "older"]);
  });

  it("ignores everything that is not an insight", () => {
    const other = { ...insight(), type: "spatial.dwell" } as RealmEvent;
    expect(liveInsights([other])).toEqual([]);
  });
});
