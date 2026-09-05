"use client";

import { useSyncExternalStore } from "react";

/**
 * The wall clock, as a value React is allowed to read.
 *
 * `Date.now()` called during render is impure: the number is captured once,
 * never updates, and differs between the server render and the client one. Two
 * places in this app were doing exactly that and both were visibly wrong rather
 * than merely improper — `/live`'s session duration and each track's lifespan
 * were computed at first paint and then sat there while the activation ran.
 *
 * An external store rather than `useState` + `setInterval`, for two reasons.
 * One clock ticks for every subscriber instead of one per component, so a live
 * page with forty track rows schedules one timer rather than forty. And the
 * server snapshot is a fixed zero, so nothing renders a time on the server that
 * the client immediately contradicts.
 *
 * Callers that want to display an elapsed time should ask for the coarsest
 * interval they can live with: this re-renders every subscriber on each tick.
 */

const stores = new Map<number, ClockStore>();

interface ClockStore {
  subscribe: (listener: () => void) => () => void;
  getSnapshot: () => number;
}

function clockFor(intervalMs: number): ClockStore {
  const existing = stores.get(intervalMs);
  if (existing) return existing;

  const listeners = new Set<() => void>();
  let timer: ReturnType<typeof setInterval> | null = null;
  let now = Date.now();

  const store: ClockStore = {
    subscribe(listener) {
      listeners.add(listener);
      if (timer === null) {
        timer = setInterval(() => {
          now = Date.now();
          for (const l of listeners) l();
        }, intervalMs);
      }
      return () => {
        listeners.delete(listener);
        // The last subscriber leaving stops the timer. Without this a page
        // that has been navigated away from keeps waking the tab forever.
        if (!listeners.size && timer !== null) {
          clearInterval(timer);
          timer = null;
        }
      };
    },
    // Read fresh on every call rather than returned from the cached tick: a
    // component mounting between ticks should not be handed a stale value, and
    // `useSyncExternalStore` compares snapshots so an unchanged one is free.
    getSnapshot: () => (timer === null ? Date.now() : now),
  };

  stores.set(intervalMs, store);
  return store;
}

/**
 * Milliseconds since the epoch, re-rendering every `intervalMs`.
 *
 * Returns 0 on the server, which is the honest answer for a render that has no
 * clock — callers showing an elapsed time get "0s" for the first paint rather
 * than a number the client then disagrees with.
 */
export function useNow(intervalMs = 1000): number {
  const store = clockFor(intervalMs);
  return useSyncExternalStore(store.subscribe, store.getSnapshot, () => 0);
}
