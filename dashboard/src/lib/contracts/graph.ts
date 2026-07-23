/**
 * realmspace — canonical graph contract.
 *
 * The graph is the product (see docs/data-model.md). These types mirror the
 * node/edge schema so the mocked prototype and the future graph store agree on
 * one shape. Anonymous by default; PII (Contact) only exists after a consent
 * event (see docs/consent-and-identity.md).
 */

export type ZoneKind =
  | "entry"
  | "experience"
  | "product"
  | "lounge"
  | "exit"
  | "sponsor"
  | "other";

export interface PersonNode {
  /** session-scoped, never reused across sessions: `${sessionId}_P_${n}` */
  anonId: string;
  firstSeen: number;
  lastSeen: number;
  totalDwellSec: number;
  attentionScore: number; // 0..1
  /** VLM-derived, non-biometric, e.g. "person in dark coat". Never identifying. */
  appearanceSummary?: string;
  /** set only after a consent event links this person to a contact */
  contactId?: string;
}

export interface ZoneNode {
  id: string;
  name: string;
  kind: ZoneKind;
  polygon?: [number, number][];
  color?: string;
  capacity?: number;
  /** ROI weight for dwell-weighted attention (see docs/roi-framework.md). */
  weight?: number;
}

export interface SurfaceNode {
  id: string;
  label: string;
  kind: string; // 'ar'|'game'|'screen'|'rfid'|'scent'|'product'|'badge'|'qr'...
  zoneId?: string;
  triggerCount: number;
  active: boolean;
}

/** PII — exists only in consent-gated PII mode. */
export interface ContactNode {
  id: string;
  email?: string;
  name?: string;
  company?: string;
  title?: string;
  source: "badge" | "qr" | "kiosk" | "form" | "manual";
  createdAt: number;
}

export interface ConsentEventNode {
  id: string;
  tier: "T1" | "T2" | "T3";
  basis: "explicit_optin" | "contract" | "legitimate_interest";
  copyVersion: string;
  capturedAt: number;
  capturedBy: string;
  expiresAt?: number;
  withdrawnAt?: number;
}

export type EdgeKind =
  | "ENTERED"
  | "LEFT"
  | "DWELLED_IN"
  | "LOOKED_AT"
  | "INTERACTED_WITH"
  | "NEAR"
  | "GROUP_MEMBER_OF"
  | "LIVE_IN"
  | "IDENTIFIED_AS"
  | "GRANTED"
  | "PERMITS";

export interface GraphEdge {
  kind: EdgeKind;
  from: string;
  to: string;
  props?: Record<string, number | string | boolean>;
}

export interface GraphSnapshot {
  tenantId: string;
  sessionId: string;
  persons: PersonNode[];
  zones: ZoneNode[];
  surfaces: SurfaceNode[];
  contacts: ContactNode[];
  consents: ConsentEventNode[];
  edges: GraphEdge[];
}
