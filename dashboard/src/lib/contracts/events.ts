/**
 * realmspace — canonical event contract.
 *
 * This is the single source of truth for the shape of an event as it lives on
 * the append-only bus (see docs/event-bus-spec.md). Both the mocked prototype
 * and the future FastAPI + Postgres backend conform to THIS type — the storage
 * layer can be swapped without changing any caller.
 *
 * An event is immutable once written. `seq` gives total order per
 * (tenant, session); `eventId` gives idempotency (re-appending a duplicate is a
 * no-op). Frames/video NEVER enter an event — only structured data.
 */

/**
 * Event taxonomy (v1) — namespaced, additive-only.
 * Mirrors docs/event-bus-spec.md §3.
 */
export type RealmEventType =
  // perception + spatial (anonymous)
  | "perception.detection"
  | "spatial.zone_enter"
  | "spatial.zone_exit"
  | "spatial.dwell"
  | "spatial.gaze"
  | "spatial.group"
  | "spatial.passby"
  // booth surfaces
  | "surface.interaction"
  // badge / RFID readers (docs/event-bus-spec.md §3)
  | "rfid.read"
  // consent + identity (PII, consent-gated)
  | "consent.captured"
  | "consent.withdrawn"
  | "identity.resolved"
  // rules + intelligence
  | "rule.fired"
  | "insight.generated"
  // attribution + outbound
  | "handoff.lead"
  // ops
  | "cost.metered"
  | "session.started"
  | "session.ended"
  /** an operator changed a session's zones or measurement parameters */
  | "session.zones_updated";

/** All spatial/anonymous event types (safe for aggregate ROI, no consent needed). */
export const ANONYMOUS_EVENT_TYPES: readonly RealmEventType[] = [
  "perception.detection",
  "spatial.zone_enter",
  "spatial.zone_exit",
  "spatial.dwell",
  "spatial.gaze",
  "spatial.group",
  "spatial.passby",
  "surface.interaction",
  "rule.fired",
  "insight.generated",
  "rfid.read",
  "cost.metered",
  "session.started",
  "session.ended",
  "session.zones_updated",
] as const;

/** Event types that may carry PII and therefore require a consent basis. */
export const PII_EVENT_TYPES: readonly RealmEventType[] = [
  "consent.captured",
  "consent.withdrawn",
  "identity.resolved",
  "handoff.lead",
] as const;

export function isPiiEventType(t: RealmEventType): boolean {
  return PII_EVENT_TYPES.includes(t);
}

/**
 * The canonical event-log row. `seq` and `recordedAt` are assigned by the bus
 * on append; producers supply everything else.
 */
export interface RealmEvent<P = RealmEventPayload> {
  /** Monotonic order within (tenantId, sessionId). Assigned by the bus. */
  seq: number;
  /** Producer-assigned unique id; the idempotency/dedupe key. */
  eventId: string;
  tenantId: string;
  sessionId: string;
  type: RealmEventType;
  payload: P;
  /** When it happened, on the producing (edge) clock. */
  occurredAt: number; // ms epoch
  /** When the bus durably recorded it. Assigned by the bus. */
  recordedAt: number; // ms epoch
}

/** Producer-facing input: the bus fills in seq/recordedAt; eventId/occurredAt optional. */
export type RealmEventInput<P = RealmEventPayload> = Omit<
  RealmEvent<P>,
  "seq" | "recordedAt" | "eventId" | "occurredAt"
> & { eventId?: string; occurredAt?: number };

/* ─────────────────────────── payload shapes ─────────────────────────── */

export interface DetectionPayload {
  anonId: string;
  /** PIXELS, xyxy — what YOLO returns. Normalized against the frame size below. */
  bbox: [number, number, number, number];
  confidence: number;
  frameId?: number;
  /**
   * **Required by the backend tracker**, which dead-letters a detection without
   * them: boxes arrive in pixels and zone polygons are normalized 0..1, so
   * without the frame size there is no way to say which zone a detection is in.
   * Optional in this type only because the browser's own in-memory tracker
   * predates the backend; anything posted to the bus must set them.
   */
  frameWidth?: number;
  frameHeight?: number;
}
/**
 * Why a stay ended.
 *
 * `dropout` means the track stopped being detected inside the zone and the
 * visit was closed at its last sighting — so the duration is a **lower bound**
 * on the real one, not a measurement of it. Anything averaging or ranking
 * dwell should be able to say which it is looking at.
 */
export type ZoneExitReason = "move" | "dropout";

export interface ZoneMovePayload {
  anonId: string;
  zoneId: string;
  /** ISO timestamp of the crossing, as the tracker records it. */
  at?: string;
  /** zone_exit only — when this visit to the zone began. */
  enteredAt?: string;
  /** zone_exit only. */
  reason?: ZoneExitReason;
}
export interface DwellPayload {
  anonId: string;
  zoneId: string;
  /** Seconds. On the wire the backend calls this `duration` — see lib/bus/wire.ts. */
  durationSec: number;
  startedAt?: string;
  endedAt?: string;
  /** Past the session's configured engagement threshold. */
  exceededThreshold?: boolean;
  /** `dropout` means this duration is a lower bound — see ZoneExitReason. */
  reason?: ZoneExitReason;
}
export interface ZonesUpdatedPayload {
  zoneIds: string[];
  zoneCount: number;
  /** user id of the operator who made the change */
  by?: string;
}
export interface GazePayload {
  anonId: string;
  targetId: string; // object or surface id
  durationSec: number;
  confidence?: number;
}
export interface GroupPayload {
  groupId: string;
  memberAnonIds: string[];
  zoneId?: string;
}
export interface PassbyPayload {
  anonId: string;
  adjacentZoneId?: string;
}
export interface SurfaceInteractionPayload {
  anonId: string;
  surfaceId: string;
  kind: string;
  durationSec?: number;
}
export interface ConsentCapturedPayload {
  anonId: string;
  contactId: string;
  tier: "T1" | "T2" | "T3";
  basis: "explicit_optin" | "contract" | "legitimate_interest";
  copyVersion: string;
  capturedBy: string;
}
export interface ConsentWithdrawnPayload {
  contactId: string;
}
export interface IdentityResolvedPayload {
  anonId: string;
  contactId: string;
  via: "badge" | "qr" | "kiosk" | "form" | "manual";
}
export interface RuleFiredPayload {
  ruleId: string;
  action: string;
  detail?: Record<string, unknown>;
}
export interface InsightGeneratedPayload {
  text: string;
  generatedBy: "llm" | "rule" | "manual";
  refs?: string[];
}
export interface LeadHandoffPayload {
  // normalized LeadHandoff/v1 — see docs/integrations.md §2
  contactId?: string;
  destination: string; // 'hubspot' | 'salesforce' | 'webhook' | ...
  dedupeKey: string;
  spatialIntent?: Record<string, unknown>;
  consentTier?: "T1" | "T2" | "T3";
}
export interface CostMeteredPayload {
  kind: "llm_tokens" | "enrichment_credit" | "storage" | "other";
  amount: number;
  unit: string;
}
export interface SessionLifecyclePayload {
  name?: string;
  venue?: string;
}

export type RealmEventPayload =
  | DetectionPayload
  | ZoneMovePayload
  | DwellPayload
  | GazePayload
  | GroupPayload
  | PassbyPayload
  | SurfaceInteractionPayload
  | ConsentCapturedPayload
  | ConsentWithdrawnPayload
  | IdentityResolvedPayload
  | RuleFiredPayload
  | InsightGeneratedPayload
  | LeadHandoffPayload
  | CostMeteredPayload
  | SessionLifecyclePayload
  | ZonesUpdatedPayload
  | Record<string, unknown>;
