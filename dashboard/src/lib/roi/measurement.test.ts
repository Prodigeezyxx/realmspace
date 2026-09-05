import { describe, expect, it } from "vitest";

import { resolveMeasurement } from "./measurement";
import type { RemoteSessionConfig } from "@/lib/session/publish";

const remote = (over: Partial<RemoteSessionConfig>): RemoteSessionConfig =>
  ({
    sessionId: "s_1",
    engagedThresholdSeconds: 60,
    activationCost: null,
    currency: "USD",
    attributionModel: "influenced",
    revenueInfluenced: null,
    qualifiedLeads: null,
    zones: [],
    touchpoints: [],
    ...over,
  }) as RemoteSessionConfig;

describe("resolveMeasurement", () => {
  it("prefers the backend, which is the authority", () => {
    // `useSessionReport` has always said why: an operator may have corrected
    // the cost from another machine, and the report must divide by what was
    // actually agreed rather than by whatever this laptop last saw.
    const out = resolveMeasurement(remote({ activationCost: 18000 }), {
      activationCost: 1000,
    });
    expect(out.activationCost).toBe(18000);
  });

  it("falls through field by field, not object by object", () => {
    // An activation configured before the settings screen existed has a cost in
    // this browser and none stored. Taking the remote object wholesale would
    // lose it and the report would say the cost is unset.
    const out = resolveMeasurement(remote({}), {
      activationCost: 1000,
      revenueInfluenced: 50000,
    });
    expect(out.activationCost).toBe(1000);
    expect(out.revenueInfluenced).toBe(50000);
  });

  it("uses the local session when there is no backend", () => {
    const out = resolveMeasurement(null, { activationCost: 250, currency: "GBP" });
    expect(out.activationCost).toBe(250);
    expect(out.currency).toBe("GBP");
  });

  it("leaves an unset figure unset rather than making it zero", () => {
    // The distinction the whole scorecard is built on: "we don't know" and
    // "zero" are different answers.
    const out = resolveMeasurement(remote({}), {});
    expect(out.activationCost).toBeUndefined();
    expect(out.revenueInfluenced).toBeUndefined();
  });
});
