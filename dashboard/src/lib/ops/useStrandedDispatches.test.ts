/**
 * The unknown-outcome queue, as executable claims.
 *
 * Driven through `fetchStranded` / `postVerdict` rather than by rendering the
 * hook — the same split `useLiveStats.test.ts` makes, and for the same reason:
 * the hook owns subscription and lifecycle, and rendering it would test React's
 * effect timing rather than what the browser does with the bus's answers.
 *
 * What is worth asserting is mostly restraint. There is no way to re-send from
 * here, and the wording an operator reads after a verdict comes from the backend
 * rather than from a copy in this file. Both are properties a well-meaning
 * refactor would quietly remove.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as strandedModule from "./useStrandedDispatches";
import { fetchStranded, postVerdict } from "./useStrandedDispatches";

const STRANDED = {
  id: 7,
  kind: "rule",
  ruleId: "r_entry_crowd",
  ruleName: "Entrance crowding → ping ops",
  actionType: "slack",
  attempts: 1,
  createdAt: "2026-08-13T10:00:00Z",
  strandedForSeconds: 305,
};

const TOKEN = {
  ok: true,
  status: 200,
  json: async () => ({
    accessToken: "jwt-abc",
    expiresIn: 3600,
    tenantId: "t_test",
  }),
};

/** Answers the token exchange, then whatever the test set up for the real call. */
function bus(reply: (url: string) => unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => (url.includes("/auth/") ? TOKEN : reply(url)))
  );
}

beforeEach(() => {
  process.env.NEXT_PUBLIC_BUS_URL = "http://bus.test";
  window.localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  delete process.env.NEXT_PUBLIC_BUS_URL;
});

describe("reading the queue", () => {
  it("says there is nothing to read when no backend is configured", async () => {
    delete process.env.NEXT_PUBLIC_BUS_URL;

    const state = await fetchStranded();

    // Not an error: the laptop demo has no backend, and "nothing behind this"
    // is an answer rather than a failure to fetch.
    expect(state.status).toBe("offline");
    expect(state.detail).toMatch(/No backend configured/);
  });

  it("reads the stranded dispatches", async () => {
    bus(() => ({ ok: true, status: 200, json: async () => [STRANDED] }));

    const state = await fetchStranded();

    expect(state.status).toBe("ready");
    expect(state.items).toHaveLength(1);
    expect(state.items[0].ruleName).toBe("Entrance crowding → ping ops");
  });

  it("keeps a stranded lead distinguishable from a stranded rule action", async () => {
    // A handoff's `ruleId` holds a session id and it has no rule name, so `kind`
    // is the only thing on the row saying which of the two an operator is
    // looking at — and they are very different urgencies.
    const lead = {
      ...STRANDED,
      id: 8,
      kind: "handoff",
      ruleId: "s_pavilion_7",
      ruleName: null,
      actionType: "handoff_webhook",
    };
    bus(() => ({ ok: true, status: 200, json: async () => [STRANDED, lead] }));

    const state = await fetchStranded();

    expect(state.items.map((i) => i.kind)).toEqual(["rule", "handoff"]);
  });

  it("reports an unreachable bus as unreachable, not as an empty queue", async () => {
    bus(() => {
      throw new Error("network down");
    });

    const state = await fetchStranded();

    // The distinction the whole panel rests on: "nothing is stranded" and "we
    // could not ask" must never render the same way.
    expect(state.status).toBe("error");
    expect(state.items).toEqual([]);
  });
});

describe("recording a verdict", () => {
  it("returns the backend's own description of what the verdict did", async () => {
    bus(() => ({
      ok: true,
      status: 200,
      json: async () => ({
        id: 7,
        status: "failed",
        resolvedBy: "u_test",
        effect: "Released. Nothing has been re-sent.",
      }),
    }));

    const outcome = await postVerdict(7, "failed", "nothing in the channel");

    // Not a string this file owns. "Released" is a claim about the dispatcher's
    // state machine, and a second copy of it in the browser goes stale the first
    // time the backend's reasoning changes.
    expect(outcome).toEqual({ effect: "Released. Nothing has been re-sent." });
  });

  it("sends the note the operator typed", async () => {
    // Typed through the generic rather than by declaring an unused `init`
    // parameter, so the recorded calls carry the RequestInit this test reads
    // back out of them.
    const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<unknown>>(
      async (url) =>
        url.includes("/auth/")
          ? TOKEN
          : { ok: true, status: 200, json: async () => ({ effect: "Closed." }) }
    );
    vi.stubGlobal("fetch", fetchMock);

    await postVerdict(7, "delivered", "found it in #ops at 14:32");

    const call = fetchMock.mock.calls.find(([url]) => url.includes("/resolve"));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      verdict: "delivered",
      note: "found it in #ops at 14:32",
    });
  });

  it("surfaces a refusal with the backend's reason", async () => {
    const detail =
      "dispatch 7 is already 'delivered', which the dispatcher recorded from the call itself.";
    bus(() => ({ ok: false, status: 409, json: async () => ({ detail }) }));

    const outcome = await postVerdict(7, "failed");

    // A 409 here is not a bug to hide — it is the system declining to let a
    // recollection overwrite something it observed, and the operator should
    // read exactly that.
    expect(outcome).toEqual({ error: detail });
  });

  it("offers no way to re-send", () => {
    // The guard on the whole feature. A retry here is the double-post that
    // `rule_dispatch` exists to prevent, so the absence is the design.
    expect(Object.keys(strandedModule)).toEqual(
      expect.not.arrayContaining(["retry", "resend", "redeliver"])
    );
  });
});
