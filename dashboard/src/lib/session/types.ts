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
  zones: Zone[];
  touchpoints: Touchpoint[];

  // ── Goals + privacy
  goals: Goals;
  privacy: Privacy;

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
