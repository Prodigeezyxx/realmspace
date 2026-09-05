"use client";

/**
 * realmspace — the events *this browser* refused, for `/ops`.
 *
 * The counterpart of `useDeadLetters`, and the reason it exists is the finding
 * the Phase 6 acceptance walk closed on: the backend and the browser disagreed
 * about what a malformed payload was, and only the backend failed loudly. The
 * backend parked 319 events in `dead_letter`; the browser, handed the same
 * events, rendered `NaN`.
 *
 * Both refuse now, and `/ops` is where the two queues are read side by side.
 *
 * Read from **localStorage, not the backend**. That is the whole point of this
 * panel: these are events the server accepted and this browser could not use.
 * There is no endpoint that knows about them, and asking the server would
 * return the events themselves rather than the disagreement.
 */

import { useCallback, useSyncExternalStore } from "react";

import {
  clearRefusals,
  refusalSnapshot,
  subscribeRefusals,
  type RefusedSession,
} from "@/lib/bus";
import { useIsHydrated } from "@/lib/hooks/useIsHydrated";
import { getTenantId } from "@/lib/tenant/context";

export type { RefusedSession };

export interface RefusedState {
  items: RefusedSession[];
  /** Total across every activation, for the header. */
  count: number;
}

/** Nothing to read on the server, where there is no localStorage. */
const NONE: RefusedSession[] = [];
const noRefusals = () => NONE;

/**
 * Refusals for the active tenant, refreshed whenever one is recorded.
 *
 * Partitioned by tenant like everything else in the browser's storage since the
 * Phase 6 walk found a brand-new organisation reading another client's list of
 * activations — a second organisation on this laptop must not see the first
 * one's refusals either. The tenant is a dependency rather than a snapshot for
 * the same reason: it is a guess until a token exchange verifies it, and this
 * panel has to follow it when it changes.
 */
export function useRefusedEvents(): RefusedState & { forget: (s: string) => void } {
  const hydrated = useIsHydrated();
  const tenantId = hydrated ? getTenantId() : "";
  const items = useSyncExternalStore(
    subscribeRefusals,
    () => refusalSnapshot(tenantId),
    noRefusals
  );

  const forget = useCallback(
    (sessionId: string) => clearRefusals(tenantId, sessionId),
    [tenantId]
  );

  return {
    items,
    count: items.reduce((n, s) => n + s.refused.length, 0),
    forget,
  };
}
