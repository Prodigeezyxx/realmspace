/**
 * The bridge, as executable claims.
 *
 * The two that matter are the idempotent mirror and the ordered outbound flush.
 * Both are the browser's half of the property `perception/bus_client.py` already
 * proves on the Python side: an event is identified when it happens, not when it
 * is sent, so an outage costs nothing and a reconnect duplicates nothing.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { append, clearPartition, readAll, getCursor } from "./log";
import {
  clearToken,
  ensureToken,
  flushOutbound,
  getRemoteSeq,
  mirror,
} from "./remote";
import type { WireEvent } from "./wire";

const T = "t_test";
const S = "s_1";

function wireEvent(over: Partial<WireEvent> = {}): WireEvent {
  return {
    seq: 1,
    eventId: "e-1",
    tenantId: T,
    sessionId: S,
    type: "spatial.zone_enter",
    payload: { anon_id: "P-1", zone_id: "z_a" },
    occurredAt: 1_754_215_200_000,
    recordedAt: 1_754_215_200_000,
    ...over,
  };
}

beforeEach(() => {
  process.env.NEXT_PUBLIC_BUS_URL = "http://bus.test";
  window.localStorage.clear();
  clearPartition(T, S);
  clearToken();
});

afterEach(() => {
  vi.restoreAllMocks();
  delete process.env.NEXT_PUBLIC_BUS_URL;
});

async function authenticate() {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        accessToken: "jwt-abc",
        expiresIn: 3600,
        tenantId: T,
      }),
    })
  );
  await ensureToken("op@floats.demo");
  vi.unstubAllGlobals();
}

describe("mirror", () => {
  it("appends a remote event into the local log, translated", () => {
    mirror(wireEvent());

    const [event] = readAll(T, S);
    expect(event.eventId).toBe("e-1");
    expect(event.payload).toEqual({ anonId: "P-1", zoneId: "z_a" });
  });

  it("is idempotent — the same event twice is one row", () => {
    mirror(wireEvent());
    mirror(wireEvent());
    expect(readAll(T, S)).toHaveLength(1);
  });

  it("dedupes an event this browser produced against the echo of it", () => {
    // The exact reconnect case: emit locally, the backend stores it, the socket
    // sends it back. Without the shared eventId this double-counts every event
    // the dashboard ever produces.
    append({
      eventId: "e-local",
      tenantId: T,
      sessionId: S,
      type: "surface.interaction",
      payload: { anonId: "P-1", surfaceId: "sf_1", kind: "tap" },
    });
    mirror(
      wireEvent({
        eventId: "e-local",
        type: "surface.interaction",
        payload: { anon_id: "P-1", surface_id: "sf_1", kind: "tap" },
      })
    );

    expect(readAll(T, S)).toHaveLength(1);
  });

  it("advances the remote cursor, so a reconnect asks for the right window", () => {
    mirror(wireEvent({ seq: 7, eventId: "e-7" }));
    expect(getRemoteSeq(T, S)).toBe(7);
  });

  it("never moves the remote cursor backwards", () => {
    // A capped replay window can legitimately re-send older events. Rewinding
    // here would re-request them on every reconnect, forever.
    mirror(wireEvent({ seq: 7, eventId: "e-7" }));
    mirror(wireEvent({ seq: 3, eventId: "e-3" }));
    expect(getRemoteSeq(T, S)).toBe(7);
  });
});

describe("flushOutbound", () => {
  beforeEach(authenticate);

  it("posts everything since the cursor and advances it", async () => {
    append({ tenantId: T, sessionId: S, type: "session.started", payload: {} });
    append({ tenantId: T, sessionId: S, type: "session.ended", payload: {} });

    const fetchMock = vi.fn().mockResolvedValue({ status: 201 });
    vi.stubGlobal("fetch", fetchMock);

    expect(await flushOutbound(T, S)).toBe(2);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(getCursor("remote-bus", T, S)).toBe(2);

    // Nothing new to send the second time.
    expect(await flushOutbound(T, S)).toBe(0);
  });

  it("treats 200 as success, because 200 means the bus already had it", async () => {
    append({ tenantId: T, sessionId: S, type: "session.started", payload: {} });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ status: 200 }));

    expect(await flushOutbound(T, S)).toBe(1);
    expect(getCursor("remote-bus", T, S)).toBe(1);
  });

  it("stops at the first failure and keeps the rest in order", async () => {
    append({ tenantId: T, sessionId: S, type: "session.started", payload: {} });
    append({ tenantId: T, sessionId: S, type: "session.ended", payload: {} });
    append({ tenantId: T, sessionId: S, type: "cost.metered", payload: {} });

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ status: 201 })
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValue({ status: 201 });
    vi.stubGlobal("fetch", fetchMock);

    expect(await flushOutbound(T, S)).toBe(1);
    // The cursor stayed on the one that landed. Advancing past the failure
    // would drop event 2 permanently — the failure mode this design exists
    // to prevent.
    expect(getCursor("remote-bus", T, S)).toBe(1);

    // Backend is back: the rest go, oldest first.
    const resend = vi.fn().mockResolvedValue({ status: 201 });
    vi.stubGlobal("fetch", resend);
    expect(await flushOutbound(T, S)).toBe(2);
    expect(getCursor("remote-bus", T, S)).toBe(3);

    const sentTypes = resend.mock.calls.map(
      (c) => JSON.parse(String((c[1] as RequestInit).body)).type
    );
    expect(sentTypes).toEqual(["session.ended", "cost.metered"]);
  });

  it("sends the translated payload, not the browser's shape", async () => {
    append({
      tenantId: T,
      sessionId: S,
      type: "spatial.dwell",
      payload: { anonId: "P-1", zoneId: "z_a", durationSec: 45 },
    });
    const fetchMock = vi.fn().mockResolvedValue({ status: 201 });
    vi.stubGlobal("fetch", fetchMock);

    await flushOutbound(T, S);

    const body = JSON.parse(String((fetchMock.mock.calls[0][1] as RequestInit).body));
    expect(body.payload).toEqual({
      anon_id: "P-1",
      zone_id: "z_a",
      duration: 45,
    });
  });
});

describe("ensureToken", () => {
  it("adopts the tenant the backend verified, rather than asserting one", async () => {
    const { getTenantId } = await import("@/lib/tenant/context");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({
          accessToken: "jwt",
          expiresIn: 3600,
          tenantId: "t_from_token",
        }),
      })
    );

    await ensureToken("op@floats.demo");
    expect(getTenantId()).toBe("t_from_token");
  });

  it("collapses concurrent callers onto one exchange", async () => {
    // The socket and the first post ask at the same instant. Two exchanges
    // would mean the second token silently replacing the first mid-connection.
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ accessToken: "jwt", expiresIn: 3600, tenantId: T }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await Promise.all([
      ensureToken("op@floats.demo"),
      ensureToken("op@floats.demo"),
      ensureToken("op@floats.demo"),
    ]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
