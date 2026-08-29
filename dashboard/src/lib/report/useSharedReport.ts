"use client";

/**
 * The report, read through a share link instead of a login.
 *
 * ## The same computation, a different door
 *
 * This builds the identical `SessionReport` that `useSessionReport` does, and
 * hands it to the same `computeScorecard`. That is deliberate and it is the
 * whole point: a client and an operator looking at the same activation must not
 * be reading two different numbers, which is exactly what a second
 * implementation would eventually give them. Only the endpoints differ.
 *
 * ## What it does not read
 *
 * No benchmark. `useBenchmark` compares this activation against the tenant's
 * *other* activations, and a share link is scoped to one — showing a client
 * their own client's other events would be the leak this design exists to
 * prevent. No live socket, no twin, no Ask, no ledger.
 *
 * ## The events arrive redacted
 *
 * `routers/share.py` runs `erasure.redact` over every PII-typed event on the
 * way out, so contact details are gone before they reach this browser. The
 * counts survive — the events are redacted, not removed — so `leadsCaptured`
 * on a shared report is the same number the operator sees.
 *
 * ## And they go through the same boundary as every other inbound event
 *
 * The first version of this file did not, and the shared report rendered one
 * visitor where there were six, with `NaN` for average dwell. The bus pins
 * snake_case for the Python producers and this app's contract is camelCase, so
 * raw wire payloads reaching `computeScorecard` produce exactly the
 * plausible-looking wrong numbers `lib/bus/wire.ts` was written to prevent —
 * and it was caught by looking at the page, which is the point of looking.
 *
 * So: `eventFromWire` to translate, `validatePayload` to refuse anything a
 * reader could not use. Not `mirror()`, because that appends to the local
 * durable log, and a client's browser has no business holding a copy of
 * somebody else's activation after the tab is closed.
 */

import { useEffect, useState } from "react";

import { busUrl, eventFromWire, isRemoteBusEnabled, type WireEvent } from "@/lib/bus";
import { validatePayload } from "@/lib/contracts";
import { computeScorecard } from "@/lib/roi/scorecard";
import { ZONE_TYPE_TO_KIND, type RemoteSessionConfig } from "@/lib/session/publish";
import type { RealmEvent, ZoneNode } from "@/lib/contracts";
import type { SessionReport } from "@/lib/report/useSessionReport";
import type { ZoneType } from "@/lib/session/types";

export type SharedStatus = "loading" | "ready" | "invalid" | "offline";

export interface SharedReport {
  status: SharedStatus;
  report: SessionReport | null;
  config: RemoteSessionConfig | null;
}

/** Pages the shared event feed, which is cursored on `seq` like every other read. */
async function fetchAllEvents(token: string): Promise<RealmEvent[]> {
  const PAGE = 500;
  const out: RealmEvent[] = [];
  let since = 0;

  for (let page = 0; page < 400; page++) {
    const res = await fetch(
      `${busUrl()}/v1/share/${encodeURIComponent(token)}/events` +
        `?since_seq=${since}&limit=${PAGE}`
    );
    if (!res.ok) break;
    const batch = (await res.json()) as WireEvent[];
    if (!batch.length) break;

    for (const wire of batch) {
      // Translated, then checked — the same order and the same reasons as
      // `bus/remote.ts`. A payload no reader can use is dropped rather than
      // averaged into a figure a client is shown.
      const translated = eventFromWire(wire);
      if (!validatePayload(wire.type, translated.payload).ok) continue;
      out.push({
        ...translated,
        seq: wire.seq,
        eventId: wire.eventId,
        occurredAt: wire.occurredAt,
        recordedAt: wire.recordedAt,
      } as RealmEvent);
    }
    // On `seq`, not an offset: seq has permanent gaps where a deduped insert
    // burned a value, so counting would skip events.
    since = batch[batch.length - 1].seq;
    if (batch.length < PAGE) break;
  }
  return out;
}

export function useSharedReport(token: string | null): SharedReport {
  const enabled = isRemoteBusEnabled();
  // `null` = still asking, `"invalid"` = the backend refused. The two cases the
  // effect cannot answer — no token, no backend — are **derived** below rather
  // than written into state, because a setState in an effect body costs every
  // subscriber a second render pass and CI gates on the rule that says so.
  const [fetched, setFetched] = useState<
    { report: SessionReport; config: RemoteSessionConfig } | "invalid" | null
  >(null);

  useEffect(() => {
    if (!token || !enabled) return;

    let cancelled = false;

    void (async () => {
      const configRes = await fetch(
        `${busUrl()}/v1/share/${encodeURIComponent(token)}`
      ).catch(() => null);

      // Unknown, expired and revoked all arrive as 404 — the backend refuses to
      // distinguish them, so this cannot either, and the page says so in one
      // sentence rather than guessing which it was.
      if (cancelled) return;
      if (!configRes || !configRes.ok) {
        setFetched("invalid");
        return;
      }

      const config = (await configRes.json()) as RemoteSessionConfig;
      const events = await fetchAllEvents(token);
      if (cancelled) return;

      const zones: ZoneNode[] = config.zones.map((z) => ({
        id: z.id,
        name: z.name,
        kind: ZONE_TYPE_TO_KIND[z.type as ZoneType] ?? "other",
        weight: z.weight,
      }));

      const scorecard = computeScorecard(events, {
        zones,
        engagedThresholdSec: config.engagedThresholdSeconds,
        activationCost: config.activationCost ?? undefined,
        revenueInfluenced: config.revenueInfluenced ?? undefined,
        qualifiedLeads: config.qualifiedLeads ?? undefined,
      });

      const missing: string[] = [];
      if (!zones.some((z) => z.kind === "entry")) {
        missing.push(
          "No zone is marked as the entry, so footfall cannot be separated from zone traffic."
        );
      }
      if (config.activationCost == null) {
        missing.push(
          "No activation cost is set, so cost per engaged visit and the ROI ratio cannot be computed."
        );
      }

      setFetched({
        config,
        report: {
          status: events.length ? "ready" : "empty",
          scorecard,
          config,
          graph: null,
          events,
          missing,
        },
      });
    })();

    return () => {
      cancelled = true;
    };
  }, [token, enabled]);

  if (!token) return { status: "invalid", report: null, config: null };
  if (!enabled) return { status: "offline", report: null, config: null };
  if (fetched === null) return { status: "loading", report: null, config: null };
  if (fetched === "invalid") return { status: "invalid", report: null, config: null };
  return { status: "ready", report: fetched.report, config: fetched.config };
}
