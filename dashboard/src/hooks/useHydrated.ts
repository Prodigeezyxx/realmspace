"use client";

import { useSyncExternalStore } from "react";

function subscribe() {
  // The client/server-rendered distinction never changes after mount, so
  // there is nothing to subscribe to — this satisfies useSyncExternalStore's
  // contract without ever firing.
  return () => {};
}

/**
 * True once mounted on the client, false during SSR and the first client
 * render. Use this instead of a `useState(false)` + `useEffect(() => setTrue)`
 * pair — that pattern calls setState synchronously inside an effect body,
 * which the react-hooks/set-state-in-effect rule flags. This is the
 * React-recommended idiom (see useSyncExternalStore docs) for "render only
 * after hydration" guards, e.g. components that measure the DOM (Recharts).
 */
export function useHydrated(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => true,
    () => false
  );
}
