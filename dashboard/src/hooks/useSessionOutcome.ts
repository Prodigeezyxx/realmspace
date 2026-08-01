"use client";

/**
 * realmspace — session outcome hook.
 *
 * Fetches the 4-layer ROI outcome for the active session from the edge API
 * (server-authoritative, durable bus) when it's configured. Shared by the
 * scorecard card, the report page, and the live ROI tile so every surface
 * renders the same numbers. Optional polling keeps the live tile fresh
 * while a session runs (operators optimize on day 2).
 */

import { useEffect, useState } from "react";

import { remoteSessionOutcome, isRemoteBusConfigured } from "@/lib/bus/remote";
import { getTenantId } from "@/lib/tenant/context";
import { getEventContext } from "@/lib/event-context";
import type { SessionOutcome } from "@/lib/contracts";

export type OutcomeState = "loading" | "remote" | "empty" | "offline";

export interface ZoneConfigInput {
  id: string;
  kind?: string;
  weight?: number;
}

async function fetchOutcome(opts: {
  zoneConfig?: ZoneConfigInput[];
  activationCost?: number;
  revenueInfluenced?: number;
}): Promise<SessionOutcome | null> {
  if (!isRemoteBusConfigured()) return null;
  const ctx = getEventContext();
  return remoteSessionOutcome(getTenantId(), ctx.sessionId, {
    zoneConfig: opts.zoneConfig,
    activationCost: opts.activationCost,
    revenueInfluenced: opts.revenueInfluenced,
  });
}

export function useSessionOutcome(opts: {
  zoneConfig?: ZoneConfigInput[];
  activationCost?: number;
  revenueInfluenced?: number;
  pollMs?: number;
} = {}) {
  const [outcome, setOutcome] = useState<SessionOutcome | null>(null);
  const [state, setState] = useState<OutcomeState>(
    isRemoteBusConfigured() ? "loading" : "offline"
  );

  useEffect(() => {
    if (!isRemoteBusConfigured()) {
      return;
    }
    let cancelled = false;

    const load = async () => {
      try {
        const res = await fetchOutcome(opts);
        if (cancelled) return;
        if (res) {
          setOutcome(res);
          setState(res.source.eventsRead > 0 ? "remote" : "empty");
        } else {
          setOutcome(null);
          setState("empty");
        }
      } catch {
        if (!cancelled) setState("offline");
      }
    };

    void load();

    if (opts.pollMs && opts.pollMs > 0) {
      const id = setInterval(() => void load(), opts.pollMs);
      return () => {
        cancelled = true;
        clearInterval(id);
      };
    }
    return () => {
      cancelled = true;
    };
    // Callers pass fresh object literals each render; re-fetching on every
    // render would defeat the interval. Session-scoped inputs are stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opts.pollMs]);

  return { outcome, state };
}
