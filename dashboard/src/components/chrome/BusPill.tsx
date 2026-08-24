"use client";

/**
 * realmspace — the live-bus connection, and a pill that admits when it is down.
 *
 * `BusBridge` holds the WebSocket open for the active session; `BusPill` renders
 * its state. They are one file because they are one fact: a bridge with no
 * indicator is the failure this component exists to prevent.
 *
 * A dashboard whose feed has silently dropped looks *exactly* like a quiet booth
 * — the numbers simply stop moving. An operator watching the floor cannot tell
 * those apart, and will happily report a dead afternoon as a slow one. So the
 * pill shows the connection whenever a backend is configured, including when it
 * is fine, because a badge that only appears on failure is a badge nobody learns
 * to look for.
 */

import { useEffect, useSyncExternalStore } from "react";

import { Pill } from "@/components/ui/Pill";
import { useAuth } from "@/components/auth/AuthProvider";
import {
  busEmail,
  connectLiveFeed,
  getBusStatus,
  isRemoteBusEnabled,
  subscribeBusStatus,
  type BusStatus,
} from "@/lib/bus";
import { useTenantId } from "@/lib/tenant/useTenantId";
import { useIsHydrated } from "@/lib/hooks/useIsHydrated";
import { useActiveSession, useHydrated } from "@/lib/session/store";

const OFF_STATE = {
  status: "off" as BusStatus,
  detail: null,
  lastSeq: 0,
  pendingOutbound: 0,
};

function useBusState() {
  return useSyncExternalStore(
    subscribeBusStatus,
    getBusStatus,
    // The server render has no socket and no localStorage. Returning the live
    // object here would hydrate with a status the server could not have known.
    () => OFF_STATE
  );
}

/**
 * Keeps the live feed open for the active session. Renders nothing.
 *
 * Re-subscribes when the session changes, because the socket is per-session:
 * `/v1/ws/{tenant}/{session}`. Switching sessions with the old socket open would
 * mirror another session's events into this one's log.
 */
export function BusBridge() {
  const { user } = useAuth();
  const active = useActiveSession();
  const email = user?.email ?? busEmail();
  // Subscribed rather than read once, so the socket reconnects under the
  // verified tenant when the token lands instead of staying on the guess it
  // opened with — which would mirror nothing for the whole session.
  const tenantId = useTenantId();
  // Two different hydrations, both of which have to have happened. React's,
  // because `useTenantId` reports the default during the hydrating render by
  // definition — the server had no storage to read. And the session store's,
  // because before it hydrates the active session is the demo one.
  const reactHydrated = useIsHydrated();
  const storeHydrated = useHydrated();
  const ready = reactHydrated && storeHydrated;

  useEffect(() => {
    // Both halves of "who am I" have to have settled, or this opens a socket
    // against a guess. Before the store hydrates, `active` is the demo session
    // — a real id, so the `!active?.id` guard below passes — and before the
    // token lands the tenant is the default. The backend correctly refuses that
    // pair with a 403, the pill shows BUS DOWN, and the connection that
    // succeeds a moment later has to clear an alarm that was never real.
    if (!ready || !isRemoteBusEnabled() || !active?.id) return;
    return connectLiveFeed({
      tenantId,
      sessionId: active.id,
      email,
    });
  }, [active?.id, email, tenantId, ready]);

  return null;
}

type PillVariant = React.ComponentProps<typeof Pill>["variant"];

const LABELS: Record<BusStatus, { label: string; variant: PillVariant }> = {
  off: { label: "Local only", variant: "neutral" },
  connecting: { label: "Bus connecting", variant: "neutral" },
  live: { label: "Bus live", variant: "success" },
  retrying: { label: "Bus reconnecting", variant: "warn" },
  error: { label: "Bus down", variant: "alert" },
};

export function BusPill() {
  const state = useBusState();
  if (!isRemoteBusEnabled()) return null;

  const meta = LABELS[state.status];
  const queued =
    state.pendingOutbound > 0 ? ` · ${state.pendingOutbound} queued` : "";

  return (
    <span
      title={
        state.detail ??
        `Connected to the event bus. Last event #${state.lastSeq}.`
      }
      className="hidden md:inline-flex"
    >
      <Pill variant={meta.variant}>
        {meta.label}
        {queued}
      </Pill>
    </span>
  );
}
