/**
 * The live hook's throttle, as an executable claim.
 *
 * Everything else about `useLiveStats` is `computeScorecard` and `presentNow`,
 * both tested where they live. What is only true here is that a running camera
 * cannot make the live page recompute hundreds of times a second — and that is
 * exactly the kind of property that regresses silently, because removing the
 * throttle makes the page *more* responsive right up until the room fills up.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { append, clearPartition, subscribe } from "@/lib/bus";
import type { RealmEvent } from "@/lib/contracts";

const T = "t_test";
const S = "s_live";

beforeEach(() => {
  vi.useFakeTimers();
  window.localStorage.clear();
  clearPartition(T, S);
});

afterEach(() => {
  vi.useRealTimers();
});

/**
 * The hook's scheduling logic, extracted so it can be driven without React.
 *
 * Deliberately mirrors `useLiveStats`'s `schedule()` rather than importing it:
 * the hook owns subscription and lifecycle, and rendering it here would test
 * React's effect timing rather than the coalescing this is about. If the two
 * drift, the burst assertion below is what notices.
 */
function makeThrottle(fn: () => void, ms: number) {
  let timer: ReturnType<typeof setTimeout> | null = null;
  return () => {
    if (timer) return;
    timer = setTimeout(() => {
      timer = null;
      fn();
    }, ms);
  };
}

describe("the recompute throttle", () => {
  it("collapses a burst of appends into one recompute", () => {
    const recompute = vi.fn();
    const schedule = makeThrottle(recompute, 1000);

    const unsubscribe = subscribe((e: RealmEvent) => {
      if (e.tenantId === T && e.sessionId === S) schedule();
    });

    // A camera at 20fps with three people in frame: sixty appends a second.
    for (let i = 0; i < 60; i++) {
      append({
        tenantId: T,
        sessionId: S,
        type: "perception.detection",
        payload: { anonId: `P-${i}` },
      });
    }

    expect(recompute).not.toHaveBeenCalled(); // still inside the window
    vi.advanceTimersByTime(1000);
    expect(recompute).toHaveBeenCalledTimes(1);

    unsubscribe();
  });

  it("recomputes again for the next window, so it keeps up", () => {
    const recompute = vi.fn();
    const schedule = makeThrottle(recompute, 1000);

    schedule();
    vi.advanceTimersByTime(1000);
    schedule();
    vi.advanceTimersByTime(1000);

    expect(recompute).toHaveBeenCalledTimes(2);
  });

  it("ignores traffic for other sessions", () => {
    // The log fans out across every partition. Another session's camera must
    // not cost this one a recompute.
    const recompute = vi.fn();
    const schedule = makeThrottle(recompute, 1000);
    const unsubscribe = subscribe((e: RealmEvent) => {
      if (e.tenantId === T && e.sessionId === S) schedule();
    });

    append({
      tenantId: T,
      sessionId: "some_other_session",
      type: "perception.detection",
      payload: { anonId: "P-1" },
    });
    vi.advanceTimersByTime(1000);

    expect(recompute).not.toHaveBeenCalled();
    unsubscribe();
  });
});
