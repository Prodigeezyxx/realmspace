/**
 * Session data model — the canonical shape for a "what we're mapping".
 *
 * A Session is a single event/experience instance: a brand activation, an
 * exhibition, a conference, etc. The system can hold many sessions
 * simultaneously; one of them is the "active" session that the dashboard
 * surfaces (Live, Twin, Ask, Agents, Report) operate on.
 */

export type SessionType =
  | "brand_activation"
  | "exhibition"
  | "conference"
  | "trade_show"
  | "experience_centre"
  | "retail_popup"
  | "product_launch"
  | "workshop"
  | "press_event"
  | "private_event"
  | "custom";

export type SessionStatus =
  | "draft"        // wizard incomplete (not really used today, kept for future)
  | "scheduled"    // saved, hasn't started yet
  | "live"         // running right now
  | "paused"       // temporarily stopped
  | "completed";   // ended, report locked

export type ZoneType =
  | "entry"
  | "reveal"
  | "engagement"
  | "lounge"
  | "retail"
  | "sponsor"
  | "demo"
  | "press"
  | "exit"
  | "privacy_masked"
  | "other";

export type TouchpointType =
  | "screen"
  | "rfid"
  | "quiz"
  | "game"
  | "photo_booth"
  | "scent_station"
  | "product_display"
  | "ar_mirror"
  | "configurator"
  | "demo_unit"
  | "voice"
  | "wayfinding"
  | "lead_form"
  | "badge_scan"
  | "audio_guide"
  | "other";

export type PrivacyMode = "default" | "strict" | "open";

export type PrimaryObjective =
  | "brand_awareness"
  | "lead_capture"
  | "product_education"
  | "vip_engagement"
  | "sales_conversion"
  | "press_coverage"
  | "research"
  | "loyalty";

export interface Zone {
  id: string;
  name: string;
  type: ZoneType;
  purpose?: string;
  capacity?: number;
  privacyMasked?: boolean;
  /** Hex colour used in the twin + heatmap. Optional — auto-assigned. */
  color?: string;
  /** Optional polygon in normalized 0..1 booth coords (demo session only). */
  polygon?: [number, number][];
  /**
   * ROI weight, default 1. Dwell-weighted attention is `Σ(dwell × weight)` —
   * a minute at the product wall is not a minute in the corridor
   * (docs/roi-framework.md §2, Layer 2).
   */
  weight?: number;
  /** Position in the entry → experience → product → capture funnel (§5). */
  funnelOrder?: number;
}

export interface Touchpoint {
  id: string;
  name: string;
  type: TouchpointType;
  zoneId?: string;
  sponsor?: string;
  /** Which graph events this touchpoint emits. */
  triggers?: string[];
}

export interface Camera {
  id: string;
  name: string;
  placement?: string;
  device?: string;
}

export interface Goals {
  primaryObjective?: PrimaryObjective;
  targetVisitors?: number;
  targetDwellSec?: number;
  targetCaptures?: number;
  notes?: string;
}

export interface Privacy {
  mode: PrivacyMode;
  consentSignage: boolean;
  retentionDays: number;
  recipients?: string[];
  /**
   * The exact wording a consent kiosk shows, and the version recorded with
   * every consent given through it.
   *
   * The version is the load-bearing half, not the tier
   * (`docs/event-bus-spec.md` §3): the tier says what somebody was asked for,
   * this says what they read before agreeing, and it is the only thing that
   * settles a withdrawal argued after the fact. A kiosk cannot be minted until
   * both are set — the backend refuses with that sentence.
   */
  consentCopy?: string;
  consentCopyVersion?: string;
  /**
   * What the kiosk asks for. **T2 by default**: below it the CRM gate refuses
   * every delivery, so a kiosk set to T1 would collect consents all day and
   * send nothing — the surface failing quietly, which is the failure mode this
   * repo keeps designing against.
   */
  consentTier?: "T1" | "T2" | "T3";
}

/**
 * How this activation will be scored — agreed with the client *before* it runs.
 *
 * docs/roi-framework.md §5: "set the attribution model + window with the client
 * before doors open, so the ROI number is pre-agreed and un-arguable
 * afterwards". Every Layer-4 metric divides by something in here, so a value
 * chosen after the results are in is a value chosen to flatter them.
 */
export interface Measurement {
  /** Dwell above this counts as an engaged visit. Default 60s. */
  engagedThresholdSec?: number;
  /** Total cost of the activation — denominator of CPEV, CPQL and ROI. */
  activationCost?: number;
  /** 3-letter currency code for `activationCost`. */
  currency?: string;
  attributionModel?:
    | "first_touch"
    | "last_touch"
    | "linear"
    | "time_decay"
    | "influenced";
  /**
   * **The client's own figures, typed in — not measured by realmspace.**
   *
   * Influenced revenue comes from CRM attribution, which is Phase 4. Until then
   * the only honest options are to take the client's number or to show nothing,
   * so these stay optional and everything that renders them says where they came
   * from. Left unset, the ROI ratio reports as unknown rather than as zero.
   */
  revenueInfluenced?: number;
  qualifiedLeads?: number;
}

export interface Session {
  id: string;
  /** True for the seeded demo (Lagos Showroom). UI shows a special label. */
  isDemo?: boolean;
  status: SessionStatus;

  // ── Basics
  name: string;
  type: SessionType;
  brand?: string;
  client?: string;
  agency?: string;

  // ── Where & when
  venue: string;
  address?: string;
  city?: string;
  timezone?: string;
  startAt: string; // ISO 8601
  endAt?: string;  // ISO 8601 — empty/undefined for open-ended
  expectedDailyFootfall?: number;

  // ── Setup
  cameras: Camera[];
  /** Venue prefab chosen during onboarding (zones + booth footprint). */
  prefabId?: string;
  boothSize?: { width: number; depth: number };
  zones: Zone[];
  touchpoints: Touchpoint[];

  // ── Goals + privacy
  goals: Goals;
  privacy: Privacy;
  /** How the ROI is computed. See `Measurement`. */
  measurement?: Measurement;
  /**
   * The operator's own read of the activation, written after it ran.
   *
   * Deliberately separate from `notes`, which is the pre-event brief collected
   * in the wizard. This appears on the client report as commentary, visually
   * distinct from every computed figure, because a human judgement and a
   * measurement should never be presented as the same kind of claim.
   */
  reportNote?: string;

  // ── System
  createdAt: string;
  startedAt?: string; // when status moved → live
  endedAt?: string;   // when status moved → completed
  notes?: string;
}

/** Convenience union for the wizard payload (everything except system fields). */
export type SessionDraft = Omit<
  Session,
  "id" | "createdAt" | "startedAt" | "endedAt" | "status"
>;
