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
    zones: session.zones.map(zoneToWire),
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
