/**
 * The cost meter's reader, as executable claims.
 *
 * The property worth pinning is the one that would be easiest to get wrong and
 * hardest to notice: **units are not interchangeable**. A summary that adds
 * tokens to dollars still renders, still looks like unit economics, and is
 * wrong by whatever the token count happens to be.
 */

import { describe, expect, it } from "vitest";

import type { RealmEvent, RealmEventType } from "@/lib/contracts";
import { summarizeCost } from "./cost";

let seq = 0;
function ev(type: RealmEventType, payload: Record<string, unknown>): RealmEvent {
  seq++;
  return {
    seq,
    eventId: `e-${seq}`,
    tenantId: "t_test",
    sessionId: "s_1",
    type,
    payload: payload as never,
    occurredAt: Date.parse("2026-08-11T10:00:00Z") + seq * 1000,
    recordedAt: 0,
  };
}

const cost = (kind: string, amount: number, unit: string) =>
  ev("cost.metered", { kind, amount, unit });

describe("summarizeCost", () => {
  it("reports nothing metered on an empty log, rather than zero spend", () => {
    const s = summarizeCost([]);
    expect(s.metered).toBe(false);
    expect(s.lines).toEqual([]);
    expect(s.money).toBeNull();
    expect(s.perEngagedVisit).toBeNull();
  });

  it("groups by kind and unit, largest first", () => {
    const s = summarizeCost([
      cost("llm_tokens", 1200, "tokens"),
      cost("llm_tokens", 800, "tokens"),
      cost("enrichment_credit", 3, "credits"),
    ]);

    expect(s.metered).toBe(true);
    expect(s.lines).toEqual([
      { kind: "llm_tokens", unit: "tokens", amount: 2000, events: 2 },
      { kind: "enrichment_credit", unit: "credits", amount: 3, events: 1 },
    ]);
  });

  it("totals money only from amounts already denominated in a currency", () => {
    const s = summarizeCost([
      cost("llm_tokens", 50_000, "tokens"),
      cost("other", 4.5, "USD"),
      cost("other", 0.5, "USD"),
    ]);

    // 50,000 tokens are on their own line and contribute nothing to the money
    // total — the price of a token is not known here, and guessing one is how a
    // tile ends up quoting a cost nobody was charged.
    expect(s.money).toEqual({ currency: "USD", amount: 5 });
    expect(s.lines).toHaveLength(2);
  });

  it("refuses a single money total when two currencies appear", () => {
    const s = summarizeCost([cost("other", 10, "USD"), cost("other", 10, "GBP")]);

    // There is no exchange rate in this system, and inventing one would put a
    // fabricated number in front of a client.
    expect(s.money).toBeNull();
    expect(s.lines.map((l) => l.unit).sort()).toEqual(["GBP", "USD"]);
  });

  it("divides money by engaged visitors, and not by zero of them", () => {
    const events = [cost("other", 20, "USD")];

    expect(summarizeCost(events, { engagedVisitors: 8 }).perEngagedVisit).toBe(2.5);
    expect(summarizeCost(events, { engagedVisitors: 0 }).perEngagedVisit).toBeNull();
    expect(summarizeCost(events).perEngagedVisit).toBeNull();
  });

  it("skips a reading with no usable amount instead of poisoning the sum", () => {
    const s = summarizeCost([
      cost("other", 5, "USD"),
      ev("cost.metered", { kind: "other", amount: "lots", unit: "USD" }),
      ev("cost.metered", { kind: "other", amount: 3 }), // no unit
    ]);

    // NaN would propagate through every total it touched and render as "—",
    // which on a cost tile is indistinguishable from having spent nothing.
    expect(s.money).toEqual({ currency: "USD", amount: 5 });
    expect(s.lines).toHaveLength(1);
  });

  it("ignores every other event type", () => {
    const s = summarizeCost([
      ev("spatial.dwell", { anonId: "P-1", zoneId: "z_entry", durationSec: 40 }),
      ev("rule.fired", { ruleId: "r_1", action: "slack" }),
    ]);
    expect(s.metered).toBe(false);
  });
});
