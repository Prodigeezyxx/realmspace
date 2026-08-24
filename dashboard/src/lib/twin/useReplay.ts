"use client";

/**
 * realmspace — load a session's events for the twin, without going through the
 * durable local log.
 *
 * The local log is a 5,000-event ring buffer that silently drops its oldest
 * entries. A camera at 20fps fills that in minutes, and the trimming would take
 * `/report` down with it — the earliest dwells would simply stop existing, and
 * nothing would say so. So the replay pages the backend and holds the result in
 * memory for as long as the page is open: **it deliberately does not call
 * `mirror()`**, which is what every other reader does.
 *
 * The demo session is local-only by design (`markLocalOnly` keeps its invented
 * visitors off the real bus), so it reads the local log instead. That is the one
 * branch here, and it is the only one.
 */

import { useEffect, useState } from "react";

import { fetchSessionEvents, isRemoteBusEnabled, readAll } from "@/lib/bus";
import { getTenantId } from "@/lib/tenant/context";
import type { RealmEvent } from "@/lib/contracts";
import type { Session } from "@/lib/session/types";
import { buildReplay, type Replay, type ReplayZone } from "./replay";

export type ReplayStatus = "loading" | "ready" | "empty";

export interface ReplayState {
  status: ReplayStatus;
  replay: Replay;
  /** How many events the replay was built from. */
  eventCount: number;
}

function zonesOf(session: Session): ReplayZone[] {
  return session.zones.map((z) => ({
    id: z.id,
    name: z.name,
    polygon: z.polygon,
  }));
}

export function useReplay(session: Session): ReplayState {
  const [state, setState] = useState<ReplayState>(() => ({
    status: "loading",
    replay: buildReplay([], []),
    eventCount: 0,
  }));

  useEffect(() => {
    let cancelled = false;

    async function load() {
      const tenantId = getTenantId();

      // The demo never leaves this browser, so its events are only ever local.
      const events =
        isRemoteBusEnabled() && !session.isDemo
          ? await fetchSessionEvents(tenantId, session.id)
          : (readAll(tenantId, session.id) as RealmEvent[]);

      if (cancelled) return;

      const replay = buildReplay(events, zonesOf(session));
      setState({
        status: replay.tracks.length ? "ready" : "empty",
        replay,
        eventCount: events.length,
      });
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [session]);

  return state;
}
