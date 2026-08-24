"use client";

/**
 * realmspace — what the rules actually did, read from the log.
 *
 * The composer screen used to show five hand-written lines ("Mirror Engagement
 * fired for P-216 — screen swapped to 'Rose Nuit'"), a fire count per rule, and
 * a "718 fires today" headline, none of which came from anywhere. This reads
 * `rule.fired` off the same durable log every other screen reads, so a count of
 * zero means the rule has not fired and a count of four means it fired four
 * times.
 *
 * Firings are on the bus by construction — the evaluator's whole output is a
 * `rule.fired` event, which is what makes "why did this fire?" answerable at all
 * (ADR-002's second reason for rules being data). Nothing extra had to be
 * recorded for this screen; it just had to look.
 */

import { useEffect, useState } from "react";

import { readAll, subscribe } from "@/lib/bus";
import { useTenantId } from "@/lib/tenant/useTenantId";
import type { RealmEvent } from "@/lib/contracts";
import type { RuleFiredPayload } from "@/lib/contracts/rules";

export interface Firing extends RuleFiredPayload {
  at: number;
  eventId: string;
}

export interface RuleActivity {
  /** Newest first, capped — this is a feed, not an audit. */
  firings: Firing[];
  /** How many times each rule fired this session, by ruleId. */
  countByRule: Record<string, number>;
  /** When each rule last fired, by ruleId. */
  lastFiredByRule: Record<string, number>;
}

const FEED_LIMIT = 25;

function derive(events: RealmEvent[]): RuleActivity {
  const firings: Firing[] = [];
  const countByRule: Record<string, number> = {};
  const lastFiredByRule: Record<string, number> = {};

  for (const e of events) {
    if (e.type !== "rule.fired") continue;
    const payload = e.payload as unknown as RuleFiredPayload;
    firings.push({ ...payload, at: e.occurredAt, eventId: e.eventId });
    countByRule[payload.ruleId] = (countByRule[payload.ruleId] ?? 0) + 1;
    lastFiredByRule[payload.ruleId] = Math.max(
      lastFiredByRule[payload.ruleId] ?? 0,
      e.occurredAt
    );
  }

  firings.sort((a, b) => b.at - a.at);
  return { firings: firings.slice(0, FEED_LIMIT), countByRule, lastFiredByRule };
}

const EMPTY: RuleActivity = { firings: [], countByRule: {}, lastFiredByRule: {} };

export function useRuleActivity(sessionId: string): RuleActivity {
  const [activity, setActivity] = useState<RuleActivity>(EMPTY);
  // From the store rather than read once: the verified tenant arrives with
  // the token, after this effect first runs, and a partition keyed on the
  // guess is empty. In the dependency list so the effect re-runs when the
  // real one lands.
  const tenantId = useTenantId();

  useEffect(() => {
    let cancelled = false;

    const recompute = () => {
      if (cancelled) return;
      setActivity(derive(readAll(tenantId, sessionId) as RealmEvent[]));
    };

    recompute();
    const unsubscribe = subscribe((event) => {
      // Only a firing changes this panel. Unlike the live tiles, which recompute
      // on any traffic, this one can ignore the detection torrent entirely —
      // there is no throttle here because there is nothing to throttle.
      if (
        event.tenantId === tenantId &&
        event.sessionId === sessionId &&
        event.type === "rule.fired"
      ) {
        recompute();
      }
    });

    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [sessionId, tenantId]);

  return activity;
}
