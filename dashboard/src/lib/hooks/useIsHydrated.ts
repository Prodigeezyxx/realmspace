"use client";

import { useSyncExternalStore } from "react";

/**
 * False during the server render and the hydrating one, true afterwards.
 *
 * The thing this replaces is `useState(false)` plus
 * `useEffect(() => setMounted(true), [])` — the standard mount guard, and a
 * setState inside an effect, which triggers a second render pass for every
 * component that uses it. This is the same guard with no state write: React
 * calls `getServerSnapshot` while rendering on the server and during hydration,
 * and `getSnapshot` on every render after.
 *
 * Used where a component genuinely cannot render on the server — Recharts
 * cannot measure a container that has no layout, and renders at width(-1)
 * height(-1) with a console warning if asked to try.
 */
const subscribe = () => () => {};

export function useIsHydrated(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => true,
    () => false
  );
}
