"use client";

/**
 * realmspace — has this organisation ever run anything?
 *
 * ## The failure this exists for
 *
 * The Phase 6 acceptance walk recorded it and left it: *"a paying customer's
 * first screen showing a fictional activation is a product decision somebody
 * should make deliberately rather than inherit."* `session/store.ts` seeds
 * `DEMO_SESSION` for every tenant and makes it the active one, so an
 * organisation that has just signed itself up lands on Lagos Showroom's
 * invented figures — a client name, a venue and a set of numbers belonging to
 * nobody.
 *
 * The demo stays. It is the laptop demo's whole point, it is labelled, and
 * `/sessions` already filters and badges it. What changes is that it is no
 * longer the first thing a real customer is shown.
 *
 * ## Three conditions, and the third is the careful one
 *
 * 1. There is a backend (`isRemoteBusEnabled`). With none, this is the laptop
 *    demo and nothing here runs at all.
 * 2. This browser's store holds no activation of this tenant's own.
 * 3. **The backend agrees** — `fetchSessionList` returns `[]`.
 *
 * The third is what keeps an operator on their second laptop out of this. The
 * local store is per-browser, so "I have never seen an activation" and "this
 * organisation has never run one" are different claims, and only the server can
 * settle the second. `fetchSessionList` already keeps `null` (no backend, or the
 * read failed) apart from `[]` (this client has run nothing) — a distinction it
 * was given for the benchmark and which is exactly the one needed here.
 * **A `null` never gates**: telling somebody they have no activations because
 * their network dropped is the same class of mistake as reporting a broken
 * pipeline as a quiet day.
 */

import { useEffect, useState, useSyncExternalStore } from "react";

import { busEmail, isRemoteBusEnabled } from "@/lib/bus";
import { fetchSessionList } from "@/lib/session/publish";
import { useSessions } from "@/lib/session/store";
import { getTenantId, subscribeTenantId } from "@/lib/tenant/context";

export type FirstRunStatus =
  /** Still asking the backend. Render the page rather than a wrong answer. */
  | "checking"
  /** This organisation has never run an activation. */
  | "first-run"
  /** They have. Either the store knows it or the backend said so. */
  | "established";

/** What the backend said, or that it did not say. */
export type RemoteAnswer = "unknown" | "none" | "some";

/**
 * `fetchSessionList`'s reply, as an answer.
 *
 * `null` means *we could not tell* — no backend, no token, a read that failed —
 * and it maps to `unknown`, never to `none`. Telling somebody they have no
 * activations because their network dropped is the same class of mistake as
 * reporting a broken pipeline to a client as a quiet day.
 */
export function remoteAnswer(list: unknown[] | null): RemoteAnswer {
  if (list === null) return "unknown";
  return list.length ? "some" : "none";
}

/**
 * The decision, with no React in it.
 *
 * Pure so it can be tested the way `wizard-validation.ts` is — this project's
 * vitest setup deliberately has no component testing, and the part worth
 * pinning here is the reasoning, not the rendering.
 */
export function firstRunStatus(input: {
  /** Is a backend configured? With none, this is the laptop demo. */
  remoteBus: boolean;
  /** Activations of this tenant's own in the local store, demo excluded. */
  ownSessions: number;
  remote: RemoteAnswer;
}): FirstRunStatus {
  if (!input.remoteBus) return "established";
  if (input.ownSessions > 0) return "established";
  if (input.remote === "none") return "first-run";
  if (input.remote === "some") return "established";
  return "checking";
}

/** Nothing to subscribe to on the server; the tenant is a client-side value. */
const serverTenant = () => "";

/**
 * Whether this organisation has anything of its own yet.
 *
 * Re-asks when the **verified** tenant lands. `tenant/context.ts` says in as
 * many words that the value starts as a guess and only becomes the truth after
 * a token exchange — a gate that decided before then would decide for the wrong
 * organisation, which is the bug that made a full activation render as a quiet
 * day.
 */
export function useFirstRun(): FirstRunStatus {
  const sessions = useSessions();
  const ownSessions = sessions.filter((s) => !s.isDemo).length;
  const tenantId = useSyncExternalStore(
    subscribeTenantId,
    getTenantId,
    serverTenant
  );

  const [remote, setRemote] = useState<RemoteAnswer>("unknown");

  useEffect(() => {
    if (ownSessions > 0) return; // the store already answers it
    if (!isRemoteBusEnabled()) return;

    // Guarded, because the verified tenant can land while this is in flight and
    // the answer for the previous organisation must not be applied to the next.
    let cancelled = false;
    void fetchSessionList(busEmail()).then((list) => {
      if (cancelled) return;
      const answer = remoteAnswer(list);
      if (answer !== "unknown") setRemote(answer);
    });
    return () => {
      cancelled = true;
    };
    // `tenantId` is a dependency and not a value: the verified one arriving is
    // what makes this ask again, on behalf of the right organisation.
  }, [ownSessions, tenantId]);

  return firstRunStatus({
    remoteBus: isRemoteBusEnabled(),
    ownSessions,
    remote,
  });
}
