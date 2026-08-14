/**
 * The ledger client, as executable claims.
 *
 * The arithmetic is the backend's — `app/attribution/ledger.py` — and asserting
 * it again here would be the second implementation this file exists to avoid.
 * What is only true on this side is that the page cannot invent a ratio, and
 * that the CSV is fetched with a bearer token rather than linked to.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchLedger } from "./useLedger";
import { benchmarkVerdict, roiRatio } from "@/lib/roi/scorecard";

const LEDGER = {
  session_id: "s_1",
  attribution_model: "influenced",
  attribution_window_days: 90,
  model_supported: true,
  model_note: null,
  activation_cost: 1000,
  revenue_influenced_stated: 99,
  truncated: false,
  rows: [],
  totals: {
    leads: 0,
    withdrawn: 0,
    orphan_outcomes: 0,
    outcomes: 0,
    outcomes_in_window: 0,
    influenced_leads: 0,
    revenue_influenced: null,
    currency: null,
    mixed_currencies: [],
  },
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

beforeEach(() => {
  process.env.NEXT_PUBLIC_BUS_URL = "http://bus.test";
  window.localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  delete process.env.NEXT_PUBLIC_BUS_URL;
});

describe("reading the ledger", () => {
  it("says there is nothing to read without a backend, and why", () => {
    delete process.env.NEXT_PUBLIC_BUS_URL;

    return fetchLedger("s_1").then((state) => {
      expect(state.status).toBe("offline");
      // Not a generic "offline": the ledger is built from the durable log, which
      // only the backend has, and an operator should know that rather than
      // wonder whether the page is broken.
      expect(state.detail).toMatch(/durable log/);
    });
  });

  it("reads the ledger", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        url.includes("/auth/")
          ? TOKEN
          : { ok: true, status: 200, json: async () => LEDGER }
      )
    );

    const state = await fetchLedger("s_1");

    expect(state.status).toBe("ready");
    expect(state.ledger?.attribution_window_days).toBe(90);
  });

  it("reports an unreachable bus rather than an empty ledger", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.includes("/auth/")) return TOKEN;
        throw new Error("network down");
      })
    );

    const state = await fetchLedger("s_1");

    // "No leads yet" and "we could not ask" must never render the same way on a
    // page a finance team is reading.
    expect(state.status).toBe("error");
    expect(state.ledger).toBeNull();
  });
});

describe("the headline figure", () => {
  it("is null whenever either side is missing", () => {
    // A ratio computed against an unknown cost is not a conservative estimate.
    expect(roiRatio(5000, null)).toBeNull();
    expect(roiRatio(null, 1000)).toBeNull();
    expect(roiRatio(5000, 0)).toBeNull();
  });

  it("matches the benchmark bands roi-framework states", () => {
    expect(benchmarkVerdict(roiRatio(6000, 1000))).toBe("exceptional"); // 5.0×
    expect(benchmarkVerdict(roiRatio(4000, 1000))).toBe("strong"); // 3.0×
    expect(benchmarkVerdict(roiRatio(3000, 1000))).toBe("below"); // 2.0×
    expect(benchmarkVerdict(null)).toBe("unknown");
  });

  it("is the same function the report uses", () => {
    // Imported from @/lib/roi/scorecard rather than reimplemented here — the
    // report and the one-pager must never be able to disagree about the number
    // a CFO reads first.
    expect(typeof roiRatio).toBe("function");
    expect(roiRatio(5000, 1000)).toBe(4);
  });
});
