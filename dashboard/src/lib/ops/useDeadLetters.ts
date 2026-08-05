"use client";

/**
 * realmspace — the HITL review queue, client side.
 *
 * `event-bus-spec.md` §5 has always promised that an event no consumer could
 * process "surfaces in the HITL review screen for a human". The table has
 * existed since the first migration; until now there was no way to look at it.
 *
 * The queue only matters if somebody reads it, which is mostly a design problem
 * rather than a data one: an entry that cannot be cleared, or that appears three
 * times, teaches an operator to stop looking. Hence the backend de-duplicates
 * repeat parks, and hence dismiss exists alongside retry.
 */

import { useCallback, useEffect, useState } from "react";

import { busEmail, busUrl, ensureToken, isRemoteBusEnabled } from "@/lib/bus";

export interface DeadLetter {
  id: number;
  consumer: string;
  eventSeq: number;
  error: string;
  attempts: number;
  createdAt: string;
  resolvedAt: string | null;
  retryable: boolean;
  retryBlockedReason: string | null;
  eventType: string | null;
  eventSessionId: string | null;
  eventPayload: Record<string, unknown> | null;
  occurredAt: string | null;
}

export type QueueStatus = "loading" | "ready" | "offline" | "error";

export interface QueueState {
  status: QueueStatus;
  items: DeadLetter[];
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
 * Whether there is a backend to read at all, decided before the first render.
 *
 * Not an error: the laptop demo runs with no backend, and a queue with nothing
 * behind it should say so rather than show an empty success. Resolved in the
 * initial state rather than in the effect, so the "nothing to read" case never
 * costs a render pass — and so the effect body has no synchronous setState in
 * it, which is a cascading render waiting to happen.
 */
function initialState(): QueueState {
  return isRemoteBusEnabled()
    ? { status: "loading", items: [], detail: null }
    : {
        status: "offline",
        items: [],
        detail: "No backend configured, so there is no queue to read.",
      };
}

export function useDeadLetters() {
  const [state, setState] = useState<QueueState>(initialState);

  const refresh = useCallback(async () => {
    if (!isRemoteBusEnabled()) return;

    try {
      const res = await authed("/v1/dead-letters");
      if (!res) {
        setState({
          status: "error",
          items: [],
          detail: "Could not authenticate with the bus.",
        });
        return;
      }
      if (!res.ok) {
        setState({
          status: "error",
          items: [],
          detail: `The bus returned ${res.status}.`,
        });
        return;
      }
      setState({ status: "ready", items: await res.json(), detail: null });
    } catch {
      setState({ status: "error", items: [], detail: "The bus is unreachable." });
    }
  }, []);

  useEffect(() => {
    if (!isRemoteBusEnabled()) return;
    // `refresh` is async and every setState in it happens after an `await`, so
    // none runs synchronously during the effect — the rule cannot see through
    // the promise. Suppressed narrowly, and with the reason, rather than left
    // in the lint output where it would camouflage the next real one.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh();
  }, [refresh]);

  /**
   * Re-run a parked event through its consumer.
   *
   * A failed retry is a **200 with `resolved: false`**, not an error — the row
   * stays open with its attempt count raised and the newest traceback attached.
   * "We tried and it still does not work" is worth showing; the alternative is
   * an operator pressing the same button with nothing to show for it.
   */
  const retry = useCallback(
    async (id: number): Promise<string | null> => {
      const res = await authed(`/v1/dead-letters/${id}/retry`, { method: "POST" });
      if (!res) return "Could not authenticate with the bus.";

      if (res.status === 409 || res.status === 404 || res.status === 410) {
        const body = await res.json().catch(() => null);
        return body?.detail ?? `The bus refused the retry (${res.status}).`;
      }
      if (!res.ok) return `The bus returned ${res.status}.`;

      const body = await res.json();
      await refresh();
      return body.resolved ? null : (body.error ?? "The retry failed again.");
    },
    [refresh]
  );

  const dismiss = useCallback(
    async (id: number): Promise<string | null> => {
      const res = await authed(`/v1/dead-letters/${id}/resolve`, { method: "POST" });
      if (!res?.ok) return `Could not dismiss (${res?.status ?? "offline"}).`;
      await refresh();
      return null;
    },
    [refresh]
  );

  return { ...state, refresh, retry, dismiss };
}
