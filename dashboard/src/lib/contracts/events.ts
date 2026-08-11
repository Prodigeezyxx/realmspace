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
  /** a badge correlated to a tracked person — a guess with a confidence, still anonymous */
  | "spatial.tagged"
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
  /** provisional — the scoring model is not pinned yet (event-bus-spec.md §3) */
  | "intent.scored"
  // attribution + outbound
  | "handoff.lead"
  /** tell the CRM to undo a push after consent withdrawal — carries PII */
  | "crm.retract"
  // ops
  | "cost.metered"
  /** the perception model's numbers moved against their baseline */
  | "drift.detected"
  /** an operator recalibrated a camera, its zone/reader map, or its privacy mask */
  | "calibration.updated"
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
  /**
   * A badge id and a track id, and no `Contact`. Anonymous for the same reason
   * `rfid.read` is: linking either to a person happens in the identity consumer,
   * behind the consent gate (docs/consent-and-identity.md).
   */
  "spatial.tagged",
  /** Derived from anonymous spatial signals; scores a track, not a person. */
  "intent.scored",
  "cost.metered",
  /** About a camera, not a visitor. */
  "drift.detected",
  "calibration.updated",
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
  /**
   * Carries `contactId`. It exists *because* consent was withdrawn, which makes
   * it tempting to file as an ops event — but the identifier is still in the
   * payload, so every PII rule applies to it: never on the anonymised cloud-sync
   * path (event-bus-spec.md §6).
   */
  "crm.retract",
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
export type ZoneExitReason = "move" | "dropout" | "session_end";

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
  /** The zone they came close to and did not enter. */
  adjacentZoneId?: string;
  /** Closest normalized distance to that zone's edge, over the whole session. */
  closestDist?: number;
  at?: string;
  /**
   * How the track ended when this was judged. Pass-by can only be decided at
   * close-out — until then, someone loitering outside a zone might still walk
   * in.
   */
  reason?: ZoneExitReason | "session_end";
}
export interface RfidReadPayload {
  readerId: string;
  /** The badge, not a person. Linking it to a Contact is consent-gated. */
  tagId: string;
  /** dBm. The fusion consumer's distance proxy. */
  rssi?: number;
  at?: string;
}
export interface TaggedPayload {
  anonId: string;
  tagId: string;
  readerId?: string;
  /** RFID fusion is a guess, so it ships with how good a guess it is. */
  confidence: number;
  /** How the correlation was made — tuning differs per venue geometry. */
  method?: "rssi_proximity" | string;
  at?: string;
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
/**
 * **Provisional** — no doc pins the scoring model yet (event-bus-spec.md §3).
 * Registered so P4's producer has a shape to write against; re-pinned when it
 * lands.
 */
export interface IntentScoredPayload {
  anonId: string;
  /** 0..1 */
  score: number;
  /** Stored, not recomputed: thresholds are per-tenant and change over time. */
  band: "cold" | "warm" | "hot";
  /** The features the score came from, kept so a disputed score can be explained. */
  signals?: Record<string, number>;
  /** Without it, a rescored replay is indistinguishable from changed data. */
  modelVersion?: string;
}
export interface CrmRetractPayload {
  /** PII — see PII_EVENT_TYPES. */
  contactId: string;
  destination: string; // 'hubspot' | 'salesforce' | 'webhook' | ...
  reason: "consent_withdrawn" | "erasure_request";
  /** Mirrors handoff.lead, so a retried retraction cannot fire twice. */
  dedupeKey: string;
}
export interface CostMeteredPayload {
  kind: "llm_tokens" | "enrichment_credit" | "storage" | "other";
  amount: number;
  unit: string;
}
export interface DriftDetectedPayload {
  cameraId: string;
  metric: "detection_rate" | "confidence_mean" | "track_length" | string;
  /** Both sides of the comparison, so the event states its case rather than a verdict. */
  observed: number;
  baseline: number;
  windowSeconds?: number;
  severity: "warn" | "critical";
}
export interface CalibrationUpdatedPayload {
  cameraId: string;
  /** `privacy_mask` is the opt-out polygon of docs/privacy.md. */
  kind: "homography" | "zone_map" | "reader_map" | "privacy_mask";
  revision: number;
  /** user id of the operator who recalibrated */
  by?: string;
  note?: string;
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
  | RfidReadPayload
  | TaggedPayload
  | SurfaceInteractionPayload
  | ConsentCapturedPayload
  | ConsentWithdrawnPayload
  | IdentityResolvedPayload
  | RuleFiredPayload
  | InsightGeneratedPayload
  | IntentScoredPayload
  | LeadHandoffPayload
  | CrmRetractPayload
  | CostMeteredPayload
  | DriftDetectedPayload
  | CalibrationUpdatedPayload
  | SessionLifecyclePayload
  | ZonesUpdatedPayload
  | Record<string, unknown>;
