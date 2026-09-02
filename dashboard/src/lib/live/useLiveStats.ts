"use client";

/**
 * realmspace — the live screen's view of the durable log.
 *
 * `/live` never read the event bus. Every figure on it came from the browser's
 * own webcam tracker — this tab, this machine — or from a mock file. So an
 * activation running the way the backend was built for, a real camera feeding
 * perception → bus → tracker, showed *nothing* on the live screen. The feed had
 * been arriving since the bus bridge landed and nothing was reading it.
 *
 * ## Why this is throttled
 *
 * The log fans out on every append, and a running camera appends at frame rate —
 * several detections per person per second, each one waking every subscriber.
 * Recomputing a whole-session scorecard on each would make the live page the
 * slowest thing in the product, and it would do it precisely when the room is
 * busiest. A recompute a second is far finer than anybody can read.
 */

import { useEffect, useRef, useState } from "react";

import {
  backfillSession,
  busEmail,
  isRemoteBusEnabled,
  readAll,
  subscribe,
} from "@/lib/bus";
import { computeScorecard, type Scorecard } from "@/lib/roi/scorecard";
import { summarizeCost, type CostSummary } from "@/lib/roi/cost";
import { useTenantId } from "@/lib/tenant/useTenantId";
import { ZONE_TYPE_TO_KIND } from "@/lib/session/publish";
import type { RealmEvent, ZoneNode } from "@/lib/contracts";
import type { Session } from "@/lib/session/types";
import { hourlyVisitors } from "@/lib/report/derive";
import {
  lastEventAt,
  liveInsights,
  presentNow,
  staffPrompts,
  zoneOccupancy,
  type LiveInsight,
  type StaffPrompt,
} from "./derive";

/** How often the scorecard may be recomputed, however fast events arrive. */
const RECOMPUTE_MS = 1000;

export interface LiveStats {
  /** True once the log has anything for this session — the tiles' precedence gate. */
  hasData: boolean;
  peopleNow: number;
  scorecard: Scorecard;
  eventCount: number;
  /** ms epoch of the most recent event, or null. */
  lastEventAt: number | null;
  /** Distinct visitors per hour so far — the traffic sparkline's shape. */
  traffic: number[];
  /**
   * What the system itself has spent running this session — LLM tokens, action
   * units, enrichment credits. Not the activation cost, which is fixed and
   * lives in `scorecard.pipeline`; see `lib/roi/cost.ts`.
   */
  cost: CostSummary;
  /**
   * What rules have asked the floor staff to do, newest first and recent only.
   * The `< 3s` end of Phase 3 — see `staffPrompts` for what "recent" means.
   */
  prompts: StaffPrompt[];
  /**
   * What the insight agent has said about this room, newest first. No TTL,
   * unlike `prompts`: an observation about a window that has closed stays
   * true, while an instruction goes stale.
   */
  insights: LiveInsight[];
  /**
   * People in each zone right now, keyed by zone id. A zone with nobody in it
   * is **absent rather than zero** — see `zoneOccupancy`, and `ZoneList`, which
   * has to tell "nobody is there" apart from "this browser has no events".
   */
  zoneOccupancy: Map<string, number>;
}

function emptyStats(): LiveStats {
  return {
    hasData: false,
    peopleNow: 0,
    scorecard: computeScorecard([]),
    eventCount: 0,
    lastEventAt: null,
    traffic: [],
    cost: summarizeCost([]),
    prompts: [],
    insights: [],
    zoneOccupancy: new Map(),
  };
}

function zonesFor(session: Session): ZoneNode[] {
  return session.zones.map((z) => ({
    id: z.id,
    name: z.name,
    kind: ZONE_TYPE_TO_KIND[z.type] ?? "other",
    weight: z.weight ?? 1,
  }));
}

/**
 * Live figures for a session, recomputed as events arrive.
 *
 * Reads the local durable log, which the bus bridge mirrors the backend into —
 * so this works for a camera on an edge box, a browser webcam writing to the
 * same log, or both at once, without knowing which.
 */
export function useLiveStats(session: Session): LiveStats {
  const [stats, setStats] = useState<LiveStats>(emptyStats);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // From the store rather than read once: the verified tenant arrives with
  // the token, after this effect first runs, and a partition keyed on the
  // guess is empty. In the dependency list so the effect re-runs when the
  // real one lands.
  const tenantId = useTenantId();

  useEffect(() => {
    let cancelled = false;

    const recompute = () => {
      if (cancelled) return;
      const events = readAll(tenantId, session.id) as RealmEvent[];
      const measurement = session.measurement ?? {};
      const scorecard = computeScorecard(events, {
        zones: zonesFor(session),
        engagedThresholdSec: measurement.engagedThresholdSec,
        activationCost: measurement.activationCost,
        revenueInfluenced: measurement.revenueInfluenced,
        qualifiedLeads: measurement.qualifiedLeads,
      });
      setStats({
        hasData: events.length > 0,
        peopleNow: presentNow(events),
        scorecard,
        eventCount: events.length,
        lastEventAt: lastEventAt(events),
        traffic: hourlyVisitors(events),
        // Per *engaged* visitor, the same denominator CPEV uses, so the two
        // cost figures on the page can be read against each other.
        cost: summarizeCost(events, {
          engagedVisitors: scorecard.engagement.engagedVisitors,
        }),
        // Against the *log's* clock, not the browser's. A replay of yesterday
        // then shows the prompts that were live at each moment rather than an
        // empty panel, and a live session is unaffected because the two agree.
        prompts: staffPrompts(events, lastEventAt(events) ?? Date.now()),
        insights: liveInsights(events),
        zoneOccupancy: zoneOccupancy(events),
      });
    };

    // Leading-edge throttle: the first event after a quiet spell lands
    // immediately, and a burst behind it collapses into one recompute at the end
    // of the window. Trailing-only would make the page feel a second behind the
    // room for no benefit.
    const schedule = () => {
      if (timer.current) return;
      timer.current = setTimeout(() => {
        timer.current = null;
        recompute();
      }, RECOMPUTE_MS);
    };

    recompute();
    const unsubscribe = subscribe((event) => {
      // The log fans out across every partition; only this session's traffic
      // should cost us a recompute.
      if (event.tenantId === tenantId && event.sessionId === session.id) schedule();
    });

    // Pull the session's history once, so a page opened mid-activation starts
    // from the whole session rather than from whatever happens to arrive next.
    if (isRemoteBusEnabled()) {
      void backfillSession(tenantId, session.id, busEmail()).then(() => {
        if (!cancelled) recompute();
      });
    }

    return () => {
      cancelled = true;
      unsubscribe();
      if (timer.current) clearTimeout(timer.current);
      timer.current = null;
    };
  }, [session, tenantId]);

  return stats;
}
