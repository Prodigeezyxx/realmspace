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
  /**
   * A zone reached the capacity its operator set, or dropped back below it.
   * One event per crossing, not per arrival — and the only `spatial.*` type
   * that names no visitor, because it is a statement about a room.
   */
  | "spatial.occupancy"
  /** a badge correlated to a tracked person — a guess with a confidence, still anonymous */
  | "spatial.tagged"
  // booth surfaces
  /** a tablet's button was pressed. No person: a tablet has no camera. */
  | "surface.touched"
  | "surface.interaction"
  // badge / RFID readers (docs/event-bus-spec.md §3)
  | "rfid.read"
  // consent + identity (PII, consent-gated)
  /**
   * A consent kiosk's raw fact: this person agreed, to this wording, at this
   * plinth. **No person id** — a kiosk has no camera, exactly as a tablet has
   * none for `surface.touched`. Which visitor gave it is the backend consumer's
   * question, answered from zone occupancy, and its answer is the
   * `consent.captured` below.
   */
  | "consent.given"
  | "consent.captured"
  | "consent.withdrawn"
  | "identity.resolved"
  // rules + intelligence
  | "rule.fired"
  /**
   * A rule's action, carried out. Distinct types rather than the browser reading
   * `rule.fired` and checking what its action happens to be — that would put a
   * second implementation of the rule spec in the client, which is the
   * split-brain ADR-002 exists to end. A screen subscribes to the one type it
   * renders and knows nothing about rules.
   */
  | "rule.staff_prompt"
  | "rule.screen_swap"
  | "insight.generated"
  /** provisional — the scoring model is not pinned yet (event-bus-spec.md §3) */
  | "intent.scored"
  // attribution + outbound
  | "handoff.lead"
  /** what a lead turned into — the other end of the attribution ledger */
  | "outcome.recorded"
  /** tell the CRM to undo a push after consent withdrawal — carries PII */
  | "crm.retract"
  /**
   * GDPR Art. 17. Its own namespace rather than a `consent.` type, because an
   * erasure is not a consent decision: it outranks one, it is authorised by an
   * admin rather than given by the visitor, and it is the only thing in the
   * system that rewrites the append-only log.
   */
  | "erasure.requested"
  /** the receipt: ids and counts, never what was removed */
  | "erasure.completed"
  /** Retention enforcing itself — `gtm.md`'s "30-day data retention", meant. */
  | "retention.purge_requested"
  | "retention.purged"
  /**
   * A path-aware follow-up the contextual SDR wrote and nobody has sent. Its own
   * namespace rather than an `insight.` type: an insight is about the room, a
   * draft is about one person who agreed to be contacted, and the two want
   * different handling everywhere PII is handled.
   */
  | "followup.drafted"
  // ops
  | "cost.metered"
  /** the perception model's numbers moved against their baseline */
  | "drift.detected"
  /** an operator recalibrated a camera, its zone/reader map, or its privacy mask */
  | "calibration.updated"
  | "session.started"
  | "session.ended"
  /** an operator changed a session's zones or measurement parameters */
  | "session.zones_updated"
  /**
   * An operator moved one of the rules the ROI is scored by — the engagement
   * threshold, the attribution model, its window — on an activation that had
   * already measured something. `roi-framework.md` §5 asks for those to be
   * agreed before doors open; this is what lets the report say one moved
   * afterwards instead of the protection living in a document.
   */
  | "session.config_updated";

/** All spatial/anonymous event types (safe for aggregate ROI, no consent needed). */
export const ANONYMOUS_EVENT_TYPES: readonly RealmEventType[] = [
  "perception.detection",
  "spatial.zone_enter",
  "spatial.zone_exit",
  "spatial.dwell",
  "spatial.gaze",
  "spatial.group",
  "spatial.passby",
  /**
   * A zone, a count and a threshold. No visitor is named — see its payload —
   * which is what keeps a crowding alert on the anonymous side of `privacy.md`
   * beside `rule.staff_prompt`, the thing it usually raises.
   */
  "spatial.occupancy",
  "surface.touched",
  "surface.interaction",
  "rule.fired",
  /**
   * A message to staff and a content id for a screen. Neither names a visitor —
   * a prompt is about a zone and a moment, which is what keeps the Next-Step
   * surface on the anonymous side of `privacy.md` rather than needing consent.
   */
  "rule.staff_prompt",
  "rule.screen_swap",
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
  /** Field names and numbers an operator set. It names nobody. */
  "session.config_updated",
  /**
   * Retention enforcing itself. The request names the admin who asked, and the
   * receipt carries a floor, a seq and two counts — no visitor is named by
   * either, which is deliberate: the receipt lands on the same log the purge
   * just emptied, so quoting what was removed would put it straight back. Same
   * argument `erasure.completed` makes about itself.
   */
  "retention.purge_requested",
  "retention.purged",
] as const;

/** Event types that may carry PII and therefore require a consent basis. */
export const PII_EVENT_TYPES: readonly RealmEventType[] = [
  /**
   * Carries the `contact` a visitor typed into the kiosk, and for a consent no
   * zone could attribute it is the *only* place that contact appears — so it is
   * classified here as well as in `backend/app/erasure.py`'s `PII_TYPES`.
   */
  "consent.given",
  "consent.captured",
  "consent.withdrawn",
  "identity.resolved",
  "handoff.lead",
  /**
   * Its `dedupeKey` embeds an email, so an identifier that resolves to a person
   * travels in it — PII by §6's own definition, which an opaque-looking string
   * does not escape.
   */
  "outcome.recorded",
  /**
   * Carries `contactId`. It exists *because* consent was withdrawn, which makes
   * it tempting to file as an ops event — but the identifier is still in the
   * payload, so every PII rule applies to it: never on the anonymised cloud-sync
   * path (event-bus-spec.md §6).
   */
  "crm.retract",
  /**
   * Names a contact, and names the person who asked. PII on both counts, and it
   * is the request rather than the receipt — `erasure.completed` deliberately
   * carries only ids and counts, because a receipt that quoted what it removed
   * would put it straight back on the log it just rewrote.
   */
  "erasure.requested",
  /**
   * It names the contact and then quotes them: the body says "Hi Sam". Redacting
   * the contact object and leaving the letter would not be redaction, which is
   * why the backend's erasure clears the body too.
   */
  "followup.drafted",
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
  /**
   * Which camera saw this, when the booth has more than one.
   *
   * Consumers key a person on `cameraId/anonId`, not on `anonId` alone:
   * ByteTrack numbers people per process and one process runs per camera, so
   * every camera calls its first visitor `P-001`. Absent means a session with
   * one camera or none — the backend refuses an unattributed detection on a
   * session that declares two, rather than merging two people.
   */
  cameraId?: string;
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
  /** Only when every member is in the same one — see `consumers/grouping.py`. */
  zoneId?: string;
  size: number;
  /** 0..1, how consistently the members were observed together. */
  cohesion: number;
  /**
   * What this event says about the group it names. One type rather than three,
   * the same shape as the `reason` the tracker puts on a visit ending.
   */
  status: "formed" | "changed" | "dissolved";
}
export interface OccupancyPayload {
  zoneId: string;
  /** As the operator named it. A staff prompt saying `z_entry` needs translating. */
  zoneName?: string;
  /** How many people were inside at the transition that crossed the line. */
  occupancy: number;
  /**
   * The number that was crossed, carried rather than looked up: a rule that
   * fired, or a report rendered next year, shows the threshold that was applied
   * and not the one configured since.
   */
  capacity: number;
  /** Reached or passed the capacity, or dropped back below it. */
  status: "over" | "cleared";
  at?: string;
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
/**
 * A tap on a touchpoint, as the tablet that recorded it knows about it.
 *
 * **No `anonId`, and the absence is the design.** A tablet has no camera and
 * cannot know who pressed it; naming the visitor is the backend's job
 * (`consumers/touch.py`, from zone occupancy), and it refuses more often than it
 * succeeds. Every tap counts as an interaction; only an attributed one counts a
 * *person* as engaged.
 *
 * `surfaceId` is written by the backend off the token row and never from the
 * tablet's request, so one tablet cannot post as another touchpoint.
 */
export interface SurfaceTouchedPayload {
  surfaceId: string;
  kind: string;
  /** Minted when the finger lands, so a retry over venue wifi is one tap. */
  touchId?: string;
  at?: string;
}
export interface SurfaceInteractionPayload {
  anonId: string;
  surfaceId: string;
  kind: string;
  durationSec?: number;
  /** The tap this explains, when it came from a tablet rather than hardware. */
  touchId?: string;
  /** How the person was decided. Only "zone_occupancy" exists today. */
  attributedBy?: string;
  zoneId?: string;
}
/** Where a consent was taken. Mirrors `Contact.source` in consent-and-identity.md §3. */
export type ConsentSource = "badge" | "qr" | "kiosk" | "form" | "manual";

/** The PII half of a capture, and the only PII in the payload. */
export interface CapturedContact {
  email?: string;
  name?: string;
  company?: string;
  title?: string;
}

export interface ConsentCapturedPayload {
  consentId: string;
  /**
   * The track this consent is about — **not** a contactId. At capture time
   * there is no Contact yet: creating one is the identity consumer's job and it
   * happens after the gate is checked, never as part of asking permission.
   */
  anonId: string;
  tier: "T1" | "T2" | "T3";
  basis: "explicit_optin" | "contract" | "legitimate_interest";
  /**
   * The exact wording shown, versioned. Required, and the load-bearing field:
   * the tier says what somebody was asked for, this says what they read before
   * agreeing, and it is the only thing that settles a withdrawal argued later.
   */
  copyVersion: string;
  /** The surface or operator that captured it. */
  capturedBy: string;
  source: ConsentSource;
  capturedAt?: string;
  /** Null/absent = the end of the activation (consent-and-identity.md §3). */
  expiresAt?: string | null;
  /**
   * Optional: a badge scan may carry only an `anonId`, with the details
   * arriving from the registry later. Everything outside this object is
   * anonymous, which is what lets a deployment keep the consent record after
   * erasing the person.
   */
  contact?: CapturedContact;
}

export interface ConsentWithdrawnPayload {
  consentId?: string;
  contactId?: string;
  anonId?: string;
  reason: "visitor_request" | "erasure_request" | "operator";
  withdrawnAt?: string;
}

export interface IdentityResolvedPayload {
  anonId: string;
  contactId: string;
  /** Carried so a CRM adapter can re-check the justification it is acting on. */
  consentId: string;
  tier: "T1" | "T2" | "T3";
  via: ConsentSource;
  at?: string;
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
/**
 * The part of a lead no CRM could have known — `integrations.md` §2 calls it
 * "the realmspace differentiator".
 */
export interface SpatialIntent {
  /** In the order first entered, de-duplicated. A route, not a set. */
  zonesVisited: string[];
  topDwellZone: string | null;
  dwellSecondsTotal: number;
  surfacesEngaged: string[];
  /**
   * `Σ(dwell × zone weight)` per roi-framework.md §2 — a quantity in seconds,
   * not a 0–1 ratio. `attentionBasis` names the unit because the number alone
   * does not, and there is no denominator in the framework that would normalise
   * it without inventing one.
   */
  attentionScore: number;
  attentionBasis: string;
  funnelDepthReached: number | null;
  /** Generated from the rows above, never written by a model. */
  pathSummary: string;
  /**
   * Null when nothing measurable was configured — never 0. "We cannot score
   * this lead" and "this is a bad lead" are different statements, and a CRM
   * sorting by score would treat them identically.
   */
  leadScore: number | null;
  /** e.g. `spatial/v1`. A score in a CRM outlives the formula that made it. */
  leadScoreBasis: string;
  /** Which components actually contributed; the rest were not configured. */
  leadScoreComponents: string[];
  surfacesAvailable: number | null;
  maxFunnelOrder: number | null;
}

/** normalized LeadHandoff/v1 — pinned in docs/event-bus-spec.md §3. */
export interface LeadHandoffPayload {
  schema: "realmspace.lead_handoff/v1";
  /**
   * `identified` goes out while the visitor is still on the floor and carries
   * the path so far; `final` goes out at session end with the complete one.
   * Both share a `dedupeKey` so a destination upserts one lead.
   */
  stage: "identified" | "final";
  tenantId: string;
  activation: {
    id: string;
    name: string | null;
    venue: string | null;
    city: string | null;
    startedAt: string | null;
    endsAt: string | null;
  };
  /** PII, and present only behind a live consent — see PII_EVENT_TYPES. */
  contact: {
    id: string;
    email: string | null;
    name: string | null;
    company: string | null;
    title: string | null;
    source: string | null;
  };
  spatialIntent: SpatialIntent;
  /** Required for any PII emission (integrations.md §2). */
  consent: {
    tier: "T1" | "T2" | "T3" | null;
    basis: string | null;
    copyVersion: string | null;
    capturedAt: string | null;
  };
  roiContext: {
    attributionModel: string;
    attributionWindowDays: number;
    /**
     * Null until the session ends: the denominator is how many leads the
     * activation produced, which is unknowable while the doors are open.
     */
    activationCostShare: number | null;
  };
  /** `tenant:email|anonId` — every adapter's upsert key. */
  dedupeKey: string;
  anonId: string;
  emittedAt: string;
  eventSeq: number;
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
/** What a lead turned into — pinned in docs/event-bus-spec.md §3. */
export interface OutcomeRecordedPayload {
  outcomeId: string;
  /** The join back to the booth touch, and every adapter's upsert key. */
  dedupeKey: string;
  /** `open` is a real answer, not a missing one. */
  stage: "won" | "lost" | "open";
  /** Null when we were not told. Never 0 — those are different statements. */
  value: number | null;
  currency: string;
  /** Required for won/lost: the attribution window is measured against it. */
  closedAt: string | null;
  source: "operator" | "crm";
  externalRef: string | null;
  /** Who said so — part of what an auditor reads the ledger to find out. */
  recordedBy: string | null;
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
  /**
   * A closed set, unlike the event taxonomy: a cost kind nothing recognises
   * cannot be summed into unit economics, and would sit in the log looking as
   * though it had been counted.
   *
   * `action_unit` is one dispatched rule action — a Slack post, a webhook, a
   * screen swap. Added when the Phase 3 dispatchers landed, and named rather
   * than folded into `other` because "actions" is one of the two spends the
   * roadmap's cost line calls out, and a `/live` tile that labels it `other`
   * tells an operator nothing about what their booth is spending on.
   */
  kind:
    | "llm_tokens"
    | "action_unit"
    | "enrichment_credit"
    | "storage"
    | "other";
  amount: number;
  unit: string;
  /**
   * What spent it, beyond the kind. Written by the backend since the meter's
   * first caller and undeclared here until 2026-08-31, which meant the cost
   * tile was reading a field this contract did not admit existed.
   *
   * `spender` is the one worth naming: three different things make `llm_tokens`
   * calls — an operator asking a question, the timer-driven insight consumer,
   * and the SDR drafting a follow-up — and "what is the timer costing us" is a
   * different question from "what are operators asking". Open-ended otherwise,
   * because a detail is context for a human reading `/ops`, not something
   * summed.
   */
  detail?: {
    spender?: "ask" | "insight" | "sdr";
    provider?: string;
    query?: string;
    [key: string]: unknown;
  };
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
  | OccupancyPayload
  | RfidReadPayload
  | TaggedPayload
  | SurfaceTouchedPayload
  | SurfaceInteractionPayload
  | ConsentCapturedPayload
  | ConsentWithdrawnPayload
  | IdentityResolvedPayload
  | RuleFiredPayload
  | InsightGeneratedPayload
  | IntentScoredPayload
  | LeadHandoffPayload
  | OutcomeRecordedPayload
  | CrmRetractPayload
  | CostMeteredPayload
  | DriftDetectedPayload
  | CalibrationUpdatedPayload
  | SessionLifecyclePayload
  | ZonesUpdatedPayload
  | Record<string, unknown>;
