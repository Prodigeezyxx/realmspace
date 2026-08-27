/**
 * The durable log's format sweep.
 *
 * Everything else in `log.ts` is exercised through `remote.test.ts` and the
 * screens; this file is about the one operation that *removes* events, which
 * deserves its own claims.
 *
 * The sweep exists because the boundary check added in `contracts/validate.ts`
 * cannot reach backwards. An unreadable event already in this browser's
 * localStorage dedupes on `eventId` and is never re-mirrored, so a report opened
 * on a laptop that saw the bad run would keep computing from it forever.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  append,
  appendMany,
  clearPartition,
  headSeq,
  OUTBOUND_CURSOR,
  readAll,
  setCursor,
  subscribe,
} from "./log";
import type { RealmEventInput } from "@/lib/contracts";

const T = "t_test";

/** A partition written by a build that predates the format version. */
function seedLegacyPartition(sessionId: string, count: number) {
  const events = Array.from({ length: count }, (_, i) => ({
    seq: i + 1,
    eventId: `e-${i + 1}`,
    tenantId: T,
    sessionId,
    type: "spatial.zone_enter",
    payload: { anonId: `P-${i + 1}`, zoneId: "z_a" },
    occurredAt: 1_754_215_200_000 + i,
    recordedAt: 1_754_215_200_000 + i,
  }));
  clearPartition(T, sessionId); // no stale in-memory copy from an earlier test
  window.localStorage.setItem(
    `rs:eventlog:${T}::${sessionId}`,
    JSON.stringify(events)
  );
}

beforeEach(() => {
  window.localStorage.clear();
  // The in-memory partition cache outlives localStorage.clear(), so a partition
  // written by one test would still be cached for the next.
  for (const id of ["s_batch", "s_one", "s_two", "s_mirrored", "s_pending", "s_later"]) {
    clearPartition(T, id);
  }
});

describe("sweeping a stale format", () => {
  it("drops a partition the backend can send again", () => {
    seedLegacyPartition("s_mirrored", 3);
    // Everything in it has been posted, so the server has a copy and
    // `backfillSession` will page it back — this time through the validation.
    setCursor(OUTBOUND_CURSOR, T, "s_mirrored", 3);

    expect(readAll(T, "s_mirrored")).toEqual([]);
    expect(window.localStorage.getItem("rs:eventlog:format")).toBe("2");
  });

  it("keeps a partition holding events that exist nowhere else", () => {
    // `remote.ts`: there is no second outbox, the local log *is* the buffer.
    // An event emitted while the backend was down is here and only here, and
    // clearing it is data loss dressed as a cleanup.
    seedLegacyPartition("s_pending", 3);
    setCursor(OUTBOUND_CURSOR, T, "s_pending", 1); // two still unsent

    expect(readAll(T, "s_pending")).toHaveLength(3);
    expect(headSeq(T, "s_pending")).toBe(3);
  });

  it("reconsiders a skipped partition on a later load", () => {
    // Stamping the version while a partition was skipped would mean it is never
    // swept, and the stale events outlive the reason for keeping them.
    seedLegacyPartition("s_pending", 3);
    setCursor(OUTBOUND_CURSOR, T, "s_pending", 1);
    readAll(T, "s_pending");

    expect(window.localStorage.getItem("rs:eventlog:format")).toBeNull();
  });

  it("does not sweep again once the version is stamped", () => {
    seedLegacyPartition("s_mirrored", 2);
    setCursor(OUTBOUND_CURSOR, T, "s_mirrored", 2);
    expect(readAll(T, "s_mirrored")).toEqual([]);

    // A partition written after the sweep is current by definition.
    seedLegacyPartition("s_later", 2);
    setCursor(OUTBOUND_CURSOR, T, "s_later", 2);
    expect(readAll(T, "s_later")).toHaveLength(2);
  });
});

describe("appending a batch", () => {
  /** One dwell, ready to append. */
  function input(over: Partial<RealmEventInput> = {}): RealmEventInput {
    return {
      eventId: "e-1",
      tenantId: T,
      sessionId: "s_batch",
      type: "spatial.dwell",
      payload: { anonId: "P-1", zoneId: "z_a", durationSec: 30 },
      occurredAt: 1_754_215_200_000,
      ...over,
    };
  }

  it("writes to storage once, not once per event", () => {
    // The whole point. `persist()` serialises the entire partition, so N
    // appends is N stringifications of an array growing to N — which is what
    // killed the vitest worker on every CI run from the day CI was added.
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    appendMany(
      Array.from({ length: 50 }, (_, i) => input({ eventId: `e-${i}` }))
    );

    const partitionWrites = setItem.mock.calls.filter(([k]) =>
      String(k).startsWith("rs:eventlog:t_test::s_batch")
    );
    expect(partitionWrites).toHaveLength(1);
    expect(readAll(T, "s_batch")).toHaveLength(50);
    setItem.mockRestore();
  });

  it("assigns seq in order, exactly as one-at-a-time appends would", () => {
    appendMany([input({ eventId: "a" }), input({ eventId: "b" }), input({ eventId: "c" })]);
    expect(readAll(T, "s_batch").map((e) => e.seq)).toEqual([1, 2, 3]);
    expect(headSeq(T, "s_batch")).toBe(3);
  });

  it("dedupes within the batch and against what is already logged", () => {
    // A backfill overlaps the socket, so a page can carry an event this browser
    // already has — the property that makes the two safe to run in any order.
    append(input({ eventId: "already" }));

    const written = appendMany([
      input({ eventId: "already" }),
      input({ eventId: "fresh" }),
      input({ eventId: "fresh" }),
    ]);

    expect(written.map((e) => e.eventId)).toEqual(["fresh"]);
    expect(readAll(T, "s_batch")).toHaveLength(2);
  });

  it("fans out once per genuinely new event", () => {
    const seen: string[] = [];
    const off = subscribe((e) => seen.push(e.eventId));
    append(input({ eventId: "already" }));
    appendMany([input({ eventId: "already" }), input({ eventId: "fresh" })]);
    off();

    expect(seen).toEqual(["already", "fresh"]);
  });

  it("keeps partitions apart when a batch spans sessions", () => {
    appendMany([
      input({ eventId: "a", sessionId: "s_one" }),
      input({ eventId: "b", sessionId: "s_two" }),
    ]);
    expect(readAll(T, "s_one").map((e) => e.eventId)).toEqual(["a"]);
    expect(readAll(T, "s_two").map((e) => e.eventId)).toEqual(["b"]);
  });

  it("writes nothing for an empty batch", () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    expect(appendMany([])).toEqual([]);
    expect(setItem).not.toHaveBeenCalled();
    setItem.mockRestore();
  });
});
