"use client";

/**
 * realmspace — everything the report needs for one session, in one place.
 *
 * The report is the deliverable the product is sold on, so the rule it lives by
 * is `roi-framework.md` §3: every figure traces to an event or to a parameter
 * the operator set. Nothing is defaulted into looking like a finding.
 *
 * That forces a distinction the old page did not make. There are three ways a
 * report can have no numbers, and they mean completely different things:
 *
 *   - **loading**   — we have not looked yet
 *   - **unconfigured** — nobody told the system where the zones are, so nothing
 *                    could have been measured even if the room was full
 *   - **empty**     — configured correctly, and genuinely nobody came
 *
 * Rendering those three the same way is how a broken pipeline gets reported to
 * a client as a quiet day. `status` keeps them apart.
 */

import { useEffect, useState } from "react";

import {
  busEmail,
  backfillSession,
  ensureTenantId,
  isRemoteBusEnabled,
  readAll,
} from "@/lib/bus";
import {
  fetchSessionConfig,
  fetchSessionGraph,
  ZONE_TYPE_TO_KIND,
  type RemoteSessionConfig,
  type RemoteSessionGraph,
} from "@/lib/session/publish";
import { seedDemoSession } from "@/lib/mock/seed-demo";
import { computeScorecard, type Scorecard } from "@/lib/roi/scorecard";
import { getTenantId } from "@/lib/tenant/context";
import type { RealmEvent, ZoneNode } from "@/lib/contracts";
import type { Session, ZoneType } from "@/lib/session/types";

export type ReportStatus = "loading" | "ready" | "empty" | "unconfigured";

export interface SessionReport {
  status: ReportStatus;
  scorecard: Scorecard;
  /** Null when there is no backend, or the session was never published to it. */
  config: RemoteSessionConfig | null;
  graph: RemoteSessionGraph | null;
  events: RealmEvent[];
  /** Human-readable reasons a figure is absent. Rendered, never swallowed. */
  missing: string[];
}

/**
 * Zones for the scorecard. Prefers what the backend holds, because an operator
 * may have redrawn a zone or changed a weight from another machine and the
 * report must divide by what was actually agreed — not by whatever this browser
 * last cached.
 */
function zonesFor(
  config: RemoteSessionConfig | null,
  session: Session
): ZoneNode[] {
  const source = config
    ? config.zones.map((z) => ({
        id: z.id,
        name: z.name,
        type: z.type as ZoneType,
        weight: z.weight,
      }))
    : session.zones.map((z) => ({
        id: z.id,
        name: z.name,
        type: z.type,
        weight: z.weight ?? 1,
      }));

  return source.map((z) => ({
    id: z.id,
    name: z.name,
    kind: ZONE_TYPE_TO_KIND[z.type] ?? "other",
    weight: z.weight,
  }));
}

export function useSessionReport(session: Session): SessionReport {
  const [report, setReport] = useState<SessionReport>(() => ({
    status: "loading",
    scorecard: computeScorecard([]),
    config: null,
    graph: null,
    events: [],
    missing: [],
  }));

  useEffect(() => {
    let cancelled = false;

    async function load() {
      const email = busEmail();
      // Awaited, not read. `getTenantId()` returns a guess until a token
      // exchange has verified who this browser is, and reading it here — before
      // the backfill below establishes it — is what made this report say "the
      // log is genuinely empty" over an activation full of visitors: every
      // event failed the tenant comparison on the way in, and the partition
      // read afterwards was the wrong one.
      //
      // The demo path keeps the stored value, because there is no backend to
      // ask and no token to wait for.
      const tenantId = session.isDemo ? getTenantId() : await ensureTenantId(email);
      if (cancelled) return;

      let config: RemoteSessionConfig | null = null;
      let graph: RemoteSessionGraph | null = null;

      // The demo session's events are synthetic and stay on this machine. It
      // goes through the same log and the same scorecard as a real session —
      // what is invented is the input, never a figure on the report.
      if (session.isDemo) seedDemoSession(tenantId);

      if (isRemoteBusEnabled() && !session.isDemo) {
        // Pull the history first: the live socket only carries what arrived
        // while a browser was watching, and a report is usually read later, on
        // a different machine.
        await backfillSession(tenantId, session.id, email);
        [config, graph] = await Promise.all([
          fetchSessionConfig(session.id, email),
          fetchSessionGraph(session.id, email),
        ]);
      }

      if (cancelled) return;

      const events = readAll(tenantId, session.id) as RealmEvent[];
      const zones = zonesFor(config, session);
      const measurement = session.measurement ?? {};

      const scorecard = computeScorecard(events, {
        zones,
        engagedThresholdSec:
          config?.engagedThresholdSeconds ?? measurement.engagedThresholdSec,
        activationCost: config?.activationCost ?? measurement.activationCost,
        revenueInfluenced:
          config?.revenueInfluenced ?? measurement.revenueInfluenced,
        qualifiedLeads: config?.qualifiedLeads ?? measurement.qualifiedLeads,
      });

      const missing: string[] = [];
      if (session.isDemo) {
        missing.push(
          "This is the demo session. Its events are synthetic — the figures are computed from them exactly as they would be from a real activation."
        );
      }
      if (isRemoteBusEnabled() && !session.isDemo && !config) {
        missing.push(
          "This session was never published to the backend, so nothing was measured against its zones."
        );
      }
      if (!zones.length) {
        missing.push("No zones are configured, so there is nothing to attribute dwell to.");
      }
      if (!zones.some((z) => z.kind === "entry")) {
        missing.push(
          "No zone is marked as the entry, so footfall cannot be separated from zone traffic."
        );
      }
      if ((config?.activationCost ?? measurement.activationCost) == null) {
        missing.push(
          "No activation cost is set, so cost per engaged visit and the ROI ratio cannot be computed."
        );
      }
      if ((config?.revenueInfluenced ?? measurement.revenueInfluenced) == null) {
        missing.push(
          "No influenced revenue has been provided by the client. Attributed revenue arrives with the CRM phase."
        );
      }

      const status: ReportStatus = events.length
        ? "ready"
        : zones.length
          ? "empty"
          : "unconfigured";

      setReport({ status, scorecard, config, graph, events, missing });
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [session]);

  return report;
}
