"use client";

/**
 * Subscribes the dashboard to the remote edge bus when NEXT_PUBLIC_BUS_URL is set.
 * Mirrors remote events into the local durable log so ROI / report keep working.
 * Mount once (RootProviders). StatusBar reads shared bridge-state.
 */

import { useEffect } from "react";
import { useSyncExternalStore } from "react";

import { append as localAppend } from "@/lib/bus/log";
import {
  getRemoteBusLastSeq,
  getRemoteBusState,
  setRemoteBusLastSeq,
  setRemoteBusState,
  subscribeRemoteBusState,
} from "@/lib/bus/bridge-state";
import {
  isRemoteBusConfigured,
  subscribeRemoteBus,
} from "@/lib/bus/remote";
import type { RealmEvent } from "@/lib/contracts";
import { getEventContext } from "@/lib/event-context";
import { getTenantId } from "@/lib/tenant/context";

function mirrorLocal(event: RealmEvent) {
  try {
    localAppend({
      tenantId: event.tenantId,
      sessionId: event.sessionId,
      type: event.type,
      payload: event.payload,
      eventId: event.eventId,
      occurredAt: event.occurredAt,
    });
  } catch {
    /* duplicate eventId is fine — local bus is idempotent */
  }
}

/** Mount once under RootProviders. */
export function useRemoteBusBridgeMount() {
  useEffect(() => {
    if (!isRemoteBusConfigured()) {
      setRemoteBusState("off");
      return;
    }
    const tenantId = getTenantId();
    const sessionId = getEventContext().sessionId;
    return subscribeRemoteBus(tenantId, sessionId, {
      onState: setRemoteBusState,
      onHello: (replay) => {
        for (const e of replay) {
          mirrorLocal(e);
          setRemoteBusLastSeq(e.seq);
        }
      },
      onEvent: (e) => {
        mirrorLocal(e);
        setRemoteBusLastSeq(e.seq);
      },
    });
  }, []);
}

/** Read-only hook for chrome / debug UI. */
export function useRemoteBusBridge() {
  const state = useSyncExternalStore(
    subscribeRemoteBusState,
    getRemoteBusState,
    () => "off" as const
  );
  const lastSeq = useSyncExternalStore(
    subscribeRemoteBusState,
    getRemoteBusLastSeq,
    () => 0
  );
  return { state, lastSeq, configured: isRemoteBusConfigured() };
}
