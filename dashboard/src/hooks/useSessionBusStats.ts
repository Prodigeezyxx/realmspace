"use client";

/**
 * Session KPIs derived from the durable event bus — not from mocks and not from
 * the ephemeral in-tab detector store.
 *
 * Sources:
 *  - local durable log (lib/bus/log.ts) — real-time via subscribe(), works
 *    offline, and already mirrors remote events through the WS bridge.
 *  - remote graph snapshot (when NEXT_PUBLIC_BUS_URL is set) — polled so the
 *    "unique visitors" number is the graph's own count of Person nodes.
 *    This is the Phase-1 acceptance path: camera → event → graph node → KPI.
 */

import { useEffect, useRef, useState } from "react";

import {
  isRemoteBusConfigured,
  readAll,
  remoteGraphSnapshot,
  subscribe,
} from "@/lib/bus";
import type { RealmEvent } from "@/lib/contracts";
import { getEventContext } from "@/lib/event-context";
import { getTenantId } from "@/lib/tenant/context";

export interface SessionBusStats {
  /** any perception.detections recorded for this session */
  hasData: boolean;
  /** distinct anonIds across the whole session (durable, survives reload) */
  uniqueVisitors: number;
  /** distinct anonIds with an event in the last 3 seconds */
  activeNow: number;
  /** total perception.detection events recorded */
  totalDetections: number;
  firstEventAt: number | null;
  lastEventAt: number | null;
  /** distinct-person counts bucketed across the session (for traffic chart) */
  peopleSeries: number[];
  /** authoritative Person-node count from the edge graph (null when offline) */
  graphPersons: number | null;
}

const EMPTY: SessionBusStats = {
  hasData: false,
  uniqueVisitors: 0,
  activeNow: 0,
  totalDetections: 0,
  firstEventAt: null,
  lastEventAt: null,
  peopleSeries: [],
  graphPersons: null,
};

const ACTIVE_WINDOW_MS = 3_000;
const SERIES_BUCKETS = 30;

function compute(
  events: RealmEvent[],
  graphPersons: number | null
): SessionBusStats {
  const detections = events.filter((e) => e.type === "perception.detection");
  if (detections.length === 0) {
    return { ...EMPTY, graphPersons };
  }

  const seen = new Set<string>();
  let first = Infinity;
  let last = 0;
  for (const e of detections) {
    const anon = (e.payload as { anonId?: string }).anonId;
    if (anon) seen.add(anon);
    if (e.occurredAt < first) first = e.occurredAt;
    if (e.occurredAt > last) last = e.occurredAt;
  }

  const now = Date.now();
  const active = new Set<string>();
  for (const e of detections) {
    if (now - e.occurredAt <= ACTIVE_WINDOW_MS) {
      const anon = (e.payload as { anonId?: string }).anonId;
      if (anon) active.add(anon);
    }
  }

  // Bucket distinct-person counts across the session span for a traffic curve.
  const span = Math.max(1, last - first);
  const buckets = new Array<number>(SERIES_BUCKETS).fill(0);
  const bucketSeen = Array.from({ length: SERIES_BUCKETS }, () => new Set<string>());
  for (const e of detections) {
    const anon = (e.payload as { anonId?: string }).anonId;
    if (!anon) continue;
    const idx = Math.min(
      SERIES_BUCKETS - 1,
      Math.floor(((e.occurredAt - first) / span) * SERIES_BUCKETS)
    );
    bucketSeen[idx].add(anon);
  }
  for (let i = 0; i < SERIES_BUCKETS; i++) buckets[i] = bucketSeen[i].size;

  return {
    hasData: true,
    uniqueVisitors: Math.max(seen.size, graphPersons ?? 0),
    activeNow: active.size,
    totalDetections: detections.length,
    firstEventAt: first,
    lastEventAt: last,
    peopleSeries: buckets,
    graphPersons,
  };
}

export function useSessionBusStats(): SessionBusStats {
  const [stats, setStats] = useState<SessionBusStats>(EMPTY);
  const graphRef = useRef<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    let throttle: ReturnType<typeof setTimeout> | null = null;

    const recompute = () => {
      if (cancelled) return;
      const { sessionId } = getEventContext();
      const events = readAll(getTenantId(), sessionId);
      setStats(compute(events, graphRef.current));
    };
    const scheduleRecompute = () => {
      if (throttle) return;
      throttle = setTimeout(() => {
        throttle = null;
        recompute();
      }, 400);
    };

    // 1. Real-time: any new append on the local durable log (incl. WS-mirrored)
    const unsub = subscribe(scheduleRecompute);

    // 2. Decay "active now" once the camera stops producing events
    const ticker = setInterval(recompute, 1_000);

    // 3. Authoritative graph count when the edge API is configured
    let poller: ReturnType<typeof setInterval> | null = null;
    if (isRemoteBusConfigured()) {
      const poll = async () => {
        try {
          const { sessionId } = getEventContext();
          const snap = await remoteGraphSnapshot(getTenantId(), sessionId);
          if (!cancelled && snap && Array.isArray(snap.persons)) {
            graphRef.current = snap.persons.length;
            recompute();
          }
        } catch {
          /* edge API unreachable — local log remains the source of truth */
        }
      };
      void poll();
      poller = setInterval(poll, 5_000);
    }

    recompute();

    return () => {
      cancelled = true;
      unsub();
      clearInterval(ticker);
      if (poller) clearInterval(poller);
      if (throttle) clearTimeout(throttle);
    };
  }, []);

  return stats;
}
