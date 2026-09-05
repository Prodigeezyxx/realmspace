"use client";

/**
 * realmspace — CV drift telemetry, client side.
 *
 * `roadmap.md`'s blind-spot table has listed "CV model drift → calibration UI +
 * drift telemetry" since the founder architecture dump, and `event-bus-spec.md`
 * §3 pinned `drift.detected` in Phase 3 so the producer would not arrive to a
 * 422. `backend/app/consumers/drift.py` is that producer; this reads what it
 * writes.
 *
 * Read from the **log**, not from a status endpoint. Drift is a sequence of
 * findings with timestamps, and what an operator needs is the shape over the
 * activation — a camera that has been degrading for an hour is a different
 * conversation from one that dropped five minutes ago. A "current status" API
 * would have to pick one of those to show and would necessarily pick the wrong
 * one half the time.
 *
 * The panel renders `observed` **and** `baseline` together, never the severity
 * alone. §3 is explicit that the event "states the comparison it is making
 * instead of asserting a verdict someone later cannot check", and a UI that
 * shows only "critical" throws that away — an operator who cannot see the
 * numbers cannot tell a real degradation from a threshold set badly.
 */

import { useCallback, useEffect, useState } from "react";

import { busEmail, busUrl, ensureToken, isRemoteBusEnabled } from "@/lib/bus";
import { payloadFromWire } from "@/lib/bus/wire";
import type { DriftDetectedPayload } from "@/lib/contracts/events";

export interface DriftFinding extends DriftDetectedPayload {
  seq: number;
  sessionId: string;
  occurredAt: string;
}

export type DriftStatus = "loading" | "ready" | "offline" | "error";

export interface DriftState {
  status: DriftStatus;
  items: DriftFinding[];
  detail: string | null;
}

/** How many findings to show. Newest first; older ones are still in the log. */
const LIMIT = 50;

function initialState(): DriftState {
  return isRemoteBusEnabled()
    ? { status: "loading", items: [], detail: null }
    : {
        status: "offline",
        items: [],
        detail: "No backend configured, so there is no telemetry to read.",
      };
}

/**
 * Findings for one session, or for every session when `sessionId` is omitted.
 *
 * `/ops` is a deployment-wide screen and passes nothing; the live view passes
 * the activation it is watching.
 */
export function useDriftEvents(sessionId?: string) {
  const [state, setState] = useState<DriftState>(initialState);

  const refresh = useCallback(async () => {
    if (!isRemoteBusEnabled()) return;

    try {
      const token = await ensureToken(busEmail());
      if (!token) {
        setState({
          status: "error",
          items: [],
          detail: "Could not authenticate with the bus.",
        });
        return;
      }

      const query = new URLSearchParams({
        type: "drift.detected",
        limit: String(LIMIT),
      });
      if (sessionId) query.set("session_id", sessionId);

      const res = await fetch(`${busUrl()}/events?${query}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) {
        setState({
          status: "error",
          items: [],
          detail: `The bus returned ${res.status}.`,
        });
        return;
      }

      const wire = (await res.json()) as Array<{
        seq: number;
        sessionId: string;
        occurredAt: string;
        payload: unknown;
      }>;

      const items = wire.map((row) => ({
        // Through the same translation every other reader uses. The bus pins
        // snake_case for the Python producers and this app's contract is
        // camelCase; the Phase 2 lesson is that an untranslated payload does
        // not raise, it renders as `undefined` beside a real number.
        ...(payloadFromWire(
          "drift.detected",
          row.payload
        ) as unknown as DriftDetectedPayload),
        seq: row.seq,
        sessionId: row.sessionId,
        occurredAt: row.occurredAt,
      }));

      // Newest first. The log is ordered by seq ascending, which is right for a
      // consumer and backwards for a person.
      items.reverse();

      setState({ status: "ready", items, detail: null });
    } catch {
      setState({ status: "error", items: [], detail: "The bus is unreachable." });
    }
  }, [sessionId]);

  useEffect(() => {
    if (!isRemoteBusEnabled()) return;
    // Every setState in `refresh` happens after an `await`, so none runs
    // synchronously during the effect. Same narrow suppression, and same
    // reason, as `useDeadLetters`.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh();
  }, [refresh]);

  return { ...state, refresh };
}

/**
 * How far the observed value has moved from its baseline, as a signed fraction.
 *
 * Signed rather than absolute, and computed here rather than carried on the
 * event, because the event deliberately carries the two measurements and lets
 * the reader do the arithmetic — that is what makes the comparison checkable.
 */
export function drift(finding: DriftFinding): number {
  if (!finding.baseline) return 0;
  return (finding.observed - finding.baseline) / Math.abs(finding.baseline);
}

/** What the metric is called on screen, and which direction is bad. */
export const METRIC_LABELS: Record<string, string> = {
  confidence_mean: "Detection confidence",
  track_length: "Tracks lost mid-visit",
};
