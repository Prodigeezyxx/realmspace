/**
 * When a brand-new organisation is shown a first-run screen instead of somebody
 * else's activation — and, more importantly, when it is not.
 *
 * The dangerous direction here is the false positive. Telling an established
 * client "this organisation has not run an activation" because a read failed is
 * the same shape of mistake as reporting a broken pipeline as a quiet day, and
 * it would greet somebody with an empty product on the morning of an event.
 */

import { describe, expect, it } from "vitest";

import { firstRunStatus, remoteAnswer } from "./useFirstRun";

describe("what the backend's answer means", () => {
  it("treats an empty list as 'this client has run nothing'", () => {
    expect(remoteAnswer([])).toBe("none");
  });

  it("treats null as 'we could not tell', never as none", () => {
    // `fetchSessionList` returns null for no backend, no token, and a failed
    // read. All three are unknown, and unknown must not gate.
    expect(remoteAnswer(null)).toBe("unknown");
  });

  it("treats any activation as established", () => {
    expect(remoteAnswer([{}])).toBe("some");
  });
});

describe("the decision", () => {
  it("shows the first-run screen only when the backend confirms it", () => {
    expect(
      firstRunStatus({ remoteBus: true, ownSessions: 0, remote: "none" })
    ).toBe("first-run");
  });

  it("never gates when the read failed", () => {
    // The operator on a second laptop, or one whose wifi dropped. They have
    // activations; this browser has just never seen them.
    expect(
      firstRunStatus({ remoteBus: true, ownSessions: 0, remote: "unknown" })
    ).toBe("checking");
  });

  it("never gates a deployment with no backend", () => {
    // The laptop demo, whose whole point is the seeded activation. Nothing
    // here runs for it.
    expect(
      firstRunStatus({ remoteBus: false, ownSessions: 0, remote: "none" })
    ).toBe("established");
  });

  it("stops the moment this browser knows of an activation", () => {
    // The store answers it without a round trip — the case immediately after
    // finishing the wizard, where waiting on the backend would flash the
    // first-run screen at somebody who has just created something.
    expect(
      firstRunStatus({ remoteBus: true, ownSessions: 1, remote: "unknown" })
    ).toBe("established");
  });

  it("stops when the backend reports activations this browser has not seen", () => {
    expect(
      firstRunStatus({ remoteBus: true, ownSessions: 0, remote: "some" })
    ).toBe("established");
  });
});
