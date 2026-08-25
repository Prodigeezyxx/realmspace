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
  connectLiveFeed,
  ensureTenantId,
  ensureToken,
  flushOutbound,
  getRemoteSeq,
  mirror,
  signUpOrganisation,
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

  it("resolves the tenant only after the backend has verified it", async () => {
    // The bug this exists for was invisible on the default tenant, which is the
    // only tenant anybody develops against.
    //
    // `getTenantId()` answers with a guess — the default, or whatever this
    // browser last stored — until a token exchange lands. Every caller that
    // read it at the top of an effect got that guess, so on a first load for
    // any other organisation the backfill compared each incoming event against
    // the wrong tenant, dropped all of them, and the report rendered "the log
    // is genuinely empty" over an activation full of visitors. A reload fixed
    // it, which is how it survived to production.
    const { getTenantId, setTenantId, DEFAULT_TENANT_ID } = await import(
      "@/lib/tenant/context"
    );
    setTenantId(DEFAULT_TENANT_ID);

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(
        () =>
          new Promise((resolve) =>
            setTimeout(
              () =>
                resolve({
                  ok: true,
                  status: 200,
                  json: async () => ({
                    accessToken: "jwt",
                    expiresIn: 3600,
                    tenantId: "t_other_org",
                  }),
                }),
              0
            )
          )
      )
    );

    // Read synchronously, this is still the guess — which is the whole point.
    expect(getTenantId()).toBe(DEFAULT_TENANT_ID);

    await expect(ensureTenantId("op@other.example")).resolves.toBe("t_other_org");
  });

  it("notifies subscribers when the verified tenant replaces the guess", async () => {
    // What the hooks depend on: an effect keyed on the tenant has to re-run
    // when the real one arrives, or it stays pinned to the guess for the life
    // of the component.
    const { setTenantId, subscribeTenantId, DEFAULT_TENANT_ID } = await import(
      "@/lib/tenant/context"
    );
    setTenantId(DEFAULT_TENANT_ID);

    let notified = 0;
    const unsubscribe = subscribeTenantId(() => notified++);

    setTenantId("t_other_org");
    expect(notified).toBe(1);

    // A token refresh an hour later carries the same tenant. Notifying again
    // would re-run every subscribed effect for a value that did not change.
    setTenantId("t_other_org");
    expect(notified).toBe(1);

    unsubscribe();
    setTenantId(DEFAULT_TENANT_ID);
    expect(notified).toBe(1);
  });

  it("opens the socket against the verified tenant, not the captured one", async () => {
    // The caller hands over whatever the tenant was when it rendered. If that
    // was the default — a first load, before any token — the socket used to be
    // opened against it *after* the token exchange had already established the
    // real one, and the backend refused the pair with a 403 the operator reads
    // as BUS DOWN.
    const { setTenantId, DEFAULT_TENANT_ID } = await import("@/lib/tenant/context");
    setTenantId(DEFAULT_TENANT_ID);

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({
          accessToken: "jwt",
          expiresIn: 3600,
          tenantId: "t_other_org",
        }),
      })
    );

    const urls: string[] = [];
    const close = connectLiveFeed({
      tenantId: DEFAULT_TENANT_ID,
      sessionId: S,
      email: "op@other.example",
      socketFactory: (url) => {
        urls.push(url);
        return { close() {} } as unknown as WebSocket;
      },
    });

    await vi.waitFor(() => expect(urls).toHaveLength(1));
    expect(urls[0]).toContain("/v1/ws/t_other_org/");
    expect(urls[0]).not.toContain("/v1/ws/t_floats/");
    close();
  });

  it("does not open a socket whose subscriber has already gone away", async () => {
    // The token exchange is an await, and an effect that re-runs during it
    // unsubscribes this attempt and starts another. Without a second check
    // after the await, both sockets open: the newer one connects and the older
    // one — already cleaned up — opens a moment later against whatever it
    // captured, which is the rejected connection that appeared in the server
    // log after an accepted one on every cold load.
    let releaseToken: (value: unknown) => void = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(
        () =>
          new Promise((resolve) => {
            releaseToken = () =>
              resolve({
                ok: true,
                status: 200,
                json: async () => ({
                  accessToken: "jwt",
                  expiresIn: 3600,
                  tenantId: T,
                }),
              });
          })
      )
    );

    let opened = 0;
    const close = connectLiveFeed({
      tenantId: T,
      sessionId: S,
      email: "op@floats.demo",
      socketFactory: () => {
        opened++;
        return { close() {} } as unknown as WebSocket;
      },
    });

    // Unsubscribe while the token is still in flight, then let it land.
    close();
    releaseToken(null);
    await Promise.resolve();
    await Promise.resolve();

    expect(opened).toBe(0);
    // The token minted while this test's exchange was in flight would otherwise
    // satisfy the next test's `ensureToken` and its fetch would never be made.
    clearToken();
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

describe("who this browser authenticates as", () => {
  /**
   * The bug this covers made the Phase 6 acceptance criterion unverifiable: a
   * third-party operator who self-served a signup was silently re-exchanged
   * into the seeded demo organisation on the first page reload, because the
   * token lived in a module variable and every caller but the login form asks
   * for `busEmail()` — the environment default.
   */
  const ENV_EMAIL = "admin@floats.demo";

  function tokenFor(tenantId: string) {
    return vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ accessToken: "jwt", expiresIn: 3600, tenantId }),
    });
  }

  function sentEmail(fetchMock: ReturnType<typeof vi.fn>, call = 0): string {
    return JSON.parse(fetchMock.mock.calls[call][1].body).email;
  }

  it("re-authenticates as the person who signed up, not the environment", async () => {
    const signup = tokenFor("t_new_org");
    vi.stubGlobal("fetch", signup);
    await signUpOrganisation("founder@newco.test", "NewCo");

    expect(window.localStorage.getItem("rs:busIdentity")).toBe(
      "founder@newco.test"
    );

    // Model a reload: the module's cached token is gone, localStorage is not.
    // `clearToken` is sign-out and forgets both deliberately, so the identity is
    // put back — that is the half a reload keeps.
    clearToken();
    window.localStorage.setItem("rs:busIdentity", "founder@newco.test");

    // Every screen but the login form asks with the environment's address,
    // because none of them has an opinion about who is using the browser.
    const refresh = tokenFor("t_new_org");
    vi.stubGlobal("fetch", refresh);
    await ensureToken(ENV_EMAIL);

    expect(sentEmail(refresh)).toBe("founder@newco.test");
  });

  it("lets somebody sign in as a different person on the same browser", async () => {
    window.localStorage.setItem("rs:busIdentity", "founder@newco.test");
    const fetchMock = tokenFor("t_other");
    vi.stubGlobal("fetch", fetchMock);

    // The login form names an address. That is a statement of identity and it
    // has to beat what the browser remembers, or nobody can ever switch.
    await ensureToken("someone.else@other.test");

    expect(sentEmail(fetchMock)).toBe("someone.else@other.test");
  });

  it("falls back to the environment when nobody has ever signed in", async () => {
    const fetchMock = tokenFor(T);
    vi.stubGlobal("fetch", fetchMock);

    await ensureToken(ENV_EMAIL);

    expect(sentEmail(fetchMock)).toBe(ENV_EMAIL);
  });

  it("forgets the identity on sign-out", async () => {
    const fetchMock = tokenFor("t_new_org");
    vi.stubGlobal("fetch", fetchMock);
    await ensureToken("founder@newco.test");

    // Otherwise the next person to use this browser is the last one.
    clearToken();

    expect(window.localStorage.getItem("rs:busIdentity")).toBeNull();
  });
});
