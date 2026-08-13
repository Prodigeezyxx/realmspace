"use client";

/**
 * realmspace — dispatches whose outcome nobody knows, client side.
 *
 * The sibling of `useDeadLetters`, and deliberately not folded into it. A dead
 * letter is a failure: something raised, three times, and there is a traceback
 * to read. This is the case that is neither failure nor success — the backend
 * claimed a dispatch, the process died before the outbound call returned, and
 * whether Slack got the message is genuinely unknown.
 *
 * That difference is the whole design of this panel. There is no retry button,
 * because a retry is exactly the double-post the claim exists to prevent. The
 * only thing on offer is a question a person can answer and the system cannot:
 * *did the message arrive?*
 */

import { useCallback, useEffect, useState } from "react";

import { busEmail, busUrl, ensureToken, isRemoteBusEnabled } from "@/lib/bus";

export interface StrandedDispatch {
  id: number;
  /**
   * `rule` or `handoff`. A stuck Slack post and a stuck lead are different
   * urgencies, and `ruleId` holds a rule for one and a session for the other —
   * without this there is nothing on the row to say which you are looking at.
   */
  kind: string;
  ruleId: string;
  /**
   * Null when the rule has since been deleted, and always null for a handoff,
   * which has no rule. Shown as absent, not blank.
   */
  ruleName: string | null;
  actionType: string;
  attempts: number;
  createdAt: string;
  strandedForSeconds: number;
}

export type Verdict = "delivered" | "failed";

export type StrandedStatus = "loading" | "ready" | "offline" | "error";

export interface StrandedState {
  status: StrandedStatus;
  items: StrandedDispatch[];
  detail: string | null;
}

async function authed(path: string, init?: RequestInit): Promise<Response | null> {
  const token = await ensureToken(busEmail());
  if (!token) return null;
  return fetch(`${busUrl()}${path}`, {
    ...init,
    headers: {
      ...(init?.headers ?? {}),
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
  });
}

/**
 * Read the queue. Exported and free of React so it can be tested without
 * rendering anything — the same split `useLiveStats.test.ts` makes, and for the
 * same reason: what is worth asserting here is what the browser does with the
 * bus's answers, not React's effect timing.
 */
export async function fetchStranded(): Promise<StrandedState> {
  if (!isRemoteBusEnabled()) return initialState();

  try {
    const res = await authed("/v1/dispatches/stranded");
    if (!res) {
      return {
        status: "error",
        items: [],
        detail: "Could not authenticate with the bus.",
      };
    }
    if (!res.ok) {
      return { status: "error", items: [], detail: `The bus returned ${res.status}.` };
    }
    return { status: "ready", items: await res.json(), detail: null };
  } catch {
    return { status: "error", items: [], detail: "The bus is unreachable." };
  }
}

/**
 * Record what the operator found out.
 *
 * Returns the backend's own description of what the verdict did, or an error
 * string. The wording comes from the API rather than from this file on purpose:
 * "released" and "closed" are claims about the dispatcher's state machine, and a
 * copy of them in the browser is a copy that goes stale.
 *
 * Note there is no sibling to this that re-sends. A retry is exactly the
 * double-post the claim exists to prevent, so the absence is the design.
 */
export async function postVerdict(
  id: number,
  verdict: Verdict,
  note?: string
): Promise<{ effect: string } | { error: string }> {
  const res = await authed(`/v1/dispatches/${id}/resolve`, {
    method: "POST",
    body: JSON.stringify({ verdict, note: note || null }),
  });
  if (!res) return { error: "Could not authenticate with the bus." };

  if (res.status === 404 || res.status === 409) {
    const body = await res.json().catch(() => null);
    return { error: body?.detail ?? `The bus refused the verdict (${res.status}).` };
  }
  if (!res.ok) return { error: `The bus returned ${res.status}.` };

  return { effect: (await res.json()).effect };
}

/** Same reasoning as `useDeadLetters.initialState`: no backend is an answer. */
function initialState(): StrandedState {
  return isRemoteBusEnabled()
    ? { status: "loading", items: [], detail: null }
    : {
        status: "offline",
        items: [],
        detail: "No backend configured, so there are no dispatches to read.",
      };
}

export function useStrandedDispatches() {
  const [state, setState] = useState<StrandedState>(initialState);

  const refresh = useCallback(async () => {
    if (!isRemoteBusEnabled()) return;
    setState(await fetchStranded());
  }, []);

  useEffect(() => {
    if (!isRemoteBusEnabled()) return;
    // Every setState below happens after an await, so none runs synchronously
    // during the effect. Same narrow suppression as useDeadLetters, with the
    // same reason.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh();
  }, [refresh]);

  const resolve = useCallback(
    async (id: number, verdict: Verdict, note?: string) => {
      const result = await postVerdict(id, verdict, note);
      if ("effect" in result) await refresh();
      return result;
    },
    [refresh]
  );

  return { ...state, refresh, resolve };
}
