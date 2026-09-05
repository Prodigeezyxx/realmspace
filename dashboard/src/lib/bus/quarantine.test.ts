/**
 * The browser's dead-letter queue, as executable claims.
 *
 * Two of these are privacy properties rather than correctness ones, and they
 * are the reason this store is deliberately small: it lives in localStorage,
 * which the Article 17 erasure job cannot reach.
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
  clearRefusals,
  listRefusedSessions,
  readRefused,
  recordRefusal,
  subscribeRefusals,
  summariseRefusals,
  type RefusedEvent,
} from "./quarantine";

const T = "t_test";
const S = "s_1";

function refusal(over: Partial<RefusedEvent> = {}): RefusedEvent {
  return {
    eventId: "e-1",
    seq: 1,
    type: "spatial.dwell",
    reasons: ["zoneId is missing"],
    occurredAt: 1_754_215_200_000,
    refusedAt: 1_754_215_200_100,
    ...over,
  };
}

beforeEach(() => {
  window.localStorage.clear();
});

describe("recording a refusal", () => {
  it("keeps it, with its reasons", () => {
    recordRefusal(T, S, refusal());
    expect(readRefused(T, S)).toEqual([refusal()]);
  });

  it("is idempotent on eventId", () => {
    // A backfill overlaps the socket and a reconnect replays from a cursor, so
    // the same bad event arrives more than once. Counting it twice would
    // overstate how much of a client's activation went unread.
    recordRefusal(T, S, refusal());
    recordRefusal(T, S, refusal());
    expect(readRefused(T, S)).toHaveLength(1);
  });

  it("caps the list rather than filling the browser's storage", () => {
    // A producer sending the wrong dialect sends it for every event it writes;
    // the Phase 6 walk produced 319 in one run.
    for (let i = 0; i < 150; i++) {
      recordRefusal(T, S, refusal({ eventId: `e-${i}`, seq: i }));
    }
    const kept = readRefused(T, S);
    expect(kept).toHaveLength(100);
    // The newest are the ones worth having: an operator is looking at what is
    // happening now, and the oldest are the same sentence again.
    expect(kept[kept.length - 1].eventId).toBe("e-149");
  });

  it("partitions by tenant and session", () => {
    recordRefusal(T, S, refusal());
    expect(readRefused("t_other", S)).toEqual([]);
    expect(readRefused(T, "s_other")).toEqual([]);
  });

  it("notifies subscribers", () => {
    let calls = 0;
    const off = subscribeRefusals(() => calls++);
    recordRefusal(T, S, refusal());
    expect(calls).toBe(1);
    off();
    recordRefusal(T, S, refusal({ eventId: "e-2" }));
    expect(calls).toBe(1);
  });
});

describe("what is never written", () => {
  it("stores no payload anywhere in the record", () => {
    // The refusal names the *field*, never the value. `consent.captured` is
    // PII-classified, and a payload written here would be a copy of somebody's
    // name in a place no withdrawal can reach.
    recordRefusal(T, S, refusal({ type: "consent.captured", reasons: ["anonId is missing"] }));
    const raw = window.localStorage.getItem(`rs:quarantine:${T}::${S}`) ?? "";
    expect(raw).not.toContain("payload");
    expect(Object.keys(readRefused(T, S)[0]).sort()).toEqual([
      "eventId",
      "occurredAt",
      "reasons",
      "refusedAt",
      "seq",
      "type",
    ]);
  });
});

describe("reading it back", () => {
  it("summarises by type, commonest first", () => {
    recordRefusal(T, S, refusal({ eventId: "a" }));
    recordRefusal(T, S, refusal({ eventId: "b" }));
    recordRefusal(T, S, refusal({ eventId: "c", type: "spatial.zone_enter" }));
    expect(summariseRefusals(T, S)).toEqual({
      count: 3,
      byType: [
        { type: "spatial.dwell", count: 2 },
        { type: "spatial.zone_enter", count: 1 },
      ],
    });
  });

  it("says nothing when nothing was refused", () => {
    expect(summariseRefusals(T, S)).toEqual({ count: 0, byType: [] });
  });

  it("lists one tenant's activations and not another's", () => {
    recordRefusal(T, "s_a", refusal({ eventId: "a" }));
    recordRefusal(T, "s_b", refusal({ eventId: "b", refusedAt: 2 }));
    recordRefusal("t_other", "s_c", refusal({ eventId: "c" }));

    const listed = listRefusedSessions(T).map((s) => s.sessionId);
    expect(listed.sort()).toEqual(["s_a", "s_b"]);
    expect(listRefusedSessions("t_other").map((s) => s.sessionId)).toEqual(["s_c"]);
  });

  it("forgets a partition on request", () => {
    recordRefusal(T, S, refusal());
    clearRefusals(T, S);
    expect(readRefused(T, S)).toEqual([]);
  });
});
