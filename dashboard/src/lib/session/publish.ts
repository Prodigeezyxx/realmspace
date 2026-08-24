/**
 * realmspace — publish a configured session to the backend.
 *
 * The wizard already collects everything the backend's `POST /v1/sessions`
 * wants: the zones with their polygons, and (now) the measurement parameters
 * the ROI report divides by. Until this file existed it collected them into
 * localStorage and stopped there — which is why a real deployment had no zones
 * and the tracker emitted nothing. This is the other half of that fix.
 *
 * ## Two vocabularies, reconciled here
 *
 * The wizard's `ZoneType` (`session/types.ts`) has 11 values — `reveal`,
 * `engagement`, `privacy_masked` and so on. The graph contract's `ZoneKind`
 * (`contracts/graph.ts`) has 7. They were written at different times and nobody
 * reconciled them. The backend deliberately accepts a free string rather than
 * pick a winner and reject a zone an operator can legitimately draw, so the
 * wizard's richer vocabulary is what gets stored — but the mapping below is
 * what the graph-shaped readers use, and it is written down rather than
 * left to each caller to guess.
 */

import { busUrl, ensureToken, isRemoteBusEnabled } from "@/lib/bus";
import type { Session, Zone, ZoneType } from "./types";
import type { ZoneKind } from "@/lib/contracts";

/** `ZoneType` (wizard, 11 values) → `ZoneKind` (graph contract, 7). */
export const ZONE_TYPE_TO_KIND: Record<ZoneType, ZoneKind> = {
  entry: "entry",
  exit: "exit",
  reveal: "experience",
  engagement: "experience",
  demo: "experience",
  retail: "product",
  lounge: "lounge",
  sponsor: "sponsor",
  press: "other",
  privacy_masked: "other",
  other: "other",
};

/**
 * A session's configuration as the backend holds it — the authority for how the
 * activation is scored.
 *
 * Read back rather than taken from this browser's own store, and that is the
 * point: an operator may have redrawn a zone or set the cost from another
 * machine, and the report must divide by what was actually agreed, not by
 * whatever this laptop last saw.
 */
export interface RemoteSessionConfig {
  sessionId: string;
  venue?: string | null;
  campaign?: string | null;
  engagedThresholdSeconds: number;
  activationCost: number | null;
  currency: string;
  attributionModel: string;
  /** Operator-supplied, never measured. Null means unknown, not zero. */
  revenueInfluenced: number | null;
  qualifiedLeads: number | null;
  zones: {
    id: string;
    name: string;
    type: string;
    weight: number;
    funnelOrder: number | null;
    polygon: [number, number][] | null;
    capacity: number | null;
  }[];
  touchpoints: {
    id: string;
    label: string;
    type: string;
    zoneId: string | null;
    active: boolean;
    /** Interactions recorded against it so far, maintained by the graph. */
    triggerCount: number;
  }[];
}

/**
 * Fetch a session's configuration. `null` distinguishes "no backend" and "never
 * configured" from a config that happens to be empty — the report needs to say
 * which, because a session nobody set up and a session nobody attended look
 * identical once the difference is thrown away.
 */
export async function fetchSessionConfig(
  sessionId: string,
  email: string
): Promise<RemoteSessionConfig | null> {
  if (!isRemoteBusEnabled()) return null;
  const token = await ensureToken(email);
  if (!token) return null;

  try {
    const res = await fetch(
      `${busUrl()}/v1/sessions/${encodeURIComponent(sessionId)}`,
      { headers: { Authorization: `Bearer ${token}` } }
    );
    if (!res.ok) return null; // 404 = never configured
    return (await res.json()) as RemoteSessionConfig;
  } catch {
    return null;
  }
}

/** Graph-derived aggregates for a session: unique people and dwell per zone. */
export interface RemoteSessionGraph {
  sessionId: string;
  uniquePeople: number;
  zones: RemoteSessionConfig["zones"];
  dwellByZone: {
    zoneId: string;
    zone: string | null;
    avgDwell: number | null;
    visitors: number;
  }[];
}

export async function fetchSessionGraph(
  sessionId: string,
  email: string
): Promise<RemoteSessionGraph | null> {
  if (!isRemoteBusEnabled()) return null;
  const token = await ensureToken(email);
  if (!token) return null;

  try {
    const res = await fetch(
      `${busUrl()}/v1/sessions/${encodeURIComponent(sessionId)}/graph`,
      { headers: { Authorization: `Bearer ${token}` } }
    );
    if (!res.ok) return null;
    return (await res.json()) as RemoteSessionGraph;
  } catch {
    return null;
  }
}

/**
 * One activation in the tenant's listing. Settings only — no measured figures.
 *
 * The backend deliberately returns no visitor count here: the four ROI layers
 * are defined once, in `lib/roi/scorecard.ts`, and a count arriving on this
 * shape would be a second definition of "unique visitor" that nothing would
 * notice diverging. The benchmark gets its numbers by running that scorecard
 * over each session's own log.
 */
export interface RemoteSessionSummary {
  sessionId: string;
  client: string | null;
  campaign: string | null;
  venue: string | null;
  city: string | null;
  startedAt: string | null;
  endsAt: string | null;
  engagedThresholdSeconds: number;
  activationCost: number | null;
  currency: string;
  revenueInfluenced: number | null;
  qualifiedLeads: number | null;
}

/**
 * Every activation this tenant has run, newest first.
 *
 * `null` for "no backend", an empty array for "this client has run nothing
 * yet" — the same distinction `fetchSessionConfig` keeps, and for the same
 * reason: a benchmark with no history to compare against has to say so rather
 * than render a comparison against zero.
 */
export async function fetchSessionList(
  email: string,
  limit = 50
): Promise<RemoteSessionSummary[] | null> {
  if (!isRemoteBusEnabled()) return null;
  const token = await ensureToken(email);
  if (!token) return null;

  try {
    const res = await fetch(`${busUrl()}/v1/sessions?limit=${limit}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) return null;
    return (await res.json()) as RemoteSessionSummary[];
  } catch {
    return null;
  }
}

export interface PublishResult {
  ok: boolean;
  /** Why not, when `ok` is false. Shown to the operator, not swallowed. */
  detail?: string;
  /** Zones the backend confirmed it stored. */
  zoneCount?: number;
}

function zoneToWire(zone: Zone, index: number) {
  return {
    id: zone.id,
    name: zone.name,
    type: zone.type,
    // Only zones the operator actually drew carry geometry. The backend keeps
    // the undrawn ones (they are still part of the funnel) but the tracker
    // skips them, since a zone with no boundary contains nobody.
    polygon: zone.polygon ?? null,
    color: zone.color ?? null,
    capacity: zone.capacity ?? null,
    weight: zone.weight ?? 1,
    // Falls back to the order the operator arranged them in. A funnel with no
    // explicit ordering is still a funnel — entry first is the common case and
    // the wizard's list order already expresses it.
    funnelOrder: zone.funnelOrder ?? index,
  };
}

export function sessionToWire(session: Session) {
  const m = session.measurement ?? {};
  return {
    sessionId: session.id,
    client: session.client ?? null,
    campaign: session.name,
    venue: session.venue,
    city: session.city ?? null,
    startedAt: session.startedAt ?? session.startAt,
    endsAt: session.endAt ?? null,
    boothWidthM: session.boothSize?.width ?? null,
    boothDepthM: session.boothSize?.depth ?? null,
    cameraCount: session.cameras.length,
    engagedThresholdSeconds: m.engagedThresholdSec ?? 60,
    activationCost: m.activationCost ?? null,
    currency: m.currency ?? "USD",
    attributionModel: m.attributionModel ?? "influenced",
    revenueInfluenced: m.revenueInfluenced ?? null,
    qualifiedLeads: m.qualifiedLeads ?? null,
    zones: session.zones.map(zoneToWire),
    // Touchpoints become Surface nodes. Without them the graph writer drops
    // every interaction a real kiosk sends, because it refuses to invent a
    // surface nobody configured — the same hole zones had before the session
    // API existed.
    touchpoints: session.touchpoints.map((t) => ({
      id: t.id,
      label: t.name,
      type: t.type,
      zoneId: t.zoneId ?? null,
      active: true,
    })),
  };
}

/**
 * Send a session's configuration to the backend.
 *
 * Returns a result rather than throwing, and never silently succeeds: the
 * caller shows the operator what happened. A session that failed to publish
 * looks completely normal in this app — the zones are on screen, the wizard
 * said "launched" — while the backend has none of them and the tracker is
 * emitting nothing. That gap has to be visible.
 *
 * A no-op when no backend is configured, which is the demo-on-a-laptop case.
 */
export async function publishSessionConfig(
  session: Session,
  email: string
): Promise<PublishResult> {
  if (!isRemoteBusEnabled()) {
    return { ok: true, detail: "no backend configured — local only" };
  }

  const token = await ensureToken(email);
  if (!token) return { ok: false, detail: "could not authenticate with the bus" };

  let res: Response;
  try {
    res = await fetch(`${busUrl()}/v1/sessions`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(sessionToWire(session)),
    });
  } catch {
    return { ok: false, detail: "the bus is unreachable" };
  }

  if (res.status === 403) {
    return {
      ok: false,
      detail: "your role cannot configure a session (needs admin or operator)",
    };
  }
  if (!res.ok) {
    // 422 carries the specific reason — a pixel polygon, a two-point zone, a
    // duplicate id. Passing it through matters: these are operator mistakes
    // with a fix, not internal errors.
    let reason = `bus rejected the session (${res.status})`;
    try {
      const body = await res.json();
      if (body?.detail) reason = JSON.stringify(body.detail);
    } catch {
      /* keep the status-code message */
    }
    return { ok: false, detail: reason };
  }

  const body = (await res.json()) as { zones?: unknown[] };
  return { ok: true, zoneCount: body.zones?.length ?? 0 };
}
