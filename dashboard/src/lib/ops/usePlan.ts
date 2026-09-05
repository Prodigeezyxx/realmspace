"use client";

/**
 * realmspace — which plan this organisation is on, and what it is using.
 *
 * `roadmap.md` Phase 6's billing bullet has two halves. The limits are real
 * (`backend/app/plans.py`): a save that would declare a fifth camera or arm a
 * third agent is refused with a **402** naming the tier. This panel is what
 * stops that 402 being the first time anybody hears a limit exists — the
 * failure mode the report's dead "Export PDF" button already taught us, where
 * the operator finds out in front of the client.
 *
 * ## No upgrade control, deliberately
 *
 * There is nothing here to press. Changing a plan needs a payment path, and the
 * Stripe half of that bullet is unbuilt — an "Upgrade" button wired to nothing
 * would be the lying button again. Moving a tenant between tiers is
 * `python -m app.plans set` on the box until a webhook owns it.
 *
 * ## `null` is two different things, and the API says which
 *
 * A `limit` of null means unlimited *and not enforced*, which for most of these
 * is because `gtm.md`'s tier table states no number rather than because the
 * tier is generous. A `used` of null with `counted: false` means nothing counts
 * this — true of Ask, whose only record is a `cost.metered` event the
 * deterministic provider never writes. Rendering either as a confident number
 * would be the invention this codebase deletes from reports.
 */

import { useCallback, useEffect, useState } from "react";

import { busEmail, busUrl, ensureToken, isRemoteBusEnabled } from "@/lib/bus";

export interface PlanLimit {
  /** Null: unlimited, and not enforced. See the file docstring. */
  limit: number | null;
  /** Null when nothing counts this. */
  used: number | null;
  counted: boolean;
  note: string | null;
}

export interface Plan {
  plan: string;
  label: string;
  retentionDays: number | null;
  cameras: PlanLimit;
  agents: PlanLimit;
  integrations: PlanLimit;
  asks: PlanLimit;
}

export type PlanStatus = "loading" | "ready" | "offline" | "error";

export interface PlanState {
  status: PlanStatus;
  plan: Plan | null;
  detail: string | null;
}

function initialState(): PlanState {
  return isRemoteBusEnabled()
    ? { status: "loading", plan: null, detail: null }
    : {
        status: "offline",
        plan: null,
        detail: "No backend configured, so there is no organisation to bill.",
      };
}

/** Free of React so it can be tested without rendering anything. */
export async function fetchPlan(): Promise<PlanState> {
  if (!isRemoteBusEnabled()) return initialState();

  try {
    const token = await ensureToken(busEmail());
    if (!token) {
      return {
        status: "error",
        plan: null,
        detail: "Could not authenticate with the bus.",
      };
    }
    const res = await fetch(`${busUrl()}/v1/plan`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) {
      return { status: "error", plan: null, detail: `The bus returned ${res.status}.` };
    }
    return { status: "ready", plan: await res.json(), detail: null };
  } catch {
    return { status: "error", plan: null, detail: "The bus is unreachable." };
  }
}

/**
 * How to render one limit.
 *
 * Kept here rather than in the component because the three cases are a
 * judgement, not formatting: an unenforced limit must not read as a generous
 * one, and an uncounted usage must not read as zero.
 */
export function describeLimit(limit: PlanLimit): {
  text: string;
  atCap: boolean;
} {
  if (!limit.counted) return { text: "not counted", atCap: false };
  const used = limit.used ?? 0;
  if (limit.limit === null) return { text: `${used} · no limit set`, atCap: false };
  return { text: `${used} of ${limit.limit}`, atCap: used >= limit.limit };
}

export function usePlan() {
  const [state, setState] = useState<PlanState>(initialState);

  const refresh = useCallback(async () => {
    if (!isRemoteBusEnabled()) return;
    setState(await fetchPlan());
  }, []);

  useEffect(() => {
    if (!isRemoteBusEnabled()) return;
    // Every setState happens after an await. Same narrow suppression, and the
    // same reason, as `useStrandedDispatches`.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh();
  }, [refresh]);

  return { ...state, refresh };
}
