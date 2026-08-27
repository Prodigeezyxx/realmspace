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

import { beforeEach, describe, expect, it } from "vitest";

import {
  clearPartition,
  headSeq,
  OUTBOUND_CURSOR,
  readAll,
  setCursor,
} from "./log";

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
